from telebot import types
from app.core.i18n import t

def main_kb(lang: str, has_child: bool = False):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)

    if has_child:
        # после добавления ребёнка
        kb.add(
            types.KeyboardButton(t(lang, "btn_sign")),
            types.KeyboardButton(t(lang, "btn_schedule")),
        )
        kb.add(
            types.KeyboardButton(t(lang, "btn_my_children")),
            types.KeyboardButton(t(lang, "btn_prices")),     # ← цены только после
        )
        kb.add(
            types.KeyboardButton(t(lang, "btn_create_child")),
            types.KeyboardButton(t(lang, "btn_help")),
        )
    else:
        # до добавления ребёнка — убираем «Записаться» и «Цены»
        kb.add(
            types.KeyboardButton(t(lang, "btn_create_child")),
            types.KeyboardButton(t(lang, "btn_schedule")),
        )
        kb.add(
            types.KeyboardButton(t(lang, "btn_my_children")),
            types.KeyboardButton(t(lang, "btn_help")),
        )

    return kb


def step_kb(lang: str):
    # ОДНА версия: Назад + (по желанию) Главный экран
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        types.KeyboardButton(t(lang, "btn_back")),
        types.KeyboardButton(t(lang, "main_menu")),
    )
    return kb


def schedule_inline(lang: str):
    kb = types.InlineKeyboardMarkup()
    for time in ("Пн 17:00", "Ср 17:00", "Пт 17:00"):
        kb.add(types.InlineKeyboardButton(text=time, callback_data=f"sign:{time}"))
    return kb


def lang_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(types.KeyboardButton("Русский"), types.KeyboardButton("O'zbekcha"))
    return kb