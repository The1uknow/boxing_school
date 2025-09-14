import secrets
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.models import Parent, Child, Appointment


def get_or_create_parent(db: Session, tg_id: str, lang: str, ref_code: str = "") -> Parent:
    """Вернёт существующего родителя по tg_id или создаст нового.
    Коммит/флаш — на вызывающей стороне.
    """
    tg_id = (tg_id or "").strip()
    lang = (lang or "").strip() or "ru"
    ref_code = (ref_code or "").strip()

    p: Optional[Parent] = db.query(Parent).filter_by(tg_id=tg_id).first()
    if p:
        changed = False
        if lang and p.language != lang:
            p.language = lang
            changed = True
        if ref_code and not p.ref_code:
            p.ref_code = ref_code
            changed = True
        # коммит снаружи — оставляем как у тебя
        return p

    p = Parent(tg_id=tg_id, language=lang, ref_code=ref_code)
    db.add(p)
    # важно: id появится после flush/commit — вызывающий решает когда
    return p


def _generate_token(n_bytes: int = 6) -> str:
    """Короткий URL‑safe токен (≈ 8–10 символов при 6 байтах)."""
    return secrets.token_urlsafe(n_bytes)


def add_child(db: Session, parent: Parent, name: str, age: int, has_telegram: bool = True) -> Child:
    """Создаёт ребёнка. Обеспечиваем:
    - у parent есть id (флашим при необходимости),
    - токен уникален (ловим IntegrityError).
    Коммит — на вызывающей стороне.
    """
    if parent.id is None:
        # если parent только что добавлен, гарантируем наличие id
        db.flush()

    name = (name or "").strip()
    if not name:
        raise ValueError("Child name is required")

    # Генерируем уникальный токен с защитой от коллизий
    while True:
        token = _generate_token(6)
        ch = Child(
            parent_id=parent.id,
            name=name,
            age=int(age),
            token=token,
            has_telegram=has_telegram,
        )
        db.add(ch)
        try:
            # Пытаемся зафиксировать только вставку ребёнка.
            db.flush()
            # Успех — токен уникален
            return ch
        except IntegrityError:
            # Коллизия по уникальному индексу token — пробуем снова
            db.rollback()
            # Важно: parent всё равно в сессии; цикл сгенерирует новый токен и повторит вставку


def list_children(db: Session, parent: Parent) -> list[Child]:
    return db.query(Child).filter_by(parent_id=parent.id).all()


def create_appointment(db: Session, child_id: int, datetime_str: str, location: str = "Главный зал") -> Appointment:
    """Создаёт запись на тренировку. Коммит — на вызывающей стороне.
    При желании проверяем, что ребёнок существует.
    """
    # (опционально) убеждаемся, что child существует
    exists = db.query(Child.id).filter_by(id=child_id).first()
    if not exists:
        raise ValueError(f"Child #{child_id} not found")

    ap = Appointment(child_id=child_id, datetime_str=(datetime_str or "").strip(), location=(location or "Главный зал"))
    db.add(ap)
    return ap