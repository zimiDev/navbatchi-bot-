"""
Navbatchilik boti (python-telegram-bot v21+)

O'rnatish:
    pip install "python-telegram-bot[job-queue]>=21"

Ishga tushirish:
    export BOT_TOKEN="123:ABC..."
    export ADMIN_IDS="11111111,22222222"
    python bot.py
"""
import json
import logging
import os
import random
import re
from functools import wraps
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:  # .env fayl bo'lsa o'qiydi (ko'p tekin hostinglarda kerak)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(value.strip())
    for value in os.environ.get("ADMIN_IDS", "7321457976").split(",")
    if value.strip().isdigit()
}
TZ = ZoneInfo("Asia/Tashkent")
ANNOUNCE_AT = time(8, 0, tzinfo=TZ)  # har kuni ertalab 08:00
SHIFT_DAYS = 1  # bajarilmasa navbat ertasi kuni ham shu odamda qoladi
STATE_FILE = os.environ.get("STATE_FILE", "state.json")  # doimiy disk yo'li berish mumkin
MEMBERS_FILE = "members.json"

KNOWN_COMMANDS = {
    "start", "setup", "bugun", "jadval", "bajarildi", "admin",
}

with open(MEMBERS_FILE, encoding="utf-8") as f:
    MEMBERS = json.load(f)


# ---------- holat ----------
# state = {
#   "chat_id": int,
#   "order": [member indekslari]   # bir marta aralashtirilgan
#   "pos": hozirgi navbatchi order dagi o'rni,
#   "duty_start": "YYYY-MM-DD"     # hozirgi navbatchining boshlanish sanasi
#   "tasks": [str, ...]
# }
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(s: dict) -> None:
    temp_file = f"{STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, STATE_FILE)


def delete_command_message(handler):
    """Buyruq bajarilgandan keyin uning xabarini o'chirishga urinadi."""
    @wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await handler(update, context)
        finally:
            message = update.effective_message
            user = update.effective_user
            if message and user:
                try:
                    await message.delete()
                except TelegramError as exc:
                    # Guruhlarda botga Delete messages huquqi kerak.
                    logging.warning("Buyruq xabarini o'chirib bo'lmadi: %s", exc)
    return wrapped


def today() -> date:
    return datetime.now(TZ).date()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def label(i: int) -> str:
    m = MEMBERS[i]
    username = (m.get("username") or "").strip().lstrip("@")
    tag = f" (@{username})" if username else ""
    return f"{m['name']}{tag}"


def username(i: int) -> str:
    return (MEMBERS[i].get("username") or "").strip().lstrip("@")


def fmt_range(st: date, en: date) -> str:
    if st == en:
        return st.strftime("%d.%m.%Y")
    return f"{st.strftime('%d.%m.%Y')} dan {en.strftime('%d.%m.%Y')} gacha"


# ---------- jadval ----------
def current_end(s: dict) -> date:
    """Hozirgi navbat tugagan keyingi kun (exclusive).
    Admin bajarildi demasa navbat har kuni 1 kunga cho'ziladi."""
    ds = date.fromisoformat(s["duty_start"])
    d = today()
    normal_end = ds + timedelta(days=SHIFT_DAYS)
    if d >= normal_end:
        return d + timedelta(days=1)  # cho'zilgan
    return normal_end


def schedule(s: dict):
    """[(boshlanish, tugash, member_index)] — hozirgidan boshlab hamma uchun."""
    n = len(s["order"])
    if not n:
        return []
    ds = date.fromisoformat(s["duty_start"])
    end = current_end(s)
    pos = s.get("pos", 0) % n
    result = [(ds, end - timedelta(days=1), s["order"][pos])]
    for k in range(1, n):
        st = end + timedelta(days=SHIFT_DAYS * (k - 1))
        en = st + timedelta(days=SHIFT_DAYS - 1)
        result.append((st, en, s["order"][(pos + k) % n]))
    return result


