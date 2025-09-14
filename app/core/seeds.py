from sqlalchemy.orm import Session
from app.core.models import MessageTemplate


def seed_templates(db: Session):
    defaults = [
        ("welcome", "ru", "Добро пожаловать в школу бокса! 🥊"),
        ("welcome", "uz", "Boks maktabiga xush kelibsiz! 🥊"),
        ("price_dm", "ru", "Наши цены: 4 занятия — 300k, 8 занятий — 520k."),
        ("price_dm", "uz", "Narxlar: 4 dars — 300k, 8 dars — 520k."),
    ]
    for key, lang, text in defaults:
        exists = db.query(MessageTemplate).filter_by(key=key, lang=lang).first()
        if not exists:
            db.add(MessageTemplate(key=key, lang=lang, text=text))


def seed_all(db: Session):
    seed_templates(db)
    db.commit()