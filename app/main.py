from datetime import datetime, timedelta

from flask import Blueprint, render_template
from flask_login import login_required
from sqlalchemy import func

from . import db
from .models import Contact, Document, Election, Interaction, VoteRecord
from .services.mailbox import is_configured

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def dashboard():
    since = datetime.utcnow() - timedelta(days=30)
    sentiment_counts = dict(
        db.session.query(Interaction.sentiment, func.count(Interaction.id))
        .filter(Interaction.date >= since)
        .group_by(Interaction.sentiment)
        .all()
    )
    election = Election.current()
    total_contacts = Contact.query.count()
    voted = 0
    if election:
        voted = VoteRecord.query.filter_by(election_id=election.id, voted=True).count()
    return render_template(
        "dashboard.html",
        total_contacts=total_contacts,
        interactions_30d=sum(sentiment_counts.values()),
        sentiment_counts=sentiment_counts,
        election=election,
        voted=voted,
        turnout=round(100 * voted / total_contacts) if total_contacts and election else 0,
        to_review=Document.query.filter(Document.status != "linked").count(),
        recent=Interaction.query.order_by(Interaction.date.desc()).limit(10).all(),
        mail_configured=is_configured(),
    )
