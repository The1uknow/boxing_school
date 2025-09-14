from fastapi import APIRouter, Depends, status, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator, ConfigDict
from datetime import datetime, timedelta
from sqlalchemy import and_
from typing import Optional
from app.core.db import get_db
from app.core.models import Lead
from app.services.telegram_notify import notify_new_lead

router = APIRouter(prefix="/api", tags=["leads"])

# ====== схемы ======
class LeadCreate(BaseModel):
    # обязательные
    name: str
    phone: str

    # опциональные
    age: Optional[int] = None        # <— число; строку тоже примем и сконвертим
    tg_username: Optional[str] = None
    comment: Optional[str] = None
    source: Optional[str] = "site"
    ref_code: Optional[str] = ""

    # игнорировать лишние ключи в JSON, чтобы не падать 422
    model_config = ConfigDict(extra="ignore")

    @field_validator("name", "phone")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        return v.replace(" ", "").replace("-", "")

    @field_validator("tg_username")
    @classmethod
    def normalize_tg(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = v.strip().lstrip("@")
        return v or None

    @field_validator("age", mode="before")
    @classmethod
    def age_accepts_str(cls, v):
        # Приходит "", None, "12", 12 → превращаем в int или None
        if v in ("", None):
            return None
        try:
            return int(v)
        except Exception:
            raise ValueError("age must be an integer")


class LeadOut(BaseModel):
    id: int
    name: str
    phone: str
    comment: str | None = None
    age: str | None = None
    source: str | None = None
    ref_code: str | None = None
    status: str
    processed: bool
    created_at: datetime
    tg_username: str | None = None  # НОВОЕ

    model_config = {"from_attributes": True}


# ====== helpers ======
def _lead_kwargs(payload: LeadCreate) -> dict:
    """Подстраиваемся под текущую модель/БД."""
    kw = {
        "phone": payload.phone,
        "source": payload.source or "site",
        "ref_code": payload.ref_code or "",
        "status": "new",
        "processed": False,
    }
    # name / full_name
    if hasattr(Lead, "full_name"):
        kw["full_name"] = payload.name
    else:
        kw["name"] = payload.name

    # comment / note
    if hasattr(Lead, "note"):
        kw["note"] = payload.comment or ""
    elif hasattr(Lead, "comment"):
        kw["comment"] = payload.comment or ""

    # age — если колонка есть
    if hasattr(Lead, "age") and payload.age is not None:
        kw["age"] = payload.age

    # tg_username — если колонка есть
    if hasattr(Lead, "tg_username") and payload.tg_username is not None:
        kw["tg_username"] = payload.tg_username

    return kw


def _to_out(lead: Lead) -> LeadOut:
    """Единый ответ независимо от колонок."""
    name = getattr(lead, "full_name", None) or getattr(lead, "name", "")
    comment = getattr(lead, "note", None) if hasattr(lead, "note") else getattr(lead, "comment", None)
    age = getattr(lead, "age", None) if hasattr(lead, "age") else None
    tg_username = getattr(lead, "tg_username", None) if hasattr(lead, "tg_username") else None
    return LeadOut(
        id=lead.id,
        name=name,
        phone=lead.phone,
        comment=comment,
        age=age,
        source=getattr(lead, "source", None),
        ref_code=getattr(lead, "ref_code", None),
        status=getattr(lead, "status", "new"),
        processed=getattr(lead, "processed", False),
        created_at=getattr(lead, "created_at"),
        tg_username=tg_username,
    )


# ====== JSON endpoint ======
@router.post("/leads", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    # (необязательно) анти-дубль: та же труба за последние 60 минут в статусе new
    try:
        one_hour_ago = datetime.utcnow() - timedelta(minutes=60)
        dup = (
            db.query(Lead)
            .filter(
                and_(
                    Lead.phone == payload.phone,
                    getattr(Lead, "status", "new") == "new",
                    getattr(Lead, "created_at", one_hour_ago) >= one_hour_ago,
                )
            )
            .first()
        )
        if dup:
            return _to_out(dup)
    except Exception:
        # если в модели нет status/created_at — тихо пропускаем
        pass

    lead = Lead(**_lead_kwargs(payload))
    db.add(lead)
    db.commit()          # фиксируем изменения
    db.refresh(lead)     # подтягиваем дефолты/таймстампы

    try:
        # уведомляем уже ПОСЛЕ успешного коммита
        notify_new_lead(lead)
    except Exception:
        pass

    return _to_out(lead)


# ====== endpoint для формы (x-www-form-urlencoded) ======
@router.post("/leads-form", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead_form(
    name: str = Form(...),
    phone: str = Form(...),
    age: str | None = Form(None),
    tg_username: str | None = Form(None),   # НОВОЕ
    comment: str | None = Form(None),       # для обратной совместимости
    db: Session = Depends(get_db),
):
    payload = LeadCreate(
        name=name,
        phone=phone,
        age=age,
        comment=comment,
        tg_username=tg_username,
        source="site",
    )
    return create_lead(payload, db)