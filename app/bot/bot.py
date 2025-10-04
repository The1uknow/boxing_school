from telebot import TeleBot, types, apihelper
from core.config import settings
from core.db import Base, engine, db_session
from core.i18n import t
from core.utils import get_or_create_parent, add_child, list_children, create_appointment
from core.models import Parent, Child
from datetime import datetime, timedelta, UTC
from sqlalchemy import text as sql_text
import threading, time, traceback, requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# невидимый разделитель сменить клаву не выводя текст
_ZWSP = '\u2063'


# Сессия requests с ретраями и backoff
_session = requests.Session()
_retry = Retry(
    total=5,
    connect=5,
    read=5,
    backoff_factor=0.5,            # 0.5s, 1s, 2s, ...
    status_forcelist=(429, 500, 502, 503, 504),
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retry, pool_connections=100, pool_maxsize=100)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

# Подсовываем сессию и таймауты самому apihelper
apihelper.SESSION = _session
apihelper.READ_TIMEOUT = 120
apihelper.CONNECT_TIMEOUT = 10

# Telegram allowed_updates — contact приходит в типе message, отдельный тип не нужен
_ALLOWED_UPDATES = ["message", "callback_query"]


# ──────────────────────────────
# Таблицы (на случай отдельного запуска)
# ──────────────────────────────
Base.metadata.create_all(bind=engine)

# ──────────────────────────────
# Грубая миграция недостающих колонок (SQLite) — без Alembic
# ──────────────────────────────
def _ensure_children_columns():
    with engine.begin() as conn:
        # parents.phone
        pcols = {row[1] for row in conn.execute(sql_text("PRAGMA table_info(parents)"))}
        if "phone" not in pcols:
            conn.execute(sql_text("ALTER TABLE parents ADD COLUMN phone VARCHAR"))

        # children.*
        ccols = {row[1] for row in conn.execute(sql_text("PRAGMA table_info(children)"))}
        if "tg_id" not in ccols:
            conn.execute(sql_text("ALTER TABLE children ADD COLUMN tg_id VARCHAR"))
        if "schedule_text" not in ccols:
            conn.execute(sql_text("ALTER TABLE children ADD COLUMN schedule_text TEXT"))
        if "paid" not in ccols:
            conn.execute(sql_text("ALTER TABLE children ADD COLUMN paid BOOLEAN DEFAULT 0"))
        if "phone" not in ccols:
            conn.execute(sql_text("ALTER TABLE children ADD COLUMN phone VARCHAR"))

_ensure_children_columns()

# ──────────────────────────────
# Токен бота
# ──────────────────────────────
if not settings.BOT_TOKEN or settings.BOT_TOKEN.startswith("000000000"):
    raise RuntimeError("BOT_TOKEN не задан. Заполни .env по образцу .env.example")

bot = TeleBot(settings.BOT_TOKEN, parse_mode="HTML", threaded=True)

# ──────────────────────────────
# FSM + антидубль входящих (+ анти‑дабл старт)
# ──────────────────────────────
STATE = {}  # {tg_id: {"step": str, ...}}

_TTL_SECONDS = 120
_SEEN_LOCK = threading.Lock()
SEEN_MSG = {}       # {(chat_id, message_id): ts}
SEEN_CALLBACK = {}  # {callback_id: ts}
LAST_START_AT = {}  # {tg_id: ts}

def _now() -> float: return time.time()

def _gc_seen():
    now = _now()
    for d in (SEEN_MSG, SEEN_CALLBACK):
        stale = [k for k, ts in d.items() if now - ts > _TTL_SECONDS]
        for k in stale:
            d.pop(k, None)

def _seen_message(m: types.Message) -> bool:
    key = (m.chat.id, m.message_id)
    with _SEEN_LOCK:
        _gc_seen()
        if key in SEEN_MSG:
            return True
        SEEN_MSG[key] = _now()
    return False

