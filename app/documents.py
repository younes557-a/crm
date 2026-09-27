"""Numérisation de courriers : OCR + rattachement automatique au bon contact."""
import os
import uuid
from datetime import datetime

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    send_from_directory, url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from . import db
from .auth import admin_required
from .models import DOCUMENT_STATUSES, Contact, Document, Interaction
from .services import sentiment
from .services.matching import best_match, find_contacts
from .services.ocr import ALLOWED_EXTENSIONS, OCRUnavailable, extract_text, ocr_available

bp = Blueprint("documents", __name__, url_prefix="/courriers")


def _file_path(doc):
    return os.path.join(current_app.config["UPLOAD_FOLDER"], doc.stored_name)


def link_document(doc, contact, user=None):
    """Rattache le document au contact et crée (ou déplace) l'interaction « Courrier »."""
    doc.contact = contact
    doc.status = "linked"
    interaction = Interaction.query.filter_by(document_id=doc.id, source="scan").first()
    if interaction is None:
        text = doc.ocr_text or ""
        interaction = Interaction(
            kind="courrier",
            direction="entrant",
            date=doc.created_at or datetime.utcnow(),
            subject=f"Courrier numérisé : {doc.original_name}"[:255],
            content=text[:5000] or None,
            sentiment=sentiment.analyze(text)[0],
            sentiment_auto=True,
            source="scan",
            document_id=doc.id,
            user=user,
        )
        db.session.add(interaction)
    interaction.contact = contact
    return interaction


def process_document(doc, data):
    """OCR puis recherche de la personne concernée. Met à jour `doc`."""
    doc.ocr_text = extract_text(data, doc.original_name, current_app.config["OCR_LANG"])
    candidates = find_contacts(doc.ocr_text, Contact.query.all())
    doc.match_candidates = [c.as_dict() for c in candidates]
    doc.match_score = candidates[0].score if candidates else None
    match = best_match(candidates, current_app.config["MATCH_AUTO_THRESHOLD"])
    if match:
        db.session.flush()  # l'interaction a besoin de doc.id
        link_document(doc, match.contact, current_user if current_user.is_authenticated else None)
    else:
        doc.contact = None
        doc.status = "review" if candidates else "unmatched"
    return match


@bp.route("/", methods=["GET"])
@login_required
def index():
    status = request.args.get("status", "")
    query = Document.query
    if status in DOCUMENT_STATUSES:
        query = query.filter_by(status=status)
    docs = query.order_by(Document.created_at.desc()).limit(200).all()
    return render_template(
        "documents/index.html", docs=docs, status=status,
        ocr_ok=ocr_available(), allowed=sorted(ALLOWED_EXTENSIONS),
    )


@bp.route("/numeriser", methods=["POST"])
@login_required
def upload():
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Sélectionnez au moins un fichier.", "warning")
        return redirect(url_for("documents.index"))
    linked, review, last_doc = 0, 0, None
    for file in files:
        name = secure_filename(file.filename) or "document"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXTENSIONS:
            flash(f"{file.filename} : format non pris en charge.", "danger")
            continue
        data = file.read()
        doc = Document(
            original_name=file.filename[:255],
            stored_name=f"{uuid.uuid4().hex}.{ext}",
            mime_type=file.mimetype,
            uploaded_by=current_user,
        )
        with open(_file_path(doc), "wb") as fh:
            fh.write(data)
        db.session.add(doc)
        try:
            match = process_document(doc, data)
        except OCRUnavailable as exc:
            doc.status = "unmatched"
            flash(str(exc), "danger")
            match = None
        except Exception as exc:
            doc.status = "unmatched"
            flash(f"{file.filename} : lecture impossible ({exc}).", "danger")
            match = None
        db.session.commit()
        last_doc = doc
        if match:
            linked += 1
            flash(
                f"{file.filename} : rattaché à {match.contact.full_name} "
                f"(confiance {match.score:.0f} %).", "success",
            )
        else:
            review += 1
    if review:
        flash(f"{review} document(s) à vérifier manuellement.", "warning")
    if len(files) == 1 and last_doc is not None:
        return redirect(url_for("documents.show", doc_id=last_doc.id))
    return redirect(url_for("documents.index"))


@bp.route("/<int:doc_id>")
@login_required
def show(doc_id):
    doc = db.session.get(Document, doc_id) or abort(404)
    candidates = []
    for cand in doc.match_candidates or []:
        contact = db.session.get(Contact, cand["contact_id"])
        if contact:
            candidates.append((contact, cand))
    return render_template("documents/show.html", doc=doc, candidates=candidates)


@bp.route("/<int:doc_id>/fichier")
@login_required
def file(doc_id):
    doc = db.session.get(Document, doc_id) or abort(404)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], doc.stored_name,
        download_name=doc.original_name, as_attachment=request.args.get("dl") == "1",
    )


@bp.route("/<int:doc_id>/rattacher", methods=["POST"])
@login_required
def link(doc_id):
    doc = db.session.get(Document, doc_id) or abort(404)
    contact = db.session.get(Contact, request.form.get("contact_id", type=int) or 0)
    if contact is None:
        flash("Contact introuvable.", "danger")
        return redirect(url_for("documents.show", doc_id=doc.id))
    link_document(doc, contact, current_user)
    db.session.commit()
    flash(f"Document rattaché à {contact.full_name}.", "success")
    return redirect(url_for("documents.show", doc_id=doc.id))


@bp.route("/<int:doc_id>/relancer", methods=["POST"])
@login_required
def reprocess(doc_id):
    """Relance l'OCR et la reconnaissance (ex. après ajout du contact dans la base)."""
    doc = db.session.get(Document, doc_id) or abort(404)
    with open(_file_path(doc), "rb") as fh:
        data = fh.read()
    try:
        match = process_document(doc, data)
    except Exception as exc:
        flash(f"Traitement impossible : {exc}", "danger")
        return redirect(url_for("documents.show", doc_id=doc.id))
    if not match:
        Interaction.query.filter_by(document_id=doc.id, source="scan").delete()
    db.session.commit()
    flash(
        f"Rattaché à {match.contact.full_name}." if match else "Aucune correspondance certaine.",
        "success" if match else "warning",
    )
    return redirect(url_for("documents.show", doc_id=doc.id))


@bp.route("/<int:doc_id>/supprimer", methods=["POST"])
@admin_required
def delete(doc_id):
    doc = db.session.get(Document, doc_id) or abort(404)
    Interaction.query.filter_by(document_id=doc.id).update({"document_id": None})
    try:
        os.remove(_file_path(doc))
    except FileNotFoundError:
        pass
    db.session.delete(doc)
    db.session.commit()
    flash("Document supprimé (l'interaction éventuelle est conservée).", "info")
    return redirect(url_for("documents.index"))


@bp.route("/recherche-contacts")
@login_required
def contact_search():
    """Petite API JSON pour choisir un contact à la main."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return {"results": []}
    like = f"%{q}%"
    rows = (
        Contact.query.filter(
            (Contact.last_name.ilike(like)) | (Contact.first_name.ilike(like))
            | (Contact.email.ilike(like)) | ((Contact.first_name + " " + Contact.last_name).ilike(like))
        )
        .order_by(Contact.last_name)
        .limit(15)
        .all()
    )
    return {
        "results": [
            {"id": c.id, "label": f"{c.full_name} — {c.city or ''} {c.email or ''}".strip()}
            for c in rows
        ]
    }
