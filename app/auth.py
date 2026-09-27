from datetime import datetime
from functools import wraps
from time import monotonic

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import db
from .models import User

bp = Blueprint("auth", __name__)

MIN_PASSWORD_LENGTH = 8
# Anti force brute simple : 5 échecs → blocage 5 minutes (par identifiant + IP).
_failures = {}
MAX_FAILURES, LOCK_SECONDS = 5, 300


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def validate_password(password, confirm):
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
    if password != confirm:
        return "Les deux mots de passe ne correspondent pas."
    return None


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("main.dashboard")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Création du premier compte administrateur."""
    if User.query.count() > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        error = None if username else "Identifiant obligatoire."
        error = error or validate_password(password, request.form.get("confirm", ""))
        if error:
            flash(error, "danger")
        else:
            user = User(username=username, full_name=request.form.get("full_name"), role="admin")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Compte administrateur créé. Bienvenue !", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        key = (username.lower(), request.remote_addr)
        count, since = _failures.get(key, (0, 0))
        if count >= MAX_FAILURES and monotonic() - since < LOCK_SECONDS:
            flash("Trop de tentatives. Réessayez dans quelques minutes.", "danger")
            return render_template("auth/login.html"), 429

        user = User.query.filter_by(username=username).first()
        if user and user.active and user.check_password(request.form.get("password", "")):
            _failures.pop(key, None)
            user.last_login = datetime.utcnow()
            db.session.commit()
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(_safe_next(request.args.get("next")))
        _failures[key] = (count + 1, monotonic())
        flash("Identifiant ou mot de passe incorrect.", "danger")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/mon-compte", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        if not current_user.check_password(request.form.get("current", "")):
            flash("Mot de passe actuel incorrect.", "danger")
        else:
            error = validate_password(request.form.get("password", ""), request.form.get("confirm", ""))
            if error:
                flash(error, "danger")
            else:
                current_user.set_password(request.form["password"])
                db.session.commit()
                flash("Mot de passe modifié.", "success")
                return redirect(url_for("auth.account"))
    return render_template("auth/account.html")