def schedule_text(s: dict) -> str:
    entries_text = ["🗓 NAVBATCHILIK JADVALI"]
    entries = schedule(s)
    if not entries:
        return "✅ Jadvaldagi hamma navbatchilar ishini tugatgan.\nAdmin /setup bilan yangi jadval tuzishi mumkin."
    for k, (st, en, i) in enumerate(entries):
        if k == 0 and st <= today():
            when = f"🔔 BUGUN · {today().strftime('%d.%m.%Y')}"
        elif (st - today()).days == 1:
            when = f"📅 ERTAGA · {st.strftime('%d.%m.%Y')}"
        else:
            when = f"📅 {st.strftime('%d.%m.%Y')}"
        person = MEMBERS[i]["name"]
        handle = username(i)
        if handle:
            person += f"\n👉 @{handle}"
        entries_text.append(f"{k + 1}. {when}\n👤 {person}")
    return "\n\n".join(entries_text)


def done_keyboard(version: int, member_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Bajarildi", callback_data=f"done:{version}:{member_idx}")]]
    )


def admin_keyboard(version: int, member_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Navbatchi bajardi", callback_data=f"done:{version}:{member_idx}")],
            [
                InlineKeyboardButton("📋 Jadval", callback_data="adm:jadval"),
            ],
            [InlineKeyboardButton("🔄 Yangi jadval tuzish", callback_data="ui:reset")],
        ]
    )


def menu_keyboard(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📅 Bugungi navbatchi", callback_data="ui:bugun")],
        [InlineKeyboardButton("📋 Navbatlar jadvali", callback_data="ui:jadval")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("🛠 Admin paneli", callback_data="ui:admin")])
    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menyu", callback_data="ui:menu")]])


def reset_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Ha, yangidan tuzish", callback_data="ui:reset_yes")],
        [InlineKeyboardButton("Bekor qilish", callback_data="ui:admin")],
    ])


def new_schedule_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Yangi jadval tuzish", callback_data="ui:reset")],
        [InlineKeyboardButton("⬅️ Menyu", callback_data="ui:menu")],
    ])


def create_schedule(previous: dict, chat_id: int) -> dict:
    order = list(range(len(MEMBERS)))
    random.shuffle(order)
    return {
        "chat_id": chat_id,
        "order": order,
        "pos": 0,
        "duty_start": today().isoformat(),
        "schedule_version": int(previous.get("schedule_version", 0)) + 1,
    }


# ---------- kunlik e'lon ----------
def duty_message(s: dict) -> str:
    if not s.get("order"):
        return "✅ Hozircha navbatchi qolmagan. Admin yangi jadval tuzishi mumkin."
    d = today()
    pos = s.get("pos", 0) % len(s["order"])
    ds = date.fromisoformat(s["duty_start"])
    duty_date = max(d, ds)
    idx = s["order"][pos]
    text = (
        "🧹 BUGUNGI NAVBATCHI\n\n"
        f"👤 Navbatchi: {MEMBERS[idx]['name']}\n"
        f"📅 Tozalash kuni: {duty_date.strftime('%d.%m.%Y')}"
    )
    if d > ds:
        text += (
            "\n\n⚠️ Kechagi tozalash bajarilmagan. Bugun ham shu odam navbatchi; "
            "keyingilarning navbati bir kunga surildi."
        )
    handle = username(idx)
    text += f"\n\n👉 @{handle}" if handle else "\n\n⚠️ Username members.json fayliga kiritilmagan."
    return text


async def announce(context: ContextTypes.DEFAULT_TYPE) -> None:
    s = load_state()
    if not s.get("order") or not s.get("chat_id"):
        return
    if date.fromisoformat(s["duty_start"]) > today():
        return  # navbat hali boshlanmagan
    await context.bot.send_message(
        s["chat_id"], duty_message(s),
        reply_markup=done_keyboard(s.get("schedule_version", 0), s["order"][s.get("pos", 0) % len(s["order"])]),
    )