def _seen_callback(c: types.CallbackQuery) -> bool:
    key = c.id
    with _SEEN_LOCK:
        _gc_seen()
        if key in SEEN_CALLBACK:
            return True
        SEEN_CALLBACK[key] = _now()
    return False

def _set(uid, **data): STATE[uid] = {**STATE.get(uid, {}), **data}
def _get(uid): return STATE.get(uid, {})
def _clear(uid): STATE.pop(uid, None)

# ──────────────────────────────
# ---- утилиты ----
def _normalize_phone(s: str) -> str:
    s = (s or "").strip()
    keep = []
    for i, ch in enumerate(s):
        if ch.isdigit() or (i == 0 and ch == "+"):
            keep.append(ch)
    return "".join(keep)

def _looks_like_phone(s: str) -> bool:
    s = _normalize_phone(s)
    return len(s) >= 7  # простая валидация

def _set_child_phone(child_id: int, phone: str):
    with engine.begin() as conn:
        conn.execute(sql_text("UPDATE children SET phone=:p WHERE id=:id"), {"p": phone, "id": child_id})

def _first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    if not parts:
        return ""
    name = parts[0]
    try:
        return name[:1].upper() + name[1:]
    except Exception:
        return name

def _admin_ids() -> list[int]:
    """
    Собираем все tg-id админов из конфигурации.
    Поддерживаются:
      - settings.ADMIN_CHAT_ID (int или str)
      - settings.ADMIN_CHAT_IDS (list[int] | tuple | set | str CSV | JSON-строка)
    """
    ids: set[int] = set()

    def _push(val):
        try:
            n = int(str(val).strip())
            if n > 0:
                ids.add(n)
        except Exception:
            pass

    # 1) одиночный ID
    single = getattr(settings, "ADMIN_CHAT_ID", None)
    if single not in (None, "", 0, "0"):
        _push(single)

    # 2) набор ID разными форматами
    many = getattr(settings, "ADMIN_CHAT_IDS", None)
    if many:
        # если это уже коллекция чисел
        if isinstance(many, (list, tuple, set)):
            for v in many:
                _push(v)
        else:
            s = str(many).strip()
            if s.startswith("[") and s.endswith("]"):
                # попытка распарсить JSON-подобный список
                try:
                    import json
                    arr = json.loads(s)
                    if isinstance(arr, list):
                        for v in arr:
                            _push(v)
                except Exception:
                    pass
            else:
                # поддержка "1,2,3", "1; 2; 3" и т.п.
                for chunk in s.replace(";", ",").split(","):
                    if chunk.strip():
                        _push(chunk)

    return sorted(ids)

# ──────────────────────────────
# Клавиатуры
# ──────────────────────────────

def step_kb(lang: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(t(lang, "btn_back")))
    kb.add(types.KeyboardButton(t(lang, "main_menu")))
    return kb

def lang_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Русский"), types.KeyboardButton("O'zbekcha"))
    return kb

def phone_kb(lang: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(t(lang, "btn_share_phone"), request_contact=True))
    kb.add(types.KeyboardButton(t(lang, "btn_back")))
    kb.add(types.KeyboardButton(t(lang, "main_menu")))
    return kb

def kid_phone_kb(lang: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(t(lang, "btn_share_phone"), request_contact=True))
    kb.add(types.KeyboardButton(t(lang, "btn_back")))
    return kb

def no_child_kb(lang: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(t(lang, "btn_create_child")))
    kb.add(types.KeyboardButton(t(lang, "btn_help")))
    kb.add(types.KeyboardButton(t(lang, "btn_back")))
    return kb

def child_added_kb(lang: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(t(lang, "btn_sign")))
    kb.add(types.KeyboardButton(t(lang, "btn_help")))
    kb.add(types.KeyboardButton(t(lang, "btn_back")))
    return kb

def after_sign_kb(lang: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(t(lang, "main_menu")))
    return kb

