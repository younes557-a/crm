from app.models import Contact
from app.services.matching import best_match, find_contacts


def names(cands):
    return [c.contact.full_name for c in cands]


def test_full_name_in_letter(app):
    text = """Jean Dupont
    12 rue des Lilas
    75011 Paris

    Madame la Maire, je vous écris au sujet du marché."""
    cands = find_contacts(text, Contact.query.all())
    match = best_match(cands, 85)
    assert match and match.contact.full_name == "Jean Dupont"
    assert "code postal" in match.reasons


def test_ocr_typo_and_inverted_order(app):
    cands = find_contacts("Expéditeur : DUPOMT Jean, Paris", Contact.query.all())
    assert best_match(cands, 85).contact.full_name == "Jean Dupont"


def test_surname_only_is_not_auto_linked(app):
    cands = find_contacts("Famille Dupont", Contact.query.all())
    assert best_match(cands, 85) is None
    assert set(names(cands)) == {"Jean Dupont", "Marie Dupont"}


def test_initial_and_city_disambiguate(app):
    cands = find_contacts("M. Dupont, 69003 Lyon", Contact.query.all())
    assert cands[0].contact.full_name == "Marie Dupont"


def test_compound_names(app):
    cands = find_contacts("Bien cordialement,\nAnne Sophie LE GOFF", Contact.query.all())
    assert best_match(cands, 85).contact.full_name == "Anne-Sophie Le Goff"


def test_email_and_phone(app):
    assert best_match(find_contacts("Contact : p.martin@example.org", Contact.query.all()), 85)
    match = best_match(find_contacts("Rappelez-moi au 06.12.34.56.78", Contact.query.all()), 85)
    assert match.contact.full_name == "Jean Dupont"


def test_no_match(app):
    assert find_contacts("Courrier de la préfecture sans nom", Contact.query.all()) == []
