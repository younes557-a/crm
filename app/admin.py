import csv
import io
from datetime import datetime

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import db
from .auth import admin_required, validate_password
from .models import Contact, Election, Setting, User, VoteRecord
from .services.mailbox import MAIL_SETTINGS, get_config
from .services import importer

bp = Blueprint("admin", __name__, url_prefix="/admin")

# Colonnes des fichiers exportés (relisibles par l'import).
CSV_COLUMNS = {
    "civilite": "civility", "prenom": "first_name", "nom": "last_name", "email": "email",
    "telephone": "phone", "adresse": "address", "code_postal": "postal_code",
    "ville": "city", "bureau_vote": "polling_station", "tags": "tags", "notes": "notes",
}


@bp.route("/")
@admin_required
def index():
    return render_template(
        "admin/index.html",
        users=User.query.order_by(User.username).all(),
        elections=Election.query.order_by(Election.date.desc()).all(),
    )


# --- Comptes -------------------------------------------------------------

@bp.route("/utilisateurs/nouveau", methods=["POST"])
@admin_required
def create_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    error = None if username else "Identifiant obligatoire."
    if not error and User.query.filter_by(username=username).first():
        error = "Cet identifiant existe déjà."
    error = error or validate_password(password, request.form.get("confirm", ""))
    if error:
        flash(error, "danger")
    else:
        user = User(
            username=username,
            full_name=request.form.get("full_name", "").strip() or None,
            role="admin" if request.form.get("role") == "admin" else "user",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"Compte « {username} » créé.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/utilisateurs/<int:user_id>", methods=["POST"])
@admin_required
def update_user(user_id):
    user = db.get_or_404(User, user_id)
    action = request.form.get("action")
    if user.id == current_user.id and action in ("toggle", "role"):
        flash("Vous ne pouvez pas modifier votre propre rôle ou statut.", "warning")
        return redirect(url_for("admin.index"))
    if action == "toggle":
        user.active = not user.active
    elif action == "role":
        user.role = "user" if user.is_admin else "admin"
    elif action == "password":
        password = request.form.get("password", "")
        error = validate_password(password, password)
        if error:
            flash(error, "danger")
            return redirect(url_for("admin.index"))
        user.set_password(password)
    db.session.commit()
    flash(f"Compte « {user.username} » mis à jour.", "success")
    return redirect(url_for("admin.index"))


# --- Élections -----------------------------------------------------------

@bp.route("/elections", methods=["POST"])
@admin_required
def elections():
    action = request.form.get("action")
    if action == "create":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Nom de l'élection obligatoire.", "danger")
            return redirect(url_for("admin.index"))
        date = request.form.get("date")
        election = Election(
            name=name, date=datetime.strptime(date, "%Y-%m-%d").date() if date else None
        )
        if not Election.query.count():
            election.is_current = True
        db.session.add(election)
    else:
        election = db.get_or_404(Election, request.form.get("election_id", type=int))
        if action == "current":
            Election.query.update({"is_current": False})
            election.is_current = True
        elif action == "delete":
            VoteRecord.query.filter_by(election_id=election.id).delete()
            db.session.delete(election)
    db.session.commit()
    flash("Élections mises à jour.", "success")
    return redirect(url_for("admin.index"))


# --- Messagerie ----------------------------------------------------------

@bp.route("/messagerie", methods=["GET", "POST"])
@admin_required
def mail_settings():
    if request.method == "POST":
        for key in MAIL_SETTINGS:
            value = request.form.get(key, "").strip()
            if key == "mail_password" and not value:
                continue  # champ vide = on garde le mot de passe existant
            Setting.set(key, value)
        if request.form.get("reset_sync"):
            Setting.set("mail_last_sync", None)
        db.session.commit()
        flash("Paramètres de messagerie enregistrés.", "success")
        return redirect(url_for("admin.mail_settings"))
    cfg = get_config()
    cfg["has_password"] = bool(cfg.pop("mail_password"))
    return render_template("admin/mail.html", cfg=cfg)


# --- Import / export -----------------------------------------------------

@bp.route("/import", methods=["POST"])
@admin_required
def import_contacts():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choisissez un fichier Excel, CSV ou Word.", "warning")
        return redirect(url_for("admin.index"))
    try:
        report = importer.import_contacts(
            file.read(), file.filename,
            overwrite=bool(request.form.get("overwrite")), user=current_user,
        )
    except importer.ImportError_ as exc:
        db.session.rollback()
        flash(f"Import impossible : {exc}.", "danger")
        return redirect(url_for("admin.index"))
    flash(report.summary(), "success")
    return redirect(url_for("admin.index"))


@bp.route("/export.csv")
@admin_required
def export_csv():
    election = Election.current()
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    header = list(CSV_COLUMNS) + ["a_vote", "nb_interactions"]
    writer.writerow(header)
    for c in Contact.query.order_by(Contact.last_name, Contact.first_name):
        record = c.vote_for(election)
        writer.writerow(
            [getattr(c, field) or "" for field in CSV_COLUMNS.values()]
            + ["" if record is None else ("oui" if record.voted else "non"), len(c.interactions)]
        )
    return Response(
        "﻿" + out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=contacts.csv"},
    )


@bp.route("/export.xlsx")
@admin_required
def export_xlsx():
    from openpyxl import Workbook
    from openpyxl.styles import Font

    election = Election.current()
    wb = Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append(list(CSV_COLUMNS) + ["a_vote", "nb_interactions"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for c in Contact.query.order_by(Contact.last_name, Contact.first_name):
        record = c.vote_for(election)
        ws.append(
            [getattr(c, field) or "" for field in CSV_COLUMNS.values()]
            + ["" if record is None else ("oui" if record.voted else "non"), len(c.interactions)]
        )
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return Response(
        out.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=contacts.xlsx"},
    )