def main_parent_kb(lang: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        types.KeyboardButton(t(lang, "btn_sign")),
        types.KeyboardButton(t(lang, "btn_schedule"))
    )
    kb.add(
        types.KeyboardButton(t(lang, "btn_prices")),
        types.KeyboardButton(t(lang, "btn_my_children"))
    )
    kb.add(types.KeyboardButton(t(lang, "btn_create_child")))
    kb.add(types.KeyboardButton(t(lang, "btn_pay")))
    kb.add(types.KeyboardButton(t(lang, "btn_help")))
    return kb

def kid_main_kb(lang: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(t(lang, "kid_schedule")))
    kb.add(types.KeyboardButton(t(lang, "kid_help")))
    return kb

def schedule_inline(lang: str):
    kb = types.InlineKeyboardMarkup()
    for time_label in ("Пн 17:00", "Ср 17:00", "Пт 17:00"):
        kb.add(types.InlineKeyboardButton(text=time_label, callback_data=f"sign:{time_label}"))
    return kb

# ──────────────────────────────
# Вспомогалки
# ──────────────────────────────

def _find_child_by_tg(tg_id: int):
    with db_session() as db:
        return db.query(Child).filter(Child.tg_id == str(tg_id)).first()

def _has_child_for(user_id: int) -> bool:
    with db_session() as db:
        p = db.query(Parent).filter_by(tg_id=str(user_id)).first()
        if not p:
            return False
        return db.query(Child).filter_by(parent_id=p.id).first() is not None

def _parent_menu_for(user_id: int, lang: str):
    st = _get(user_id).get("step")
    if st == "after_sign":
        return after_sign_kb(lang)
    if not _has_child_for(user_id):
        return no_child_kb(lang)
    return main_parent_kb(lang)

def _send_main_menu(chat_id: int, lang: str, is_kid: bool = False, greet_name: str = ""):
    if is_kid:
        text = f"Привет, {greet_name}!" if greet_name else _ZWSP
        safe_send_message(chat_id, text, reply_markup=kid_main_kb(lang))
    else:
        text = f"Привет, {greet_name}!" if greet_name else t(lang, "main_menu")
        safe_send_message(chat_id, text, reply_markup=_parent_menu_for(chat_id, lang))


