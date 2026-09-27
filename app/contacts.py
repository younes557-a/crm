from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from . import db
from .auth import admin_required
from .models import INTERACTION_KINDS, SENTIMENTS, Contact, Election, Interaction, VoteRecord
from .services import sentiment as sentiment_service
from .services.mailbox import send_email
from .services.summary import summarize

bp = Blueprint("contacts", __name__, url_prefix="/contacts")

CONTACT_FIELDS = (
    "civility", "first_name", "last_name", "email", "phone", "address",
    "postal_code", "city", "polling_station", "tags", "notes",
)
PER_PAGE = 50


def _parse_date(value, fmt="%Y-%m-%d"):
    try:
        return datetime.strptime(value, fmt) if value else None
    except ValueError:
        return None


def _fill_contact(contact, form):
    for name in CONTACT_FIELDS:
        value = (form.get(name) or "").strip()
        setattr(contact, name, value or None)
    contact.first_name = contact.first_name or ""
    if contact.email:
        contact.email = contact.email.lower()
    birth = _parse_date(form.get("birth_date"))
    contact.birth_date = birth.date() if birth else None
    return None if contact.last_name else "Le nom est obligatoire."


def _get_contact(contact_id):
    contact = db.session.get(Contact, contact_id)
    if contact is None:
        abort(404)
    return contact


@bp.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    vote = request.args.get("vote", "")
    tag = request.args.get("tag", "").strip()
    election = Election.current()

    query = Contact.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Contact.last_name.ilike(like), Contact.first_name.ilike(like),
                Contact.email.ilike(like), Contact.city.ilike(like),
                Contact.phone.ilike(like), Contact.postal_code.ilike(like),
                (Contact.first_name + " " + Contact.last_name).ilike(like),
                (Contact.last_name + " " + Contact.first_name).ilike(like),
            )
        )
    if tag:
        query = query.filter(Contact.tags.ilike(f"%{tag}%"))
    if election and vote in ("oui", "non"):
        voted_ids = db.session.query(VoteRecord.contact_id).filter_by(
            election_id=election.id, voted=True
        )
        cond = Contact.id.in_(voted_ids)
        query = query.filter(cond if vote == "oui" else ~cond)

    page = query.order_by(Contact.last_name, Contact.first_name).paginate(
        page=request.args.get("page", 1, type=int), per_page=PER_PAGE, error_out=False
    )
    ids = [c.id for c in page.items]
    stats = {}
    if ids:
        rows = (
            db.session.query(Interaction.contact_id, Interaction.sentiment, func.count())
            .filter(Interaction.contact_id.in_(ids))
            .group_by(Interaction.contact_id, Interaction.sentiment)
        )
        for cid, sent, n in rows:
            stats.setdefault(cid, {})[sent] = n
    return render_template(
        "contacts/index.html", page=page, stats=stats, election=election,
        q=q, vote=vote, tag=tag,
    )


@bp.route("/nouveau", methods=["GET", "POST"])
@login_required
def create():
    contact = Contact()
    if request.method == "POST":
        error = _fill_contact(contact, request.form)
        if error:
            flash(error, "danger")
        else:
            db.session.add(contact)
            db.session.commit()
            flash(f"Contact « {contact.full_name} » créé.", "success")
            return redirect(url_for("contacts.show", contact_id=contact.id))
    return render_template("contacts/form.html", contact=contact)


@bp.route("/<int:contact_id>")
@login_required
def show(contact_id):
    contact = _get_contact(contact_id)
    elections = Election.query.order_by(Election.date.desc()).all()
    return render_template(
        "contacts/show.html",
        contact=contact,
        summary=summarize(contact.interactions),
        elections=elections,
        votes={v.election_id: v for v in contact.votes},
        now=datetime.now(),
    )


@bp.route("/<int:contact_id>/modifier", methods=["GET", "POST"])
@login_required
def edit(contact_id):
    contact = _get_contact(contact_id)
    if request.method == "POST":
        error = _fill_contact(contact, request.form)
        if error:
            flash(error, "danger")
        else:
            db.session.commit()
            flash("Fiche mise à jour.", "success")
            return redirect(url_for("contacts.show", contact_id=contact.id))
    return render_template("contacts/form.html", contact=contact)


