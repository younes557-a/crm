"""Liaison avec une boîte mail : import IMAP des échanges et envoi SMTP.

Chaque e-mail reçu ou envoyé à une adresse connue devient une interaction
« E-mail » sur la fiche du contact correspondant (sans doublon grâce au
Message-ID). Les messages ne sont pas marqués comme lus sur le serveur.
"""
import email
import html
import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import getaddresses, make_msgid, parsedate_to_datetime

from sqlalchemy import func

from .. import db
from ..models import Contact, Interaction, Setting
from . import sentiment
from .matching import best_match, find_contacts

MAIL_SETTINGS = {
    "mail_imap_host": "",
    "mail_imap_port": "993",
    "mail_username": "",
    "mail_password": "",
    "mail_inbox_folder": "INBOX",
    "mail_sent_folder": "",
    "mail_smtp_host": "",
    "mail_smtp_port": "587",
    "mail_from": "",
    "mail_days_back": "30",
}


def get_config():
    return {key: Setting.get(key, dflt) for key, dflt in MAIL_SETTINGS.items()}


def is_configured(cfg=None):
    cfg = cfg or get_config()
    return bool(cfg["mail_imap_host"] and cfg["mail_username"] and cfg["mail_password"])


@dataclass
class SyncReport:
    scanned: int = 0
    created: int = 0
    skipped: int = 0
    unmatched: int = 0
    errors: list = field(default_factory=list)

    def summary(self):
        text = (
            f"{self.scanned} message(s) analysé(s), {self.created} interaction(s) créée(s), "
            f"{self.unmatched} sans contact correspondant, {self.skipped} déjà importé(s)."
        )
        if self.errors:
            text += " Erreurs : " + " ; ".join(self.errors)
        return text


def _body_text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        content = part.get_payload(decode=True).decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style).*?</\1>", " ", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>", "\n", content)
        content = html.unescape(re.sub(r"<[^>]+>", " ", content))
    content = re.sub(r"[ \t]+", " ", content)
    return re.sub(r"\n\s*\n+", "\n\n", content).strip()


def _strip_quoted(text):
    """Retire l'historique cité (« Le ... a écrit : », lignes « > »)."""
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^(Le|On) .{5,120}(a écrit|wrote)\s*:\s*$", line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _message_date(msg):
    try:
        dt = parsedate_to_datetime(msg["Date"])
    except (TypeError, ValueError):
        return datetime.utcnow()
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _contacts_for(addresses, display_names, incoming):
    found = {}
    emails = [a.lower() for a in addresses if a]
    if emails:
        for contact in Contact.query.filter(func.lower(Contact.email).in_(emails)).all():
            found[contact.id] = contact
    # Expéditeur inconnu : on tente de reconnaître son nom d'affichage.
    if not found and incoming:
        names = " ".join(n for n in display_names if n)
        if names.strip():
            match = best_match(find_contacts(names, Contact.query.all()), threshold=90)
            if match:
                found[match.contact.id] = match.contact
    return list(found.values())


def import_message(raw, own_addresses, report=None):
    """Transforme un message brut (bytes) en interaction(s). Renvoie le nombre créé."""
    report = report or SyncReport()
    report.scanned += 1
    msg = email.message_from_bytes(raw, policy=default_policy)
    message_id = (msg.get("Message-ID") or "").strip() or make_msgid()

    senders = getaddresses(msg.get_all("From", []))
    recipients = getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []))
    own = {a.lower() for a in own_addresses if a}
    incoming = not any(addr.lower() in own for _, addr in senders)
    others = senders if incoming else recipients
    others = [(n, a) for n, a in others if a.lower() not in own]

    contacts = _contacts_for([a for _, a in others], [n for n, _ in others], incoming)
    if not contacts:
        report.unmatched += 1
        return 0

    body = _strip_quoted(_body_text(msg))
    subject = str(msg.get("Subject", "")).strip()
    # Seuls les messages reçus reflètent l'attitude de la personne.
    label = sentiment.analyze(f"{subject}\n{body}")[0] if incoming else "neutral"
    created = 0
    for contact in contacts:
        external_id = f"{message_id}#{contact.id}"
        if Interaction.query.filter_by(external_id=external_id).first():
            report.skipped += 1
            continue
        db.session.add(
            Interaction(
                contact=contact,
                kind="email",
                direction="entrant" if incoming else "sortant",
                date=_message_date(msg),
                subject=subject[:255] or "(sans objet)",
                content=body[:20000],
                sentiment=label,
                sentiment_auto=incoming,
                source="email",
                external_id=external_id[:255],
            )
        )
        created += 1
    report.created += created
    return created