def safe_send_message(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except requests.exceptions.ReadTimeout:
        try:
            return bot.send_message(chat_id, text, **kwargs)
        except Exception as e:
            print(f"safe_send_message retry failed: chat_id={chat_id}, err={e!r}")
            return None
    except Exception as e:
        print(f"safe_send_message failed: chat_id={chat_id}, err={e!r}")
        return None


def safe_edit_message_text(text, chat_id, message_id, **kwargs):
    try:
        return bot.edit_message_text(text, chat_id, message_id, **kwargs)
    except requests.exceptions.ReadTimeout:
        try:
            return bot.edit_message_text(text, chat_id, message_id, **kwargs)
        except Exception:
            return None
    except Exception:
        return None

# ──────────────────────────────
# /start (+ поддержка /start <ID_РЕБЁНКА>)
# ──────────────────────────────
@bot.message_handler(commands=["start"])
def on_start(m: types.Message):
    if _seen_message(m):
        return
    last = LAST_START_AT.get(m.from_user.id, 0)
    if _now() - last < 1:
        return
    LAST_START_AT[m.from_user.id] = _now()

    try:
        # ── 1) Если это уже ПРИВЯЗАННЫЙ РЕБЁНОК — восстанавливаем его меню/шаг
        with db_session() as db:
            kid = db.query(Child).filter(Child.tg_id == str(m.from_user.id)).first()
            if kid:
                parent = db.query(Parent).filter(Parent.id == kid.parent_id).first()
                lang_local = parent.language if parent else settings.DEFAULT_LANG

                # если телефон ещё не сохранён — вернёмся в шаг запроса телефона
                if not (getattr(kid, "phone", "") or "").strip():
                    safe_send_message(
                        m.chat.id,
                        t(lang_local, "child_linked_child").format(name=kid.name),
                        reply_markup=kid_phone_kb(lang_local)
                    )
                    _set(m.from_user.id, step="kid:phone", child_id=kid.id, lang=lang_local)
                else:
                    # иначе — просто показать детское главное меню (кнопки ребёнка)
                    safe_send_message(m.chat.id, _ZWSP, reply_markup=kid_main_kb(lang_local))
                    _clear(m.from_user.id)
                return


        ref_code = ""
        arg = None
        if m.text and " " in m.text:
            arg = m.text.split(" ", 1)[1][:64]

            # Детская привязка по ID
            if arg and arg.isdigit():
                with db_session() as db:
                    child = db.query(Child).filter(Child.id == int(arg)).first()
                    if child:
                        parent = db.query(Parent).filter(Parent.id == child.parent_id).first()
                        lang_local = parent.language if parent else settings.DEFAULT_LANG

                        # 0) защита от дурака — родитель нажал ссылку сам
                        if parent and str(m.from_user.id) == (parent.tg_id or ""):
                            safe_send_message(
                                m.chat.id,
                                t(lang_local, "link_is_for_child")
                            )
                            return

                        # 1) привязываем ребёнка
                        child.has_telegram = True
                        child.tg_id = str(m.from_user.id)
                        child_name = child.name

                        # 2) уведомляем РОДИТЕЛЯ
                        if parent and parent.tg_id:
                            try:
                                safe_send_message(
                                    parent.tg_id,
                                    t(lang_local, "child_linked_parent").format(name=child_name)
                                )
                            except Exception:
                                pass

                        # 3) приветствуем РЕБЁНКА и просим телефон
                        safe_send_message(
                            m.chat.id,
                            t(lang_local, "child_linked_child").format(name=child_name),
                            reply_markup=kid_phone_kb(lang_local)
                        )
                        _set(m.from_user.id, step="kid:phone", child_id=child.id, lang=lang_local)
                        return
                ref_code = arg  # не нашли ребёнка — трактуем как реф-код

        # Обычный старт (родитель)
        with db_session() as db:
            parent = db.query(Parent).filter_by(tg_id=str(m.from_user.id)).first()
            if not parent:
                safe_send_message(m.chat.id, "Выберите язык / Tilni tanlang", reply_markup=lang_kb())
                _set(m.from_user.id, step="choose_lang", ref_code=ref_code)
                return
            lang_local = parent.language

            if not (parent.full_name or "").strip():
                safe_send_message(m.chat.id, t(lang_local, "ask_parent_name"), reply_markup=step_kb(lang_local))
                _set(m.from_user.id, step="parent:name", lang=lang_local)
                return

        name = _first_name(parent.full_name)
        _send_main_menu(m.chat.id, lang_local, greet_name=name)

    except Exception:
        print("on_start error:\n", traceback.format_exc())

@bot.message_handler(content_types=["contact"])
def on_contact(m: types.Message):
    try:
        st = _get(m.from_user.id)
        lang = settings.DEFAULT_LANG
        with db_session() as db:
            pr = db.query(Parent).filter_by(tg_id=str(m.from_user.id)).first()
            if pr:
                lang = pr.language

        phone = _normalize_phone(getattr(m.contact, "phone_number", ""))

        if st.get("step") == "parent:phone":
            if not _looks_like_phone(phone):
                safe_send_message(m.chat.id, t(lang, "ask_phone_retry"), reply_markup=phone_kb(lang))
                return
            with db_session() as db:
                p = get_or_create_parent(db, str(m.from_user.id), lang=lang)
                p.phone = phone
                fname = p.full_name
            _clear(m.from_user.id)
            _send_main_menu(m.chat.id, lang, greet_name=_first_name(fname))
            return

        if st.get("step") == "kid:phone":
            child_id = st.get("child_id")
            if not _looks_like_phone(phone):
                safe_send_message(m.chat.id, t(lang, "ask_phone_retry"), reply_markup=kid_phone_kb(lang))
                return
            _set_child_phone(child_id, phone)
            _clear(m.from_user.id)
            safe_send_message(m.chat.id, _ZWSP, reply_markup=kid_main_kb(lang))
            return
    except Exception:
        print("on_contact error:\n", traceback.format_exc())

@bot.message_handler(commands=["menu"])
def on_menu(m: types.Message):
    if _seen_message(m):
        return
    try:
        kid = _find_child_by_tg(m.from_user.id)
        if kid:
            parent_lang = settings.DEFAULT_LANG
            with db_session() as db:
                p = db.query(Parent).filter(Parent.id == kid.parent_id).first()
                if p:
                    parent_lang = p.language
            _clear(m.from_user.id)
            _send_main_menu(m.chat.id, parent_lang, is_kid=True)
            return

        with db_session() as db:
            parent = get_or_create_parent(db, str(m.from_user.id), lang=settings.DEFAULT_LANG)
            lang = parent.language
        _clear(m.from_user.id)
        _send_main_menu(m.chat.id, lang, greet_name=_first_name(parent.full_name))
    except Exception:
        print("on_menu error:\n", traceback.format_exc())

# ──────────────────────────────
# Выбор языка при первом запуске (родитель)
# ──────────────────────────────
@bot.message_handler(func=lambda msg: _get(msg.from_user.id).get("step") == "choose_lang")
def choose_lang(m: types.Message):
    if _seen_message(m):
        return
    try:
        lang = "ru" if "Рус" in (m.text or "") else "uz"
        ref_code = _get(m.from_user.id).get("ref_code", "")

        with db_session() as db:
            parent = get_or_create_parent(db, str(m.from_user.id), lang=lang)
            parent.language = lang  # фикс
        safe_send_message(m.chat.id, t(lang, "ask_parent_name"), reply_markup=step_kb(lang))
        _set(m.from_user.id, step="parent:name", lang=lang, ref_code=ref_code)
    except Exception:
        print("choose_lang error:\n", traceback.format_exc())

# ──────────────────────────────
# ОБРАБОТЧИК ТЕКСТА
# ──────────────────────────────
@bot.message_handler(content_types=["text"])
def on_text(m: types.Message):
    if _seen_message(m):
        return
    try:
        txt = (m.text or "").strip()

        # --- игнорируем команды ---
        if txt.startswith("/"):
            return

            # Ребёнок
        kid = _find_child_by_tg(m.from_user.id)
        if kid:
            return _handle_kid_text(m, kid)

        # Родитель
        with db_session() as db:
            parent = get_or_create_parent(db, str(m.from_user.id), lang=settings.DEFAULT_LANG)
            lang = parent.language
        parent_name = _first_name(parent.full_name)

        # FSM
        st = _get(m.from_user.id)
        step = st.get("step")

        if txt == t(lang, "btn_back"):
            if step in ("child:age", "support:ask", "parent:name"):
                if step == "child:age":
                    _set(m.from_user.id, step="child:name", lang=lang)
                    safe_send_message(m.chat.id, t(lang, "ask_child_name"), reply_markup=step_kb(lang))
                    return
                _clear(m.from_user.id)
                _send_main_menu(m.chat.id, lang, greet_name=parent_name)
                return
            if step == "after_sign":
                _clear(m.from_user.id)
                safe_send_message(m.chat.id, t(lang, "sign_when"), reply_markup=schedule_inline(lang))
                return
            _clear(m.from_user.id)
            _send_main_menu(m.chat.id, lang, greet_name=parent_name)
            return

        if step == "parent:name":
            full_name = txt[:24]
            with db_session() as db:
                p = get_or_create_parent(db, str(m.from_user.id), lang=lang)
                p.full_name = full_name
            safe_send_message(m.chat.id, t(lang, "ask_parent_phone"), reply_markup=phone_kb(lang))
            _set(m.from_user.id, step="parent:phone", lang=lang)
            return

        # ---- родитель вводит телефон вручную ----
        if step == "parent:phone":
            if _looks_like_phone(txt):
                phone = _normalize_phone(txt)
                with db_session() as db:
                    p = get_or_create_parent(db, str(m.from_user.id), lang=lang)
                    p.phone = phone
                    fname = p.full_name
                _clear(m.from_user.id)
                _send_main_menu(m.chat.id, lang, greet_name=_first_name(fname))
            else:
                safe_send_message(m.chat.id, t(lang, "ask_phone_retry"), reply_markup=phone_kb(lang))
            return

        if step == "child:name":
            _set(m.from_user.id, step="child:age", child_name=txt[:80], lang=lang)
            safe_send_message(m.chat.id, t(lang, "ask_child_age"), reply_markup=step_kb(lang))
            return

        if step == "child:age":
            try:
                age = int(txt)
                if not (5 <= age <= 25):
                    raise ValueError
            except Exception:
                safe_send_message(m.chat.id, "Введите возраст числом", reply_markup=step_kb(lang))
                return

            child_name = _get(m.from_user.id).get("child_name", "Ребёнок")
            with db_session() as db:
                parent = get_or_create_parent(db, str(m.from_user.id), lang=settings.DEFAULT_LANG)
                lang_local = parent.language
                recent = (db.query(Child)
                          .filter(Child.parent_id == parent.id,
                                  Child.name == child_name,
                                  Child.age == age,
                                  Child.created_at > datetime.now(UTC) - timedelta(seconds=90))
                          .order_by(Child.id.desc())
                          .first())
                ch = recent or add_child(db, parent, child_name, age)

            safe_send_message(
                m.chat.id,
                (
                    f"Готово! Ребёнок <b>{child_name}</b> сохранён ✅\n"
                    f"ID ребёнка: <code>{ch.id}</code>\n"
                    f"Ссылку для привязки отправьте ребёнку и откройте с ЕГО устройства:\n"
                    f"<code>t.me/{settings.BOT_USERNAME}?start={ch.id}</code>"
                ),
                reply_markup=child_added_kb(lang_local),
            )
            _clear(m.from_user.id)
            return

        if step == "support:ask":
            question = txt
            for admin_id in _admin_ids():
                try:
                    safe_send_message(
                        admin_id,
                        f"🆘 Вопрос от родителя tg={m.from_user.id}:\n\n{question}"
                    )
                except Exception:
                    pass
            safe_send_message(m.chat.id, "✅ Сообщение отправлено тренеру.",
                              reply_markup=_parent_menu_for(m.from_user.id, lang))
            _clear(m.from_user.id)
            return

        # Глобальные кнопки
        if txt in ("Русский", "O'zbekcha"):
            lang = "ru" if "Рус" in txt else "uz"
            with db_session() as db:
                get_or_create_parent(db, str(m.from_user.id), lang=lang)
            _send_main_menu(m.chat.id, lang, greet_name=_first_name(parent.full_name))
            return

        if txt == t(lang, "main_menu"):
            _clear(m.from_user.id)
            _send_main_menu(m.chat.id, lang, greet_name=parent_name)
            return

        if txt in (t(lang, "btn_sign"), t(lang, "btn_prices"), t(lang, "btn_pay")) and not _has_child_for(m.from_user.id):
            safe_send_message(m.chat.id, "Сначала добавьте ребёнка 🙂", reply_markup=step_kb(lang))
            safe_send_message(m.chat.id, t(lang, "ask_child_name"), reply_markup=step_kb(lang))
            _set(m.from_user.id, step="child:name", lang=lang)
            return

        if txt == t(lang, "btn_prices"):
            safe_send_message(m.chat.id, t(lang, "prices_text"),
                              reply_markup=_parent_menu_for(m.from_user.id, lang))
            return

        if txt == t(lang, "btn_schedule"):
            parts = [t(lang, "schedule_text")]
            with db_session() as db:
                parent = get_or_create_parent(db, str(m.from_user.id), lang=lang)
                kids = list_children(db, parent)

            if kids:
                parts.append("")
                parts.append(t(lang, "my_kids_schedule_title"))
                for c in kids:
                    paid = 1 if getattr(c, "paid", 0) else 0
                    sched = (getattr(c, "schedule_text", "") or "").strip()
                    if paid and sched:
                        line = f"• {c.name}: {sched}"
                    elif not paid:
                        line = f"• {c.name}: {t(lang, 'sched_wait_payment')}"
                    else:
                        line = f"• {c.name}: {t(lang, 'sched_not_set')}"
                    parts.append(line)

            safe_send_message(m.chat.id, "\n".join(parts), reply_markup=_parent_menu_for(m.from_user.id, lang))
            return

        if txt == t(lang, "btn_create_child"):
            safe_send_message(m.chat.id, t(lang, "ask_child_name"), reply_markup=step_kb(lang))
            _set(m.from_user.id, step="child:name", lang=lang)
            return

        if txt == t(lang, "btn_my_children"):
            with db_session() as db:
                parent = get_or_create_parent(db, str(m.from_user.id), lang=lang)
                kids = list_children(db, parent)
            if not kids:
                safe_send_message(m.chat.id, "Пока нет добавленных детей.",
                                  reply_markup=_parent_menu_for(m.from_user.id, lang))
            else:
                msg = "\n".join([f"• {c.name}, {c.age} лет — ID: <code>{c.id}</code>" for c in kids])
                safe_send_message(m.chat.id, msg, reply_markup=_parent_menu_for(m.from_user.id, lang))
            return

        if txt == t(lang, "btn_pay"):
            safe_send_message(m.chat.id, settings.PAYMENT_DETAILS,
                              reply_markup=_parent_menu_for(m.from_user.id, lang))
            return

        if txt == t(lang, "btn_sign"):
            safe_send_message(m.chat.id, t(lang, "sign_when"), reply_markup=schedule_inline(lang))
            return

        if txt == t(lang, "btn_help"):
            safe_send_message(m.chat.id, t(lang, "help_text"), reply_markup=step_kb(lang))
            _set(m.from_user.id, step="support:ask", lang=lang)
            return

        _send_main_menu(m.chat.id, lang, greet_name=parent_name)

    except Exception:
        print("on_text error:\n", traceback.format_exc())


@bot.message_handler(commands=["whoami"])
def whoami(m):
    bot.reply_to(m, f"Твой ID: {m.from_user.id}\nИмя: {m.from_user.first_name}")


# ──────────────────────────────
# Детский обработчик текста
# ──────────────────────────────
def _handle_kid_text(m: types.Message, kid: Child):
    try:
        lang = settings.DEFAULT_LANG
        with db_session() as db:
            p = db.query(Parent).filter(Parent.id == kid.parent_id).first()
            if p:
                lang = p.language

        txt = (m.text or "").strip()

        if txt == t(lang, "kid_schedule"):
            status = getattr(kid, "paid", 0)
            sched = (kid.schedule_text or "").strip() if getattr(kid, "schedule_text", None) is not None else ""
            if not status:
                safe_send_message(m.chat.id, "Вы записаны на пробное занятие. После оплаты тренер установит расписание.",
                                  reply_markup=kid_main_kb(lang))
            else:
                safe_send_message(m.chat.id, sched or "Расписание пока пустое — уточните у тренера.",
                                  reply_markup=kid_main_kb(lang))
            return

        if txt == t(lang, "kid_help"):
            safe_send_message(m.chat.id, "Напиши свой вопрос. Мы передадим его тренеру.", reply_markup=step_kb(lang))
            _set(m.from_user.id, step="kid:support", lang=lang)
            return

        st = _get(m.from_user.id)
        if txt == t(lang, "btn_back"):
            _clear(m.from_user.id)
            safe_send_message(m.chat.id, t(lang, "main_menu"), reply_markup=kid_main_kb(lang))
            return

        if st.get("step") == "kid:phone":
            if _looks_like_phone(txt):
                _set_child_phone(kid.id, _normalize_phone(txt))
                _clear(m.from_user.id)
                safe_send_message(m.chat.id, _ZWSP, reply_markup=kid_main_kb(lang))
            else:
                safe_send_message(m.chat.id, t(lang, "ask_phone_retry"), reply_markup=kid_phone_kb(lang))
            return

        if st.get("step") == "kid:support":
            question = txt

            # данные ребёнка
            child_name = (kid.name or "").strip() or "—"
            child_phone = (getattr(kid, "phone", "") or "").strip() or "—"

            # username/ссылка на профиль
            uname = (getattr(m.from_user, "username", "") or "").strip()
            if uname:
                tg_line = f"Telegram: @{uname} (id={m.from_user.id})"
            else:
                # у нас parse_mode="HTML", можно дать кликабельную ссылку
                tg_line = f'Telegram: <a href="tg://user?id={m.from_user.id}">профиль</a> (id={m.from_user.id})'

            msg = (
                "🧒 <b>Вопрос от ребёнка</b>\n"
                f"Имя: {child_name}\n"
                f"Телефон: {child_phone}\n"
                f"{tg_line}\n\n"
                f"Вопрос: {question}"
            )

            for admin_id in _admin_ids():
                try:
                    safe_send_message(admin_id, msg)
                except Exception:
                    pass

            safe_send_message(m.chat.id, "✅ Сообщение отправлено тренеру.", reply_markup=kid_main_kb(lang))
            _clear(m.from_user.id)
            return

        safe_send_message(m.chat.id, t(lang, "main_menu"), reply_markup=kid_main_kb(lang))

    except Exception:
        print("_handle_kid_text error:\n", traceback.format_exc())

# ──────────────────────────────
# Callback — запись на пробное (родитель)
# ──────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("sign:"))
def cb_sign(call: types.CallbackQuery):
    if _seen_callback(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return
    try:
        time_str = call.data.split(":", 1)[1]
        with db_session() as db:
            parent = db.query(Parent).filter_by(tg_id=str(call.from_user.id)).first()
            if not parent:
                bot.answer_callback_query(call.id, "Сначала нажмите /start"); return
            kids = list_children(db, parent)
            if not kids:
                bot.answer_callback_query(call.id, "Сначала добавьте ребёнка"); return
            lang_local = parent.language
            create_appointment(db, child_id=kids[0].id, datetime_str=time_str)

        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "OK")

        _set(call.from_user.id, step="after_sign", lang=lang_local)
        safe_send_message(
            call.message.chat.id,
            t(lang_local, "sign_done"),
            reply_markup=after_sign_kb(lang_local)
        )
    except Exception:
        print("cb_sign error:\n", traceback.format_exc())

# ──────────────────────────────
# Запуск
# ──────────────────────────────

def _run_polling():
    # устойчивый цикл с перезапуском при сетевых таймаутах/ошибках
    while True:
        try:
            bot.infinity_polling(
                timeout=120,                # сетевой таймаут requests
                long_polling_timeout=60,    # сервер держит соединение до N сек.
                skip_pending=True,
                allowed_updates=_ALLOWED_UPDATES,
            )
        except requests.exceptions.ReadTimeout:
            print("polling ReadTimeout — retry in 2s")
            time.sleep(2)
            continue
        except requests.exceptions.ConnectTimeout:
            print("polling ConnectTimeout — retry in 3s")
            time.sleep(3)
            continue
        except Exception as e:
            print("polling crashed:", repr(e))
            time.sleep(5)
            continue


if __name__ == "__main__":
    print("Bot is running…")
    _run_polling()