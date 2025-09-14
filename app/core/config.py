from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # БОТ
    BOT_TOKEN: str = Field("", description="Telegram bot token")
    BOT_USERNAME: str = "boxing_school_bot"
    BASE_URL: str = "http://127.0.0.1:8000"

    # ДБ
    DATABASE_URL: str = "sqlite:///./data/boxing.db"

    # Секреты/админка
    SECRET_KEY: str = "change-me"
    # ↓↓↓ ВАЖНО: теперь это обычные поля BaseSettings, pydantic сам читает из .env
    ADMIN_CHAT_ID: int = 0
    # можно указать в .env строкой "5532256714,-1001234567890"
    ADMIN_CHAT_IDS: List[int] = []  # pydantic корректно распарсит список

    ADMIN_SECRET_KEY: str = "change-admin"
    ADMIN_LOGIN: str = "admin"
    ADMIN_PASSWORD: str = "admin"

    # Локали
    DEFAULT_LANG: str = "ru"

    PAYMENT_DETAILS: str = (
        "Реквизиты для оплаты:\n"
        "Карта Uzcard: 8600 **** **** 1234 (И.О. Фамилия)\n"
        "Комментарий: Оплата за занятия (ФИО ребёнка)"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Единая точка правды: собираем валидный набор ID админов
    @property
    def admin_ids(self) -> list[int]:
        ids: set[int] = set()
        try:
            if self.ADMIN_CHAT_ID:
                ids.add(int(self.ADMIN_CHAT_ID))
        except Exception:
            pass
        for x in (self.ADMIN_CHAT_IDS or []):
            try:
                ids.add(int(x))
            except Exception:
                continue
        return list(ids)

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()