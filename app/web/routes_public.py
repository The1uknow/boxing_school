from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.core.config import settings
from app.core.i18n import get_tx, pick_lang  # берём тексты и выбор языка

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

@router.get("/", response_class=HTMLResponse)
def landing(request: Request, lang: str | None = Query(None)):
    lng = pick_lang(lang or request.query_params.get("lang"), default=settings.DEFAULT_LANG)
    tx = get_tx(lng)
    return templates.TemplateResponse(
        "landing.html",
        {
            "request": request,
            "tx": tx,
            "lang": lng,
            "alt_lang": "uz" if lng == "ru" else "ru",
        },
    )