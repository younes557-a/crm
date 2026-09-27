"""Import de contacts depuis un fichier Excel (.xlsx), CSV ou un tableau Word (.docx).

Règles de fusion (rien n'est jamais supprimé) :
- une cellule vide ne modifie jamais la fiche existante ;
- les étiquettes sont ajoutées à celles déjà présentes ;
- les notes sont ajoutées à la suite des notes existantes ;
- les autres champs déjà remplis ne sont remplacés que si `overwrite` est vrai ;
- les interactions, votes et documents existants ne sont jamais touchés.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from .. import db
from ..models import Contact, Election, VoteRecord
from .text import normalize

# Intitulés de colonnes reconnus (après normalisation : minuscules, sans accents).
HEADER_ALIASES = {
    "civility": ["civilite", "titre", "genre"],
    "first_name": ["prenom", "prenoms", "first name", "firstname"],
    "last_name": ["nom", "nom de famille", "nom d usage", "last name", "lastname", "surname"],
    "email": ["email", "e mail", "mail", "courriel", "adresse email", "adresse e mail", "adresse mail"],
    "phone": ["telephone", "tel", "portable", "mobile", "gsm", "numero", "numero de telephone", "phone"],
    "address": ["adresse", "rue", "adresse postale", "voie", "address"],
    "postal_code": ["code postal", "cp", "code_postal", "codepostal", "postal code", "zip"],
    "city": ["ville", "commune", "localite", "city"],
    "birth_date": ["date de naissance", "naissance", "ne le", "nee le", "birth date"],
    "polling_station": ["bureau de vote", "bureau", "bureau_vote", "bv"],
    "tags": ["tags", "etiquettes", "etiquette", "categorie", "categories", "groupe"],
    "notes": ["notes", "note", "commentaire", "commentaires", "remarques", "observations"],
    "voted": ["a vote", "a_vote", "vote", "a vote ?"],
}
ALIASES = {alias.replace("_", " "): key for key, names in HEADER_ALIASES.items() for alias in names}
SIMPLE_FIELDS = ("civility", "first_name", "last_name", "email", "phone", "address",
                 "postal_code", "city", "polling_station")


class ImportError_(ValueError):
    pass


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    votes: int = 0
    ignored_columns: list = field(default_factory=list)

    def summary(self):
        text = (f"Import terminé : {self.created} contact(s) créé(s), {self.updated} complété(s), "
                f"{self.unchanged} déjà à jour")
        if self.skipped:
            text += f", {self.skipped} ligne(s) ignorée(s) (sans nom)"
        if self.votes:
            text += f", {self.votes} participation(s) au vote enregistrée(s)"
        text += "."
        if self.ignored_columns:
            text += " Colonnes non utilisées : " + ", ".join(self.ignored_columns) + "."
        return text


def _column_key(header):
    return ALIASES.get(normalize(str(header or "")))


def _cell_to_str(value, key):
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    # Excel supprime les zéros initiaux des nombres : on les restaure.
    if key == "postal_code" and text.isdigit() and len(text) == 4:
        text = "0" + text
    if key == "phone" and text.isdigit() and len(text) == 9:
        text = "0" + text
    return text


def _read_xlsx(data):
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportError_(f"fichier Excel illisible ({exc})") from exc
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    # La ligne d'en-tête est la première qui contient une colonne « nom ».
    for header in rows:
        if any(_column_key(h) == "last_name" for h in header):
            break
    else:
        raise ImportError_("colonne « Nom » introuvable dans la feuille")
    for row in rows:
        yield dict(zip(header, row))


def _read_docx(data):
    """Tableau d'un document Word : la première table avec une colonne « Nom »."""
    from docx import Document as DocxDocument

    try:
        doc = DocxDocument(io.BytesIO(data))
    except Exception as exc:
        raise ImportError_(f"document Word illisible ({exc})") from exc
    for table in doc.tables:
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        for i, header in enumerate(rows):
            if any(_column_key(h) == "last_name" for h in header):
                for row in rows[i + 1:]:
                    yield dict(zip(header, row))
                return
    raise ImportError_("aucun tableau avec une colonne « Nom » dans le document Word")