def _fetch_folder(conn, folder, since, own, report):
    status, _ = conn.select(f'"{folder}"', readonly=True)
    if status != "OK":
        report.errors.append(f"dossier « {folder} » introuvable")
        return
    status, data = conn.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
    if status != "OK":
        report.errors.append(f"recherche impossible dans « {folder} »")
        return
    for uid in data[0].split():
        status, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK" or not parts or not isinstance(parts[0], tuple):
            continue
        try:
            import_message(parts[0][1], own, report)
        except Exception as exc:  # un message corrompu ne bloque pas la synchro
            report.errors.append(f"message {uid.decode()} : {exc}")


def sync_mailbox(connect=None):
    """Synchronise la boîte configurée. `connect` permet d'injecter un faux serveur (tests)."""
    cfg = get_config()
    report = SyncReport()
    if not is_configured(cfg):
        report.errors.append("boîte mail non configurée (menu Administration → Messagerie)")
        return report

    last_sync = Setting.get("mail_last_sync")
    if last_sync:
        since = datetime.fromisoformat(last_sync) - timedelta(days=1)
    else:
        since = datetime.utcnow() - timedelta(days=int(cfg["mail_days_back"] or 30))
    own = [cfg["mail_username"], cfg["mail_from"]]

    try:
        if connect:
            conn = connect(cfg)
        else:
            conn = imaplib.IMAP4_SSL(
                cfg["mail_imap_host"], int(cfg["mail_imap_port"] or 993),
                ssl_context=ssl.create_default_context(),
            )
        conn.login(cfg["mail_username"], cfg["mail_password"])
    except (OSError, imaplib.IMAP4.error) as exc:
        report.errors.append(f"connexion IMAP impossible : {exc}")
        return report

    try:
        for folder in (cfg["mail_inbox_folder"], cfg["mail_sent_folder"]):
            if folder:
                _fetch_folder(conn, folder, since, own, report)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    Setting.set("mail_last_sync", datetime.utcnow().isoformat(timespec="seconds"))
    db.session.commit()
    return report


def send_email(contact, subject, body, user=None):
    """Envoie un e-mail via SMTP et l'enregistre comme interaction sortante."""
    cfg = get_config()
    if not (cfg["mail_smtp_host"] and cfg["mail_username"] and cfg["mail_password"]):
        raise RuntimeError("Serveur d'envoi (SMTP) non configuré.")
    if not contact.email:
        raise RuntimeError("Ce contact n'a pas d'adresse e-mail.")

    sender = cfg["mail_from"] or cfg["mail_username"]
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = Address(contact.full_name, addr_spec=contact.email)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(body)

    port = int(cfg["mail_smtp_port"] or 587)
    context = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["mail_smtp_host"], port, context=context, timeout=30)
    else:
        server = smtplib.SMTP(cfg["mail_smtp_host"], port, timeout=30)
        server.starttls(context=context)
    with server:
        server.login(cfg["mail_username"], cfg["mail_password"])
        server.send_message(msg)

    interaction = Interaction(
        contact=contact,
        user=user,
        kind="email",
        direction="sortant",
        subject=subject[:255],
        content=body,
        sentiment="neutral",
        source="email",
        external_id=f"{msg['Message-ID']}#{contact.id}",
    )
    db.session.add(interaction)
    db.session.commit()
    return interaction