@bp.route("/<int:contact_id>/supprimer", methods=["POST"])
@admin_required
def delete(contact_id):
    contact = _get_contact(contact_id)
    for doc in contact.documents:
        doc.contact = None
        doc.status = "unmatched"
    db.session.delete(contact)
    db.session.commit()
    flash(f"Contact « {contact.full_name} » supprimé.", "info")
    return redirect(url_for("contacts.index"))


@bp.route("/<int:contact_id>/vote", methods=["POST"])
@login_required
def set_vote(contact_id):
    contact = _get_contact(contact_id)
    election = db.session.get(Election, request.form.get("election_id", type=int))
    if election is None:
        abort(400)
    record = contact.vote_for(election)
    value = request.form.get("voted")
    if value == "inconnu":
        if record:
            db.session.delete(record)
    else:
        if record is None:
            record = VoteRecord(contact=contact, election=election)
            db.session.add(record)
        record.voted = value == "oui"
        record.recorded_at = datetime.utcnow()
        record.recorded_by = current_user
    db.session.commit()
    flash(f"Participation à « {election.name} » enregistrée.", "success")
    target = request.form.get("next", "")
    if not (target.startswith("/") and not target.startswith("//")):
        target = url_for("contacts.show", contact_id=contact.id)
    return redirect(target)


def _fill_interaction(interaction, form):
    interaction.kind = form.get("kind") if form.get("kind") in INTERACTION_KINDS else "autre"
    interaction.direction = "entrant" if form.get("direction") == "entrant" else "sortant"
    interaction.subject = (form.get("subject") or "").strip()[:255] or None
    interaction.content = (form.get("content") or "").strip() or None
    interaction.date = _parse_date(form.get("date"), "%Y-%m-%dT%H:%M") or interaction.date or datetime.utcnow()
    chosen = form.get("sentiment")
    if chosen in SENTIMENTS:
        interaction.sentiment, interaction.sentiment_auto = chosen, False
    else:  # « auto » : suggestion à partir du texte
        text = f"{interaction.subject or ''}\n{interaction.content or ''}"
        interaction.sentiment = sentiment_service.analyze(text)[0]
        interaction.sentiment_auto = True


@bp.route("/<int:contact_id>/interactions", methods=["POST"])
@login_required
def add_interaction(contact_id):
    contact = _get_contact(contact_id)
    interaction = Interaction(contact=contact, user=current_user, source="manuel")
    _fill_interaction(interaction, request.form)
    db.session.add(interaction)
    db.session.commit()
    flash("Interaction enregistrée.", "success")
    return redirect(url_for("contacts.show", contact_id=contact.id))


@bp.route("/interactions/<int:interaction_id>/modifier", methods=["GET", "POST"])
@login_required
def edit_interaction(interaction_id):
    interaction = db.session.get(Interaction, interaction_id) or abort(404)
    if request.method == "POST":
        _fill_interaction(interaction, request.form)
        db.session.commit()
        flash("Interaction mise à jour.", "success")
        return redirect(url_for("contacts.show", contact_id=interaction.contact_id))
    return render_template("contacts/interaction_form.html", interaction=interaction)


@bp.route("/interactions/<int:interaction_id>/supprimer", methods=["POST"])
@login_required
def delete_interaction(interaction_id):
    interaction = db.session.get(Interaction, interaction_id) or abort(404)
    if not current_user.is_admin and interaction.user_id != current_user.id:
        abort(403)
    contact_id = interaction.contact_id
    db.session.delete(interaction)
    db.session.commit()
    flash("Interaction supprimée.", "info")
    return redirect(url_for("contacts.show", contact_id=contact_id))


@bp.route("/<int:contact_id>/email", methods=["POST"])
@login_required
def email(contact_id):
    contact = _get_contact(contact_id)
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    if not subject or not body:
        flash("Objet et message sont obligatoires.", "danger")
    else:
        try:
            send_email(contact, subject, body, user=current_user)
            flash("E-mail envoyé et ajouté à l'historique.", "success")
        except Exception as exc:
            flash(f"Envoi impossible : {exc}", "danger")
    return redirect(url_for("contacts.show", contact_id=contact.id))