def _read_csv(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    yield from csv.DictReader(io.StringIO(text), dialect=dialect)


def read_rows(data, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("xlsx", "xlsm"):
        return _read_xlsx(data)
    if ext == "docx":
        return _read_docx(data)
    if ext in ("csv", "txt"):
        return _read_csv(data)
    if ext in ("xls", "doc"):
        raise ImportError_(f"ancien format .{ext} : enregistrez le fichier au format .{ext}x")
    raise ImportError_(f"format .{ext} non pris en charge (utilisez .xlsx, .csv ou .docx)")


def _find_existing(data):
    if data.get("email"):
        contact = Contact.query.filter(db.func.lower(Contact.email) == data["email"]).first()
        if contact:
            return contact
    last, first = normalize(data["last_name"]), normalize(data.get("first_name"))
    for contact in Contact.query.filter(Contact.last_name.ilike(data["last_name"][:3] + "%")):
        if normalize(contact.last_name) != last or normalize(contact.first_name) != first:
            continue
        # Homonymes : si les deux fiches ont un code postal, il doit être identique.
        if data.get("postal_code") and contact.postal_code and data["postal_code"] != contact.postal_code:
            continue
        return contact
    return None


def _merge(contact, data, overwrite):
    changed = False
    for key in SIMPLE_FIELDS:
        value = data.get(key)
        if value and getattr(contact, key) != value and (overwrite or not getattr(contact, key)):
            setattr(contact, key, value)
            changed = True
    if data.get("birth_date") and (overwrite or not contact.birth_date):
        try:
            birth = datetime.strptime(data["birth_date"][:10], "%Y-%m-%d").date()
        except ValueError:
            try:
                birth = datetime.strptime(data["birth_date"][:10], "%d/%m/%Y").date()
            except ValueError:
                birth = None
        if birth and birth != contact.birth_date:
            contact.birth_date, changed = birth, True
    if data.get("tags"):
        existing = contact.tag_list
        new = [t.strip() for t in data["tags"].replace(";", ",").split(",") if t.strip()]
        added = [t for t in new if t.lower() not in {e.lower() for e in existing}]
        if added:
            contact.tags = ", ".join(existing + added)
            changed = True
    if data.get("notes") and data["notes"] not in (contact.notes or ""):
        contact.notes = f"{contact.notes}\n{data['notes']}" if contact.notes else data["notes"]
        changed = True
    return changed


def _parse_vote(value):
    v = normalize(value)
    if v in ("oui", "o", "yes", "x", "1", "vrai", "true", "a vote"):
        return True
    if v in ("non", "n", "no", "0", "faux", "false"):
        return False
    return None


def import_contacts(data, filename, overwrite=False, user=None):
    rows = read_rows(data, filename)
    report = ImportReport()
    election = Election.current()
    mapping = None
    for row in rows:
        if mapping is None:
            mapping = {h: _column_key(h) for h in row}
            if "last_name" not in mapping.values():
                raise ImportError_("colonne « Nom » introuvable")
            report.ignored_columns = [str(h) for h, k in mapping.items() if h and not k]
        values = {}
        for header, value in row.items():
            key = mapping.get(header)
            if key and key not in values:
                values[key] = _cell_to_str(value, key)
        if not any(values.values()):
            continue
        if not values.get("last_name"):
            report.skipped += 1
            continue
        if values.get("email"):
            values["email"] = values["email"].lower()

        contact = _find_existing(values)
        if contact is None:
            contact = Contact(first_name="", last_name=values["last_name"])
            db.session.add(contact)
            _merge(contact, values, overwrite=True)
            report.created += 1
        elif _merge(contact, values, overwrite):
            report.updated += 1
        else:
            report.unchanged += 1

        voted = _parse_vote(values.get("voted"))
        if election and voted is not None:
            db.session.flush()
            record = contact.vote_for(election)
            if record is None:
                record = VoteRecord(contact=contact, election=election, recorded_by=user)
                db.session.add(record)
            if record.voted != voted:
                record.voted = voted
                report.votes += 1
        db.session.flush()  # rend la fiche visible pour les lignes suivantes (doublons)
    if mapping is None:
        raise ImportError_("le fichier est vide")
    db.session.commit()
    return report
