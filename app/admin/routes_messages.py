from flask import Blueprint, render_template, request, redirect, url_for, flash
from app.admin.forms import BroadcastForm
from app.core.db import db_session
from app.services.telegram_broadcast import broadcast_message

bp_messages = Blueprint(
    "messages",
    __name__,
    url_prefix="/admin/messages",
    template_folder="templates",
)

@bp_messages.route("/", methods=["GET", "POST"])
def messages():
    form = BroadcastForm(request.form)
    if request.method == "POST" and form.validate():
        with db_session() as db:
            stats = broadcast_message(db, audience=form.audience.data, text=form.text.data)
        flash(f"Отправлено: {stats['sent']}, ошибок: {stats['failed']}", "success")
        return redirect(url_for("messages.messages"))
    # ВАЖНО: имя файла без префикса "admin/"
    return render_template("messages.html", form=form)