from flask import Flask, render_template, request, redirect, url_for, session, send_file, Blueprint, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from io import StringIO, BytesIO
from flasgger import Swagger, swag_from
from sqlalchemy import text
import csv
from datetime import datetime
from app.core.config import settings
from app.core.db import Base, engine, db_session
from app.core.models import Parent, Child, Lead, Appointment, MessageTemplate, AdminUser
from .forms import LoginForm
from .auth import login_required
from app.admin.routes_messages import bp_messages


app = Flask(__name__, template_folder="templates", static_folder="static", static_url_path="/static")
app.secret_key = settings.ADMIN_SECRET_KEY

app.register_blueprint(bp_messages)


def dmy(dt):
    return dt.strftime('%d-%m-%Y') if dt else ''

app.jinja_env.filters['dmy'] = dmy


# ─────────────────────────────────────────────────────────
# Swagger (Flasgger) — Swagger 2.0, UI на /apidocs
# HTML-роуты остаются как есть.
swagger = Swagger(
    app,
    template={
        "swagger": "2.0",
        "info": {
            "title": "Admin Flask API",
            "version": "1.0.0",
            "description": "JSON API к админке (чтение справочной информации). HTML‑разделы без изменений."
        },
        "schemes": ["http"],
        "basePath": "/",
        "tags": [
            {"name": "Health", "description": "Служебные проверки"},
            {"name": "Parents", "description": "Родители (чтение)"}
        ]
    },
    config={
        "headers": [],
        "specs": [
            {
                "endpoint": "apispec_1",
                "route": "/apispec_1.json",
                "rule_filter": lambda rule: True,
                "model_filter": lambda tag: True,
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/apidocs/"
    }
)


def tg_at(username: str | None) -> str:
    if not username:
        return ""
    username = username.strip().lstrip("@")
    return f"@{username}" if username else ""

# ─────────────────────────────────────────────────────────
# БД, таблицы и дефолтный админ
# ─────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# Грубые миграции недостающих колонок
with engine.begin() as conn:
    # children
    cols_children = {row[1] for row in conn.execute(text("PRAGMA table_info(children)"))}
    if "tg_id" not in cols_children:
        conn.execute(text("ALTER TABLE children ADD COLUMN tg_id VARCHAR"))
    if "schedule_text" not in cols_children:
        conn.execute(text("ALTER TABLE children ADD COLUMN schedule_text TEXT"))
    if "paid" not in cols_children:
        conn.execute(text("ALTER TABLE children ADD COLUMN paid BOOLEAN DEFAULT 0"))
    if "tg_username" not in cols_children:
        conn.execute(text("ALTER TABLE children ADD COLUMN tg_username VARCHAR"))

    # parents
    cols_parents = {row[1] for row in conn.execute(text("PRAGMA table_info(parents)"))}
    if "tg_username" not in cols_parents:
        conn.execute(text("ALTER TABLE parents ADD COLUMN tg_username VARCHAR"))

    # leads
    cols_leads = {row[1] for row in conn.execute(text("PRAGMA table_info(leads)"))}
    if "name" not in cols_leads:
        conn.execute(text("ALTER TABLE leads ADD COLUMN name VARCHAR"))
    if "age" not in cols_leads:
        conn.execute(text("ALTER TABLE leads ADD COLUMN age INTEGER"))
    if "processed" not in cols_leads:
        conn.execute(text("ALTER TABLE leads ADD COLUMN processed BOOLEAN DEFAULT 0"))
    if "source" not in cols_leads:
        conn.execute(text("ALTER TABLE leads ADD COLUMN source VARCHAR"))
    if "tg_username" not in cols_leads:
        conn.execute(text("ALTER TABLE leads ADD COLUMN tg_username VARCHAR"))

    # appointments (на всякий случай — вдруг нет created_at)
    cols_appts = {row[1] for row in conn.execute(text("PRAGMA table_info(appointments)"))}
    if "created_at" not in cols_appts:
        conn.execute(text("ALTER TABLE appointments ADD COLUMN created_at DATETIME"))


# Создаём дефолтного админа, если отсутствует
with db_session() as db:
    if not db.query(AdminUser).filter_by(login=settings.ADMIN_LOGIN).first():
        db.add(AdminUser(
            login=settings.ADMIN_LOGIN,
            password_hash=generate_password_hash(settings.ADMIN_PASSWORD)
        ))

# ─────────────────────────────────────────────────────────
# API Blueprint (только JSON)
# ─────────────────────────────────────────────────────────
api = Blueprint("api", __name__, url_prefix="/api")

@api.get("/health")
@swag_from({
    "tags": ["Health"],
    "summary": "Проверка работоспособности",
    "responses": {
        200: {
            "description": "OK",
            "schema": {
                "type": "object",
                "properties": {"status": {"type": "string", "example": "ok"}}
            }
        }
    }
})
def api_health():
    return jsonify(status="ok")

@api.get("/parents")
@swag_from({
    "tags": ["Parents"],
    "summary": "Получить список родителей (JSON)",
    "parameters": [
        {"in": "query", "name": "limit", "type": "integer", "default": 50, "minimum": 1, "maximum": 500},
        {"in": "query", "name": "offset", "type": "integer", "default": 0, "minimum": 0}
    ],
    "responses": {
        200: {
            "description": "Список родителей",
            "schema": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "tg_id": {"type": "string"},
                                "full_name": {"type": "string"},
                                "phone": {"type": "string"},
                                "city": {"type": "string"},
                                "language": {"type": "string"},
                                "created_at": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
    }
})
def api_parents():
    # простая пагинация
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except Exception:
        limit, offset = 50, 0
    limit = max(1, min(500, limit))
    offset = max(0, offset)

    with db_session() as db:
        total = db.query(Parent).count()
        rows = (db.query(Parent)
                  .order_by(Parent.id.desc())
                  .offset(offset).limit(limit)
                  .all())
        items = []
        for p in rows:
            items.append({
                "id": p.id,
                "tg_id": p.tg_id,
                "full_name": p.full_name or "",
                "phone": p.phone or "",
                "city": p.city or "",
                "language": p.language or "",
                "created_at": (p.created_at.isoformat() if getattr(p, "created_at", None) else None),
            })
    return jsonify(total=total, items=items)

# регистрируем API после объявления
app.register_blueprint(api)

# ─────────────────────────────────────────────────────────
# HTML-админка
# ─────────────────────────────────────────────────────────
# ---- Аутентификация
@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm(request.form)
    if request.method == "POST" and form.validate():
        login_ = form.login.data
        pwd = form.password.data
        with db_session() as db:
            # 👉 ищем по login
            u = db.query(AdminUser).filter_by(login=login_).first()
            if u and check_password_hash(u.password_hash, pwd):
                session["admin_logged"] = True
                return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---- Главная
@app.route("/")
@login_required
def dashboard():
    with db_session() as db:
        parents = db.query(Parent).count()
        children = db.query(Child).count()
        leads = db.query(Lead).count()
        appts = db.query(Appointment).count()
    return render_template("dashboard.html", parents=parents, children=children, leads=leads, appts=appts)

# ---- Разделы
@app.route("/leads")
@login_required
def leads_view():
    with db_session() as db:
        rows = db.query(Lead).order_by(Lead.id.desc()).all()

    items = []
    for l in rows:
        # сырое значение + формат под отображение
        raw_tg = (getattr(l, "tg_username", "") or "").strip().lstrip("@")
        tg_show = f"@{raw_tg}" if raw_tg else ""

        items.append({
            "id": l.id,
            "name": getattr(l, "name", "") or "",
            "age": getattr(l, "age", "") or "",
            "phone": getattr(l, "phone", "") or "",
            "tg_username": tg_show,                 # 🆕 добавили в выдачу
            "source": getattr(l, "source", "") or "",
            "processed": 1 if getattr(l, "processed", 0) else 0,
            "created": (l.created_at.strftime("%d-%m-%Y") if getattr(l, "created_at", None) else "")
        })

    return render_template("leads.html", items=items)


@app.post("/leads/<int:lead_id>/toggle_processed")
@login_required
def lead_toggle_processed(lead_id: int):
    with db_session() as db:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.processed = 0 if getattr(lead, "processed", 0) else 1
    return redirect(url_for("leads_view"))



@app.route("/parents", methods=["GET"])
@login_required
def parents_view():
    with db_session() as db:
        items = db.query(Parent).order_by(Parent.id.desc()).all()
    return render_template("parents.html", items=items, tg_at=tg_at)


@app.route("/parents/<int:parent_id>/update", methods=["POST"])
@login_required
def parent_update(parent_id: int):
    # обновление ФИО родителя из формы
    full_name = (request.form.get("full_name") or "").strip()[:120]
    with db_session() as db:
        p = db.query(Parent).filter(Parent.id == parent_id).first()
        if p:
            p.full_name = full_name
    return redirect(url_for("parents_view"))


@app.route("/children", methods=["GET"])
@login_required
def children_view():
    with db_session() as db:
        items = db.query(Child).order_by(Child.id.desc()).all()
    return render_template("children.html", items=items, tg_at=tg_at)


@app.route("/children/<int:child_id>/update", methods=["POST"])
@login_required
def children_update(child_id: int):
    # обновление имени/возраста/оплаты/расписания ребёнка из формы
    name = (request.form.get("name") or "").strip()[:80]
    age_raw = request.form.get("age") or ""
    try:
        age = int(age_raw) if age_raw != "" else 0
    except Exception:
        age = 0
    paid = 1 if (request.form.get("paid") == "on") else 0
    schedule_text = (request.form.get("schedule_text") or "").strip()

    with db_session() as db:
        c = db.query(Child).filter(Child.id == child_id).first()
        if c:
            c.name = name or c.name
            if age:
                c.age = age
            # эти поля существуют в БД даже если их нет в ORM‑модели
            setattr(c, "paid", paid)
            setattr(c, "schedule_text", schedule_text)
    return redirect(url_for("children_view"))


@app.route("/appointments")
@login_required
def appointments_view():
    with db_session() as db:
        rows = (
            db.query(Appointment, Child)
            .outerjoin(Child, Child.id == Appointment.child_id)
            .order_by(Appointment.id.desc())
            .all()
        )
        items = [{
            "id": a.id,
            "child_name": (c.name if c else "") or "",
            "child_tg": tg_at(getattr(c, "tg_username", None) if c else None),
            "created": (a.created_at.strftime("%d-%m-%Y") if getattr(a, "created_at", None) else ""),
        } for a, c in rows]
    return render_template("appointments.html", items=items)


@app.route("/messages", methods=["GET", "POST"])
@login_required
def messages_view():
    if request.method == "POST":
        key = request.form.get("key", "")
        lang = request.form.get("lang", "ru")
        text_val = request.form.get("text", "")
        with db_session() as db:
            tpl = db.query(MessageTemplate).filter_by(key=key, lang=lang).first()
            if tpl:
                tpl.text = text_val
            else:
                db.add(MessageTemplate(key=key, lang=lang, text=text_val))
        return redirect(url_for("messages_view"))
    with db_session() as db:
        items = db.query(MessageTemplate).all()
    return render_template("messages.html", items=items)

# ---- Экспорт CSV (расширенный)
@app.route("/export.csv")
@login_required
def export_csv():
    si = StringIO(newline="")
    writer = csv.writer(si)
    writer.writerow([
        "parent_id", "parent_full_name", "phone", "language",
        "child_id", "child_name", "child_age", "paid"
    ])

    with db_session() as db:
        rows = (
            db.query(Parent, Child)
            .join(Child, Child.parent_id == Parent.id, isouter=True)
            .order_by(Parent.id.asc())
            .all()
        )
        for p, c in rows:
            writer.writerow([
                p.id,
                p.full_name or "",
                p.phone or "",
                p.language or "",
                getattr(c, "id", "") or "",
                getattr(c, "name", "") or "",
                getattr(c, "age", "") or "",
                getattr(c, "paid", 0) or 0,  # колонка может быть вне ORM‑модели
            ])

    csv_text = si.getvalue()
    buf = BytesIO()
    buf.write("\ufeff".encode("utf-8"))  # BOM для Excel
    buf.write(csv_text.encode("utf-8"))
    buf.seek(0)
    return send_file(buf, mimetype="text/csv; charset=utf-8", as_attachment=True, download_name="export.csv")

if __name__ == "__main__":
    app.run(debug=True)