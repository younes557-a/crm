"""CRM électoral / relation citoyens.

Application Flask : fiches contacts, interactions, participation au vote,
synchronisation d'une boîte mail (IMAP) et numérisation de courriers (OCR)
rattachés automatiquement à la bonne personne.
"""
import os
import secrets

import click
from flask import Flask, abort, redirect, request, session, url_for
from flask_login import LoginManager, current_user
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "warning"


def _load_secret_key(instance_path):
    env_key = os.environ.get("CRM_SECRET_KEY")
    if env_key:
        return env_key
    key_file = os.path.join(instance_path, "secret_key")
    if os.path.exists(key_file):
        with open(key_file) as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    with open(key_file, "w") as fh:
        fh.write(key)
    os.chmod(key_file, 0o600)
    return key


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "CRM_DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "crm.db")
        ),
        UPLOAD_FOLDER=os.environ.get(
            "CRM_UPLOAD_FOLDER", os.path.join(app.instance_path, "uploads")
        ),
        MAX_CONTENT_LENGTH=30 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # En ligne (HTTPS derrière un proxy, ex. Render) : cookies sécurisés.
        SESSION_COOKIE_SECURE=os.environ.get("CRM_BEHIND_PROXY") == "1",
        REMEMBER_COOKIE_SECURE=os.environ.get("CRM_BEHIND_PROXY") == "1",
        OCR_LANG=os.environ.get("CRM_OCR_LANG", "fra+eng"),
        # Score minimal (0-100) pour rattacher automatiquement un document.
        MATCH_AUTO_THRESHOLD=85,
        CSRF_ENABLED=True,
    )
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_secret_key(app.instance_path)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    if os.environ.get("CRM_BEHIND_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix

        # Vraie IP du visiteur (anti force brute) et schéma https.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    login_manager.init_app(app)

    from . import models  # noqa: F401  (enregistre les modèles)
    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .contacts import bp as contacts_bp
    from .documents import bp as documents_bp
    from .mail import bp as mail_bp
    from .main import bp as main_bp

    for blueprint in (auth_bp, main_bp, contacts_bp, documents_bp, mail_bp, admin_bp):
        app.register_blueprint(blueprint)

    with app.app_context():
        db.create_all()

    _register_security(app)
    _register_template_helpers(app)
    _register_cli(app)
    return app


def _register_security(app):
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        return session["_csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def protect():
        from .models import User

        # Premier lancement : forcer la création du compte administrateur.
        if request.endpoint not in ("auth.setup", "static") and User.query.count() == 0:
            return redirect(url_for("auth.setup"))

        if request.method == "POST" and app.config.get("CSRF_ENABLED", True):
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
                abort(400, "Jeton de sécurité (CSRF) invalide ou expiré. Rechargez la page.")

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp


def _register_template_helpers(app):
    from .models import INTERACTION_KINDS, SENTIMENTS

    @app.template_filter("datefr")
    def datefr(value, with_time=False):
        if not value:
            return "—"
        return value.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")

    @app.context_processor
    def inject():
        return {
            "SENTIMENTS": SENTIMENTS,
            "INTERACTION_KINDS": INTERACTION_KINDS,
            "is_admin": current_user.is_authenticated and current_user.is_admin,
        }


def _register_cli(app):
    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--admin", is_flag=True, help="Donne le rôle administrateur.")
    @click.password_option()
    def create_user(username, admin, password):
        """Crée un compte (utilisateur ou administrateur)."""
        from .models import User

        if User.query.filter_by(username=username).first():
            raise click.ClickException("Cet identifiant existe déjà.")
        user = User(username=username, role="admin" if admin else "user")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Compte « {username} » créé ({user.role}).")

    @app.cli.command("sync-mail")
    def sync_mail():
        """Synchronise la boîte mail configurée (à planifier avec cron)."""
        from .services.mailbox import sync_mailbox

        report = sync_mailbox()
        click.echo(report.summary())
