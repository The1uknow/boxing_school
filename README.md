# OneBot Kids — школа бокса (FastAPI + Telegram Bot)

Запуск без Docker. БД — SQLite.

## Быстрый старт
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1) Скопируй .env.example -> .env и впиши свой BOT_TOKEN
cp .env.example .env

# 2) Запусти API (веб-сайт)
uvicorn app.main:app --reload

# 3) В отдельном терминале запусти бота
python -m app.bot.run_bot
```

## Что внутри
- FastAPI сайт с шаблонами (Jinja2): лендинг, кабинет родителя/ребёнка, выдача заданий.
- Telegram-бот: регистрация родителей/детей, выдача квиза, подсчёт результатов.
- SQLite: `app.db` в корне проекта.
- I18N: ru/uz (минимальный словарь, легко расширять).
