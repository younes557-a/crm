from email.message import EmailMessage

from app import db
from app.models import Interaction, Setting
from app.services.mailbox import import_message, sync_mailbox


def make_mail(frm, to, subject, body, msgid):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = frm, to, subject
    msg["Message-ID"] = msgid
    msg["Date"] = "Mon, 21 Sep 2026 10:00:00 +0200"
    msg.set_content(body)
    return msg.as_bytes()


OWN = ["campagne@example.fr"]


def test_incoming_mail_linked_by_address(app):
    raw = make_mail('"Jean D." <Jean.Dupont@example.org>', "campagne@example.fr",
                    "Réunion", "Bravo et merci pour la réunion d'hier !\n\n> ancien message", "<a@x>")
    assert import_message(raw, OWN) == 1
    db.session.commit()
    inter = Interaction.query.one()
    assert inter.contact.full_name == "Jean Dupont"
    assert inter.direction == "entrant" and inter.sentiment == "positive"
    assert "ancien message" not in inter.content
    assert import_message(raw, OWN) == 0  # pas de doublon


def test_outgoing_mail_and_display_name_match(app):
    out = make_mail("campagne@example.fr", "p.martin@example.org", "Invitation", "Venez !", "<b@x>")
    assert import_message(out, OWN) == 1
    unknown = make_mail("Marie Dupont <marie.perso@gmail.com>", "campagne@example.fr",
                        "Question", "Bonjour", "<c@x>")
    assert import_message(unknown, OWN) == 1
    db.session.commit()
    assert {i.contact.full_name for i in Interaction.query} == {"Pierre Martin", "Marie Dupont"}


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages

    def login(self, user, pwd):
        pass

    def select(self, folder, readonly=False):
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b" ".join(str(i).encode() for i in range(len(self.messages)))]
        return "OK", [(b"1 (BODY[] {10}", self.messages[int(args[0])])]

    def logout(self):
        pass


def test_sync_mailbox(app):
    for key, value in {"mail_imap_host": "imap.test", "mail_username": "campagne@example.fr",
                       "mail_password": "x"}.items():
        Setting.set(key, value)
    db.session.commit()
    msgs = [make_mail("jean.dupont@example.org", "campagne@example.fr", "Colère",
                      "Je suis furieux, c'est un scandale.", "<d@x>"),
            make_mail("inconnu@example.com", "campagne@example.fr", "Pub", "Promo", "<e@x>")]
    report = sync_mailbox(connect=lambda cfg: FakeIMAP(msgs))
    assert report.created == 1 and report.unmatched == 1 and not report.errors
    assert Interaction.query.one().sentiment == "negative"
    assert Setting.get("mail_last_sync")
