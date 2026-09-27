from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from .models import Interaction, Setting
from .services.mailbox import get_config, is_configured, sync_mailbox

bp = Blueprint("mail", __name__, url_prefix="/messagerie")


@bp.route("/")
@login_required
def index():
    cfg = get_config()
    return render_template(
        "mail/index.html",
        configured=is_configured(cfg),
        mailbox=cfg["mail_username"],
        last_sync=Setting.get("mail_last_sync"),
        recent=Interaction.query.filter_by(source="email")
        .order_by(Interaction.date.desc())
        .limit(30)
        .all(),
    )


@bp.route("/synchroniser", methods=["POST"])
@login_required
def sync():
    report = sync_mailbox()
    flash(report.summary(), "danger" if report.errors else "success")
    return redirect(url_for("mail.index"))
