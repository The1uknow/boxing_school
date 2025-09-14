from app.core.config import settings
from app.bot.bot import bot

def notify_new_lead(name: str, phone: str, age: str | None, comment: str | None) -> None:
    text = (
        "🔥 Новая заявка с сайта\n\n"
        f"Имя: {name}\nТелефон: {phone}\n"
        + (f"Возраст: {age}\n" if age else "")
        + (f"Комментарий: {comment}\n" if comment else "")
    )
    for chat_id in settings.ADMIN_CHAT_IDS:
        try:
            bot.send_message(chat_id, text)
        except Exception:
            pass