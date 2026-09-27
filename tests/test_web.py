import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app import create_app, db
from app.models import Contact, Document, Election, Interaction, User
from app.services.ocr import ocr_available

from .conftest import login


def test_first_run_setup(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "CSRF_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'a.db'}",
                      "UPLOAD_FOLDER": str(tmp_path / "u")})
    client = app.test_client()
    assert client.get("/").headers["Location"].endswith("/setup")
    client.post("/setup", data={"username": "chef", "password": "12345678", "confirm": "12345678"})
    with app.app_context():
        assert User.query.one().is_admin


def test_login_required_and_bad_password(client):
    assert "/login" in client.get("/contacts/").headers["Location"]
    resp = login(client, password="mauvais")
    assert "incorrect" in resp.get_data(as_text=True)


def test_roles(user_client):
    assert user_client.get("/contacts/").status_code == 200
    assert user_client.get("/admin/").status_code == 403
    assert user_client.post("/contacts/1/supprimer").status_code == 403


def test_csrf_enforced(app):
    app.config["CSRF_ENABLED"] = True
    client = app.test_client()
    assert client.post("/login", data={"username": "admin", "password": "x"}).status_code == 400


def test_contact_page_with_synthesis(admin_client, app):
    admin_client.post("/contacts/1/interactions", data={
        "kind": "appel", "direction": "sortant", "sentiment": "auto",
        "subject": "Appel", "content": "Très content, merci pour votre soutien !",
    })
    admin_client.post("/contacts/1/interactions", data={
        "kind": "porte_a_porte", "sentiment": "negative", "content": "Pas intéressé",
    })
    html = admin_client.get("/contacts/1").get_data(as_text=True)
    assert "SYNTHÈSE DES INTERACTIONS" in html
    assert "1 positive" in html and "1 négative" in html
    with app.app_context():
        assert {i.sentiment for i in Interaction.query.all()} == {"positive", "negative"}


def test_vote_tracking(user_client, app):
    with app.app_context():
        e = Election(name="Municipales", is_current=True)
        db.session.add(e)
        db.session.commit()
        eid = e.id
    user_client.post("/contacts/1/vote", data={"election_id": eid, "voted": "oui"})
    html = user_client.get("/contacts/?vote=oui").get_data(as_text=True)
    assert "DUPONT" in html and "MARTIN" not in html
    user_client.post("/contacts/1/vote", data={"election_id": eid, "voted": "inconnu"})
    assert "DUPONT" not in user_client.get("/contacts/?vote=oui").get_data(as_text=True)


def test_upload_text_letter_is_linked(user_client, app):
    letter = "Pierre Martin\nNantes\n\nJe suis très mécontent, c'est inacceptable.".encode()
    resp = user_client.post("/courriers/numeriser", data={"files": (io.BytesIO(letter), "lettre.txt")},
                            content_type="multipart/form-data")
    assert resp.status_code == 302
    with app.app_context():
        doc = Document.query.one()
        assert doc.status == "linked" and doc.contact.full_name == "Pierre Martin"
        inter = Interaction.query.filter_by(document_id=doc.id).one()
        assert inter.kind == "courrier" and inter.sentiment == "negative"


def test_ambiguous_letter_needs_review_then_manual_link(user_client, app):
    user_client.post("/courriers/numeriser", data={"files": (io.BytesIO(b"Famille Dupont"), "a.txt")},
                     content_type="multipart/form-data")
    with app.app_context():
        doc = Document.query.one()
        assert doc.status == "review" and doc.contact is None
        doc_id = doc.id
    assert "Rattacher" in user_client.get(f"/courriers/{doc_id}").get_data(as_text=True)
    user_client.post(f"/courriers/{doc_id}/rattacher", data={"contact_id": 2})
    with app.app_context():
        doc = db.session.get(Document, doc_id)
        assert doc.status == "linked" and doc.contact.full_name == "Marie Dupont"
        assert Interaction.query.filter_by(document_id=doc_id).one().contact_id == 2


@pytest.mark.skipif(not ocr_available(), reason="Tesseract absent")
def test_scanned_image_is_recognised(user_client, app):
    img = Image.new("RGB", (1400, 700), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    lines = ["Anne-Sophie Le Goff", "4 place de la Liberté", "29200 Brest", "",
             "Merci pour votre engagement, je vous soutiens !"]
    for n, line in enumerate(lines):
        draw.text((80, 60 + n * 70), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    user_client.post("/courriers/numeriser", data={"files": (buf, "scan.png")},
                     content_type="multipart/form-data")
    with app.app_context():
        doc = Document.query.one()
        assert doc.contact and doc.contact.last_name == "Le Goff", doc.ocr_text
        assert Interaction.query.filter_by(document_id=doc.id).one().sentiment == "positive"


def test_csv_import_export(admin_client, app):
    csv_data = "prénom;nom;email;ville\nLucie;Bernard;lucie@example.org;Lille\nJean;Dupont;JEAN.DUPONT@example.org;Paris 11\n"
    admin_client.post("/admin/import", data={"file": (io.BytesIO(csv_data.encode()), "c.csv")},
                      content_type="multipart/form-data")
    with app.app_context():
        assert Contact.query.count() == 5
        assert Contact.query.filter_by(email="jean.dupont@example.org").one().city == "Paris 11"
    export = admin_client.get("/admin/export.csv").get_data(as_text=True)
    assert "Bernard" in export


def test_admin_creates_user(admin_client, app):
    admin_client.post("/admin/utilisateurs/nouveau", data={
        "username": "benevole", "password": "abcdefgh", "confirm": "abcdefgh", "role": "user"})
    with app.app_context():
        assert User.query.filter_by(username="benevole").one().role == "user"


def test_all_pages_render(admin_client):
    for url in ["/", "/contacts/", "/contacts/nouveau", "/contacts/1", "/courriers/",
                "/messagerie/", "/admin/", "/admin/messagerie", "/mon-compte"]:
        assert admin_client.get(url).status_code == 200, url
