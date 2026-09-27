import pytest

from app import create_app, db
from app.models import Contact, User


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "CSRF_ENABLED": False,
    })
    with app.app_context():
        admin = User(username="admin", role="admin")
        admin.set_password("motdepasse-admin")
        user = User(username="militant", role="user")
        user.set_password("motdepasse-user")
        db.session.add_all([admin, user])
        db.session.add_all([
            Contact(first_name="Jean", last_name="Dupont", email="jean.dupont@example.org",
                    phone="06 12 34 56 78", address="12 rue des Lilas", postal_code="75011", city="Paris"),
            Contact(first_name="Marie", last_name="Dupont", postal_code="69003", city="Lyon"),
            Contact(first_name="Pierre", last_name="Martin", email="p.martin@example.org", city="Nantes"),
            Contact(first_name="Anne-Sophie", last_name="Le Goff", city="Brest"),
        ])
        db.session.commit()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username="admin", password="motdepasse-admin"):
    return client.post("/login", data={"username": username, "password": password})


@pytest.fixture
def admin_client(client):
    login(client)
    return client


@pytest.fixture
def user_client(client):
    login(client, "militant", "motdepasse-user")
    return client
