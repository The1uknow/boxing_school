from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.db import db_session
from app.core.i18n import t
from app.core.models import Parent, Lead

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


def get_parent(db: Session, token: str) -> Parent:
    p = (
        db.query(Parent).filter_by(ref_code=token).first()
        or db.query(Parent).filter_by(tg_id=token).first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Parent not found")
    return p


@router.get("/parent/{token}", response_class=HTMLResponse)
def parent_dashboard(token: str, request: Request, db: Session = Depends(db_session)):
    p = get_parent(db, token)

    # Покажем базовую инфу о родителе и его лидах (заявках)
    leads = db.query(Lead).filter(Lead.parent_id == p.id).order_by(Lead.created_at.desc()).all()

    ctx = {
        "request": request,
        "t": t,
        "lang": p.language,
        "parent": p,
        "leads": leads,
    }
    return templates.TemplateResponse("parent_dashboard.html", ctx)