def mark_done(s: dict) -> str:
    if not s.get("order"):
        return "Jadvalda navbatchi qolmagan."
    pos = s.get("pos", 0) % len(s["order"])
    finished = s["order"].pop(pos)
    s["pos"] = pos % len(s["order"]) if s["order"] else 0
    s["duty_start"] = (today() + timedelta(days=1)).isoformat()
    save_state(s)
    if not s["order"]:
        return f"✅ {label(finished)} bajarib, jadvaldan chiqarildi.\nHamma navbatchi tugadi; yangi jadval uchun /setup yozing."
    nxt_st, nxt_en, nxt = schedule(s)[0]
    return (
        f"✅ {label(finished)} bajarib, joriy jadvaldan chiqarildi.\n"
        f"Keyingi navbatchi: {label(nxt)}\n"
        f"📅 Tozalash kuni: {fmt_range(nxt_st, nxt_en)}"
    )


# ---------- buyruqlar ----------
@delete_command_message
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👋 Navbatchilik botiga xush kelibsiz!\nKerakli bo‘limni tanlang:",
        reply_markup=menu_keyboard(is_admin(update.effective_user.id)),
    )


@delete_command_message
async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin guruhda yozadi. Yangi jadvalni boshidan yaratadi."""
    if not is_admin(update.effective_user.id):
        return
    s = create_schedule(load_state(), update.effective_chat.id)
    save_state(s)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🔄 Yangi jadval boshidan tuzildi.\nBajarilganlar holati tozalandi.\n\n{duty_message(s)}",
        reply_markup=admin_keyboard(s["schedule_version"], s["order"][0]),
    )


@delete_command_message
async def bugun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = load_state()
    if s.get("order"):
        await context.bot.send_message(update.effective_chat.id, duty_message(s))


@delete_command_message
async def jadval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = load_state()
    if s.get("order"):
        await context.bot.send_message(update.effective_chat.id, schedule_text(s))


@delete_command_message
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    s = load_state()
    if not s.get("order"):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Jadval tugagan. Yangi jadval tuzing:",
            reply_markup=new_schedule_keyboard(),
        )
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🛠 Admin paneli\n\n" + duty_message(s),
        reply_markup=admin_keyboard(
            s.get("schedule_version", 0), s["order"][s.get("pos", 0) % len(s["order"])]
        ),
    )


@delete_command_message
async def bajarildi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    s = load_state()
    if s.get("order"):
        await context.bot.send_message(update.effective_chat.id, mark_done(s))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    s = load_state()
    kind, _, arg = q.data.partition(":")

    if kind == "ui":
        if arg in {"admin", "reset", "reset_yes"} and not is_admin(q.from_user.id):
            await q.answer("Faqat admin uchun", show_alert=True)
            return
        await q.answer()
        if arg == "menu":
            await q.edit_message_text(
                "👋 Kerakli bo‘limni tanlang:",
                reply_markup=menu_keyboard(is_admin(q.from_user.id)),
            )
            return
        if arg == "admin":
            if s.get("order"):
                text, markup = "🛠 Admin paneli\n\n" + duty_message(s), admin_keyboard(
                    s.get("schedule_version", 0), s["order"][s.get("pos", 0) % len(s["order"])]
                )
            else:
                text, markup = "Jadval tugagan. Yangi jadval tuzing:", new_schedule_keyboard()
        elif arg == "bugun":
            text = duty_message(s) if s.get("order") else "Jadval hali sozlanmagan."
            markup = back_keyboard()
        elif arg == "jadval":
            text = schedule_text(s) if s.get("order") else "Jadval hali sozlanmagan."
            markup = back_keyboard()
        elif arg == "reset":
            text, markup = "Bajarilganlarni ro‘yxatdan chiqarib, yangi jadval tuzilsinmi?", reset_confirm_keyboard()
        elif arg == "reset_yes":
            s = create_schedule(s, q.message.chat_id)
            save_state(s)
            text = "🔄 Yangi jadval boshidan tuzildi.\n\n" + duty_message(s)
            markup = admin_keyboard(s["schedule_version"], s["order"][0])
        else:
            text, markup = "Bo‘lim topilmadi.", back_keyboard()
        await q.edit_message_text(text, reply_markup=markup)
        return

    if kind == "adm":
        if not is_admin(q.from_user.id):
            await q.answer("Faqat admin uchun", show_alert=True)
            return
        await q.answer()
        text = schedule_text(s)
        await context.bot.send_message(
            chat_id=q.message.chat_id, text=text, reply_markup=back_keyboard()
        )
        return

    # kind == "done"
    if not is_admin(q.from_user.id):
        await q.answer("Faqat admin belgilay oladi", show_alert=True)
        return
    try:
        _, version_arg, member_arg = q.data.split(":", 2)
        expected_member = int(member_arg)
        expected_version = int(version_arg)
    except ValueError:
        await q.answer("Bu eski tugma. Yangilangan jadvalni oching.", show_alert=True)
        return
    pos = s.get("pos", 0) % len(s["order"]) if s.get("order") else 0
    if (
        not s.get("order")
        or expected_version != s.get("schedule_version", 0)
        or expected_member != s["order"][pos]
    ):
        await q.answer("Bu allaqachon belgilangan")
        return
    msg = mark_done(s)
    await q.answer("Belgilandi ✅")
    await q.edit_message_reply_markup(None)
    await context.bot.send_message(chat_id=q.message.chat_id, text=msg)


# ---------- "@bot /familiya" qidiruvi ----------
def find_member(text: str):
    t = text.strip().lstrip("/@").lower()
    if not t:
        return None
    for i, m in enumerate(MEMBERS):
        username = (m.get("username") or "").strip().lstrip("@").lower()
        if username and username == t:
            return i
    if len(t) >= 3:
        for i, m in enumerate(MEMBERS):
            if t in m["name"].lower():
                return i
    return None


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Quyidagilarda ishlaydi:
      @botnomi /sattorov     (botni teg qilib)
      /sattorov              (noma'lum buyruq sifatida)
      botning xabariga reply qilib nik yozish
    """
    msg = update.message
    if not msg or not msg.text:
        return
    s = load_state()
    if not s.get("order"):
        return

    text = msg.text.strip()
    bot_un = (context.bot.username or "").lower()
    first = text.split()[0]
    cmd = first.split("@")[0].lstrip("/").lower()
    if first.startswith("/") and cmd in KNOWN_COMMANDS:
        return  # oddiy buyruqlar boshqa handlerda

    mentioned = f"@{bot_un}" in text.lower()
    replied = (
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )
    unknown_cmd = first.startswith("/")
    if not (mentioned or replied or unknown_cmd):
        return

    query = re.sub(rf"@{re.escape(bot_un)}", " ", text, flags=re.I).strip()
    idx = None
    for token in query.split():
        idx = find_member(token)
        if idx is not None:
            break
    if idx is None and not query.lstrip("/").strip():
        idx = find_member(update.effective_user.username or "")  # o'zining nikini olamiz
    if idx is None:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text="Bunday odam topilmadi 🤷‍♂️ Familiya yoki nikneymni tekshiring.",
        )
        return

    for k, (st, en, i) in enumerate(schedule(s)):
        if i != idx:
            continue
        left = (st - today()).days
        if k == 0 and left <= 0:
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=f"👮 {label(idx)} — hozir navbatchi!\n🗓 {fmt_range(st, en)}",
            )
        else:
            when = "ertaga" if left == 1 else f"{left} kundan keyin"
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=(
                    f"👤 {label(idx)}\n"
                    f"📍 Oldida {k} ta odam bor\n"
                    f"🗓 Navbati: {fmt_range(st, en)} ({when})"
                ),
            )
        return


def main() -> None:
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. .env.example faylidan .env yarating, "
            "BOT_TOKEN= qatoriga BotFather'dan olingan yangi tokenni kiriting "
            "va botni qayta ishga tushiring."
        )
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", setup))
    app.add_handler(CommandHandler("bugun", bugun))
    app.add_handler(CommandHandler("jadval", jadval))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("bajarildi", bajarildi))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(done|adm|ui):"))
    app.add_handler(MessageHandler(filters.TEXT, on_text), group=1)
    app.job_queue.run_daily(announce, ANNOUNCE_AT)
    app.run_polling()


if __name__ == "__main__":
    main()
