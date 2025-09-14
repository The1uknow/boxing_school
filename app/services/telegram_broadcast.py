from time import sleep
from typing import Iterable, Literal
from sqlalchemy.orm import Session

from app.core.models import Parent, Child
from app.bot.bot import safe_send_message

Audience = Literal["parents", "children"]


def _parent_chat_ids(db: Session) -> Iterable[int]:
    q = db.query(Parent).filter(Parent.tg_id.isnot(None))
    for p in q.yield_per(1000):
        try:
            yield int(p.tg_id)
        except Exception:
            continue


def _child_chat_ids(db: Session) -> Iterable[int]:
    q = db.query(Child).filter(Child.tg_id.isnot(None))
    for c in q.yield_per(1000):
        try:
            yield int(c.tg_id)
        except Exception:
            continue


def broadcast_message(db: Session, audience: Audience, text: str) -> dict:
    """
    Рассылает text выбранной аудитории.
    audience: 'parents' | 'children'
    Возвращает {"sent": N, "failed": M}
    """
    text = (text or "").strip()
    if not text:
        return {"sent": 0, "failed": 0}

    ids = _child_chat_ids(db) if audience == "children" else _parent_chat_ids(db)

    sent = 0
    failed = 0

    for chat_id in ids:
        r = safe_send_message(chat_id, text)
        if r:
            sent += 1
            sleep(0.05)  # лёгкий троттлинг
        else:
            failed += 1
            sleep(0.2)

    return {"sent": sent, "failed": failed}