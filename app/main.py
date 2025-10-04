from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from core.config import settings
from core.db import init_db
from api.lead_routes import router as lead_router
from web.routes_public import router as public_router
from web.routes_parent import router as parent_router


app = FastAPI(title="Boxing School")

# CORS (если фронт отдельно)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # или перечисли конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статика
static_dir = os.path.join(os.path.dirname(__file__), "web", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Роуты
app.include_router(public_router)   # /
app.include_router(parent_router)   # /parent/...
app.include_router(lead_router)     # /api/leads

# Таблицы
init_db()

@app.get("/health")
def health():
    return {"ok": True}