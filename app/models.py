from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, login_manager

SENTIMENTS = {
    "positive": "Positive",
    "neutral": "Neutre",
    "negative": "Négative",
}

INTERACTION_KINDS = {
    "appel": "Appel téléphonique",
    "porte_a_porte": "Porte-à-porte",
    "rencontre": "Rencontre / réunion",
    "email": "E-mail",
    "courrier": "Courrier",
    "sms": "SMS",
    "evenement": "Événement",
    "autre": "Autre",
}

DOCUMENT_STATUSES = {
    "linked": "Rattaché",
    "review": "À vérifier",
    "unmatched": "Non reconnu",
}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_active(self):
        return self.active

    @property
    def display_name(self):
        return self.full_name or self.username


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    civility = db.Column(db.String(10))
    first_name = db.Column(db.String(80), nullable=False, default="")
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(160), index=True)
    phone = db.Column(db.String(40))
    address = db.Column(db.String(255))
    postal_code = db.Column(db.String(10))
    city = db.Column(db.String(120))
    birth_date = db.Column(db.Date)
    polling_station = db.Column(db.String(80))
    tags = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    interactions = db.relationship(
        "Interaction",
        back_populates="contact",
        cascade="all, delete-orphan",
        order_by="desc(Interaction.date)",
    )
    votes = db.relationship("VoteRecord", back_populates="contact", cascade="all, delete-orphan")
    documents = db.relationship("Document", back_populates="contact")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def tag_list(self):
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    def vote_for(self, election):
        if election is None:
            return None
        for record in self.votes:
            if record.election_id == election.id:
                return record
        return None


class Election(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    date = db.Column(db.Date)
    is_current = db.Column(db.Boolean, default=False)

    @staticmethod
    def current():
        return Election.query.filter_by(is_current=True).first()


class VoteRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"), nullable=False)
    election_id = db.Column(db.Integer, db.ForeignKey("election.id"), nullable=False)
    voted = db.Column(db.Boolean, nullable=False, default=True)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    contact = db.relationship("Contact", back_populates="votes")
    election = db.relationship("Election")
    recorded_by = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("contact_id", "election_id"),)


class Interaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    kind = db.Column(db.String(30), nullable=False, default="autre")
    direction = db.Column(db.String(10), default="sortant")  # entrant / sortant
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    subject = db.Column(db.String(255))
    content = db.Column(db.Text)
    sentiment = db.Column(db.String(10), nullable=False, default="neutral")
    sentiment_auto = db.Column(db.Boolean, default=False)
    source = db.Column(db.String(20), default="manuel")  # manuel / email / scan
    external_id = db.Column(db.String(255), unique=True)  # Message-ID des e-mails
    document_id = db.Column(db.Integer, db.ForeignKey("document.id"))

    contact = db.relationship("Contact", back_populates="interactions")
    user = db.relationship("User")
    document = db.relationship("Document", foreign_keys=[document_id])

    @property
    def kind_label(self):
        return INTERACTION_KINDS.get(self.kind, self.kind)

    @property
    def sentiment_label(self):
        return SENTIMENTS.get(self.sentiment, self.sentiment)


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(100))
    ocr_text = db.Column(db.Text)
    contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"))
    match_score = db.Column(db.Float)
    match_candidates = db.Column(db.JSON)  # [{"contact_id", "score", "reasons"}]
    status = db.Column(db.String(20), nullable=False, default="unmatched")
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    contact = db.relationship("Contact", back_populates="documents")
    uploaded_by = db.relationship("User")

    @property
    def status_label(self):
        return DOCUMENT_STATUSES.get(self.status, self.status)


class Setting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text)

    @staticmethod
    def get(key, default=None):
        row = db.session.get(Setting, key)
        return row.value if row and row.value is not None else default

    @staticmethod
    def set(key, value):
        row = db.session.get(Setting, key)
        if row is None:
            row = Setting(key=key)
            db.session.add(row)
        row.value = value
