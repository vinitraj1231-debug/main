#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║        ADVANCED TELEGRAM CHAT BOT — v5.0  (FINAL)                         ║
# ║   OpenAI SDK + OpenRouter · JSON DB · Broadcast · Live Chat · AI          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

import asyncio
import json
import logging
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ⚙️  CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN      = "8604814452:AAHVDgFvAhS8Xq5zBoH7WPzhYJI7JDfvFfU"
ADMIN_ID       = 8373641692
ADMIN_USERNAME = "@webdevsid"

# ── OpenRouter via OpenAI SDK ─────────────────────────────────────────────────
OPENROUTER_KEY = "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"

# Models tried in order — first one that works is used
AI_MODELS = [
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
]

# ── Settings ──────────────────────────────────────────────────────────────────
DB_FILE           = "database.json"
RATE_LIMIT_COUNT  = 5
RATE_LIMIT_WINDOW = 10
AI_TURNS_HANDOFF  = 6

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  📋  LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("TeleBot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🤖  OPENAI CLIENT  (pointed at OpenRouter)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
    default_headers={
        "HTTP-Referer": "https://t.me",
        "X-OpenRouter-Title": "SidAssistantBot",
    },
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🗄️  JSON DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_db_lock = asyncio.Lock()

def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _blank_db():
    return {
        "users":         {},
        "messages":      [],
        "admin":         {"online": True, "last_seen": _now()},
        "conversations": {},
        "ai_turns":      {},
    }

def _load():
    if not Path(DB_FILE).exists():
        data = _blank_db()
        _save(data)
        return data
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("DB load error: %s", e)
        return _blank_db()

def _save(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Users ─────────────────────────────────────────────────────────────────────

async def db_get_user(uid):
    async with _db_lock:
        return _load()["users"].get(str(uid))

async def db_upsert_user(uid, username, first_name, last_name):
    async with _db_lock:
        db     = _load()
        key    = str(uid)
        is_new = key not in db["users"]
        if is_new:
            db["users"][key] = {
                "telegram_id":   uid,
                "username":      username    or "",
                "first_name":    first_name  or "",
                "last_name":     last_name   or "",
                "joined_at":     _now(),
                "is_blocked":    False,
                "message_count": 0,
                "last_seen":     _now(),
            }
        else:
            u = db["users"][key]
            u["username"]   = username    or u.get("username",   "")
            u["first_name"] = first_name  or u.get("first_name", "")
            u["last_name"]  = last_name   or u.get("last_name",  "")
            u["last_seen"]  = _now()
        _save(db)
        return is_new

async def db_set_blocked(uid, blocked):
    async with _db_lock:
        db  = _load()
        key = str(uid)
        if key not in db["users"]:
            return False
        db["users"][key]["is_blocked"] = blocked
        _save(db)
        return True

async def db_get_all_users():
    async with _db_lock:
        return list(_load()["users"].values())

async def db_get_active_users():
    return [u for u in await db_get_all_users() if not u.get("is_blocked")]

async def db_inc_msg_count(uid):
    async with _db_lock:
        db  = _load()
        key = str(uid)
        if key in db["users"]:
            db["users"][key]["message_count"] = db["users"][key].get("message_count", 0) + 1
            db["users"][key]["last_seen"]      = _now()
            _save(db)

async def db_stats():
    async with _db_lock:
        db    = _load()
        users = list(db["users"].values())
        msgs  = db["messages"]
        return {
            "total":        len(users),
            "active":       sum(1 for u in users if not u.get("is_blocked")),
            "blocked":      sum(1 for u in users if     u.get("is_blocked")),
            "messages":     len([m for m in msgs if not m.get("is_admin")]),
            "admin_online": db.get("admin", {}).get("online", True),
        }

# ── Messages ──────────────────────────────────────────────────────────────────

async def db_log_msg(uid, text, is_admin, msg_type="text"):
    async with _db_lock:
        db = _load()
        db["messages"].append({
            "user_id":   uid,
            "text":      text or "",
            "is_admin":  is_admin,
            "type":      msg_type,
            "timestamp": _now(),
        })
        if len(db["messages"]) > 2000:
            db["messages"] = db["messages"][-2000:]
        _save(db)

async def db_get_history(uid, limit=10):
    async with _db_lock:
        all_msgs = [m for m in _load()["messages"] if m["user_id"] == uid]
        return all_msgs[-limit:]

# ── Admin status ──────────────────────────────────────────────────────────────

async def db_is_online():
    async with _db_lock:
        return _load().get("admin", {}).get("online", True)

async def db_set_online(online):
    async with _db_lock:
        db = _load()
        db.setdefault("admin", {})["online"]    = online
        db["admin"]["last_seen"] = _now()
        _save(db)

# ── AI conversation memory ────────────────────────────────────────────────────

async def db_get_conv(uid):
    async with _db_lock:
        return _load().get("conversations", {}).get(str(uid), [])

async def db_push_conv(uid, role, content):
    async with _db_lock:
        db    = _load()
        convs = db.setdefault("conversations", {})
        hist  = convs.setdefault(str(uid), [])
        hist.append({"role": role, "content": content})
        if len(hist) > 24:
            hist = hist[-24:]
        convs[str(uid)] = hist
        _save(db)

async def db_clear_conv(uid):
    async with _db_lock:
        db = _load()
        db.setdefault("conversations", {}).pop(str(uid), None)
        db.setdefault("ai_turns",      {}).pop(str(uid), None)
        _save(db)

async def db_get_turns(uid):
    async with _db_lock:
        return _load().get("ai_turns", {}).get(str(uid), 0)

async def db_inc_turns(uid):
    async with _db_lock:
        db  = _load()
        cnt = db.setdefault("ai_turns", {})
        cnt[str(uid)] = cnt.get(str(uid), 0) + 1
        _save(db)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🤖  AI ENGINE  —  OpenAI SDK → OpenRouter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_IMPORTANT_KW = [
    "urgent", "asap", "project", "website", "app", "budget", "hire",
    "freelance", "contract", "paid", "payment", "client", "deadline",
    "business", "startup", "ecommerce", "bot", "api", "price", "cost",
    "quote", "design", "development", "work", "need", "want", "build",
    "create", "make", "logo", "branding", "hosting", "domain",
    "जल्दी", "काम", "पैसे", "बजट", "प्रोजेक्ट", "वेबसाइट", "ऐप",
    "बनाना", "चाहिए", "जरूरी", "तुरंत", "फ्रीलांस", "रेट", "कीमत",
    "बनाओ", "बनादो", "कितना", "लगेगा",
]

def _is_important(text):
    t = text.lower()
    return any(kw in t for kw in _IMPORTANT_KW)

def _system_prompt(user_name, turn_count):
    wrap = ""
    if turn_count >= AI_TURNS_HANDOFF:
        wrap = (
            "\n\nNOTE: Conversation is getting long. "
            "Wrap up warmly, summarize the user's needs, and say the developer "
            "will personally reach out soon. Do NOT share any contact handle."
        )
    lines = [
        "You are a smart AI assistant working for Sid, a professional web developer.",
        "You handle messages when Sid is offline or busy.",
        "",
        "Your job for user " + user_name + ":",
        "1. Be warm, friendly, professional. Sound like a real helpful human.",
        "2. Understand what they need: website, app, Telegram bot, design, API, etc.",
        "3. Ask ONE smart follow-up question at a time:",
        "   - What kind of project?",
        "   - What specific features?",
        "   - Budget range?",
        "   - Timeline or deadline?",
        "4. NEVER share any contact info, usernames, or phone numbers.",
        "5. If asked when Sid replies: say 'I will make sure he gets back to you soon!'",
        "6. Keep replies SHORT: 2-4 sentences max.",
        "7. Reply in the SAME language the user uses (Hindi or English).",
        "8. NEVER mention errors, API issues, or technical problems to users.",
        "9. Always sound confident and capable." + wrap,
    ]
    return "\n".join(lines)

def _msg_preview(m):
    txt = m.get("text") or ""
    if not txt:
        mtype = m.get("type") or "unknown"
        txt   = "[" + mtype + "]"
    return txt[:65]


async def _call_model(messages, model):
    """
    Single call to OpenRouter using the openai SDK.
    Raises exception on any failure so caller can try next model.
    """
    log.debug("[AI] Trying: %s", model)

    completion = await ai_client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=400,
        temperature=0.75,
    )

    content = completion.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("Empty response from " + model)

    content = content.strip()
    log.info("[AI] ✅ %s replied (%d chars)", model, len(content))
    return content


async def ai_respond(uid, user_message, user_name):
    """
    Try every model. Returns (reply_text, is_important).
    Users never see errors.
    """
    turns        = await db_get_turns(uid)
    history      = await db_get_conv(uid)
    is_important = _is_important(user_message)

    messages = [{"role": "system", "content": _system_prompt(user_name, turns)}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    errors = []
    for model in AI_MODELS:
        try:
            reply = await _call_model(messages, model)
            await db_push_conv(uid, "user",      user_message)
            await db_push_conv(uid, "assistant", reply)
            await db_inc_turns(uid)
            return reply, is_important
        except Exception as e:
            err = model + " -> " + str(e)
            log.warning("[AI] ❌ %s", err)
            errors.append(err)

    # All models failed
    log.critical("[AI] 🚨 ALL MODELS FAILED for user %s:\n%s", uid, "\n".join(errors))

    fallbacks = [
        "Hey " + user_name + "! 👋 Could you tell me what you are looking for?",
        "Thanks for reaching out! What kind of project do you have in mind?",
        "Hi! Happy to help. What can I assist you with today?",
    ]
    return fallbacks[turns % len(fallbacks)], is_important


async def test_ai():
    """Test all models — returns status string for /testai command."""
    results = []
    test_msg = [{"role": "user", "content": "Reply with exactly one word: WORKING"}]

    for model in AI_MODELS:
        try:
            reply = await _call_model(test_msg, model)
            results.append("✅ <code>" + model + "</code>\n   Reply: " + reply[:60])
            break  # one passing model is enough to confirm connection
        except Exception as e:
            results.append("❌ <code>" + model + "</code>\n   " + str(e)[:100])

    return "\n\n".join(results)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🛑  RATE LIMITER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_windows = defaultdict(deque)

def is_rate_limited(uid):
    if uid == ADMIN_ID:
        return False, 0
    now    = time.monotonic()
    window = _windows[uid]
    while window and now - window[0] > RATE_LIMIT_WINDOW:
        window.popleft()
    if len(window) >= RATE_LIMIT_COUNT:
        wait = int(RATE_LIMIT_WINDOW - (now - window[0])) + 1
        return True, wait
    window.append(now)
    return False, 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🧰  UI HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _admin_kb(uid, is_blocked):
    toggle_label = "✅ Unblock" if is_blocked else "🚫 Block"
    toggle_data  = ("unblock:" if is_blocked else "block:") + str(uid)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("↩️ Reply",    callback_data="reply:"   + str(uid)),
            InlineKeyboardButton("📜 History",  callback_data="history:" + str(uid)),
        ],
        [
            InlineKeyboardButton(toggle_label, callback_data=toggle_data),
            InlineKeyboardButton("🤖 AI Log",  callback_data="ailog:"   + str(uid)),
        ],
    ])

def _bc_confirm_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send Now", callback_data="bc:confirm"),
        InlineKeyboardButton("❌ Cancel",   callback_data="bc:cancel"),
    ]])

# FSM state
_pending_reply     = {}   # admin_id → target_uid
_pending_broadcast = {}   # admin_id → bc data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  👤  USER HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def user_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u      = update.effective_user
    is_new = await db_upsert_user(
        u.id, u.username or "", u.first_name or "", u.last_name or ""
    )
    name = u.first_name or "there"

    if is_new:
        await update.message.reply_text(
            "👋 <b>Hello " + name + "!</b>\n\n"
            "I am an AI assistant. Tell me what you need and I will help! 🚀",
            parse_mode=ParseMode.HTML,
        )
        log.info("New user: %s (@%s)", u.id, u.username)
        try:
            text = (
                "🆕 <b>New User!</b>\n"
                "👤 <b>" + (u.first_name or "") + " " + (u.last_name or "") + "</b>\n"
                "🆔 <code>" + str(u.id) + "</code>  |  @" + (u.username or "N/A")
            )
            await ctx.bot.send_message(ADMIN_ID, text, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        await update.message.reply_text(
            "👋 <b>Welcome back, " + name + "!</b>\nHow can I help you today?",
            parse_mode=ParseMode.HTML,
        )


async def user_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u   = update.effective_user
    msg = update.message

    # Rate limit
    limited, wait = is_rate_limited(u.id)
    if limited:
        await msg.reply_text(
            "⏳ Please wait <b>" + str(wait) + "s</b> before sending again.",
            parse_mode=ParseMode.HTML,
        )
        return

    await db_upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")
    user = await db_get_user(u.id)
    if user and user.get("is_blocked"):
        await msg.reply_text("🚫 You have been restricted from using this bot.")
        return

    await db_inc_msg_count(u.id)

    # Detect media
    text    = msg.text or msg.caption or ""
    mtype   = "text"
    file_id = None
    if msg.photo:
        mtype, file_id = "photo",    msg.photo[-1].file_id
    elif msg.video:
        mtype, file_id = "video",    msg.video.file_id
    elif msg.audio:
        mtype, file_id = "audio",    msg.audio.file_id
    elif msg.document:
        mtype, file_id = "document", msg.document.file_id
    elif msg.voice:
        mtype, file_id = "voice",    msg.voice.file_id
    elif msg.sticker:
        mtype, file_id = "sticker",  msg.sticker.file_id

    await db_log_msg(u.id, text, is_admin=False, msg_type=mtype)

    admin_online = await db_is_online()
    fname        = u.first_name or "User"
    is_blocked   = user.get("is_blocked", False) if user else False

    # ── Build forward header ───────────────────────────────────────────────
    dot   = "🟢" if admin_online else "🔴"
    lname = (u.last_name or "").strip()
    name  = (fname + " " + lname).strip()

    header_parts = [
        dot + " <b>Message from</b> <a href='tg://user?id=" + str(u.id) + "'>" + name + "</a>",
        "🆔 <code>" + str(u.id) + "</code>" + ("  |  @" + u.username if u.username else ""),
    ]
    if mtype != "text":
        header_parts.append("📎 " + mtype)
    if text:
        header_parts.append("💬 " + text)
    header_parts.append("")
    header_parts.append("<i>/reply " + str(u.id) + " &lt;text&gt;</i>")
    header = "\n".join(header_parts)

    kb = _admin_kb(u.id, is_blocked)

    # Forward to admin
    try:
        if mtype == "text":
            await ctx.bot.send_message(
                ADMIN_ID, header, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        elif mtype == "photo" and file_id:
            cap = "📸 <code>" + str(u.id) + "</code>" + ("\n💬 " + text if text else "")
            await ctx.bot.send_photo(
                ADMIN_ID, file_id, caption=cap,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        elif mtype == "video" and file_id:
            cap = "🎥 <code>" + str(u.id) + "</code>" + ("\n💬 " + text if text else "")
            await ctx.bot.send_video(
                ADMIN_ID, file_id, caption=cap,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        elif mtype == "voice" and file_id:
            cap = "🎙️ <code>" + str(u.id) + "</code>"
            await ctx.bot.send_voice(
                ADMIN_ID, file_id, caption=cap,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        elif mtype == "document" and file_id:
            cap = "📄 <code>" + str(u.id) + "</code>" + ("\n💬 " + text if text else "")
            await ctx.bot.send_document(
                ADMIN_ID, file_id, caption=cap,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        elif mtype == "audio" and file_id:
            cap = "🎵 <code>" + str(u.id) + "</code>"
            await ctx.bot.send_audio(
                ADMIN_ID, file_id, caption=cap,
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        elif mtype == "sticker" and file_id:
            await ctx.bot.send_sticker(ADMIN_ID, file_id)
            await ctx.bot.send_message(
                ADMIN_ID,
                "↑ Sticker from <code>" + str(u.id) + "</code>",
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        else:
            await msg.forward(ADMIN_ID)
    except Exception as e:
        log.error("Forward to admin failed: %s", e)

    # ── Reply to user ──────────────────────────────────────────────────────
    if admin_online:
        await msg.reply_text(
            "✅ <b>Received!</b> Admin will reply shortly.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await ctx.bot.send_chat_action(u.id, "typing")

        if mtype != "text":
            ai_reply     = (
                "Thanks for the " + mtype + "! 📎 "
                "Could you also describe what you need in text? "
                "That will help me understand better."
            )
            is_important = False
        else:
            ai_reply, is_important = await ai_respond(u.id, text, fname)

        await msg.reply_text(
            "🤖 <i>AI Assistant:</i>\n\n" + ai_reply,
            parse_mode=ParseMode.HTML,
        )

        # Silent admin alerts
        if is_important:
            try:
                cur_turns = await db_get_turns(u.id)
                alert = (
                    "🔔 <b>Important Lead!</b>\n"
                    "👤 " + fname + " (<code>" + str(u.id) + "</code>)\n"
                    "💬 <i>" + text[:100] + "</i>\n"
                    "🤖 AI turn #" + str(cur_turns) + " — consider /online"
                )
                await ctx.bot.send_message(ADMIN_ID, alert, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        cur_turns = await db_get_turns(u.id)
        if cur_turns == AI_TURNS_HANDOFF:
            try:
                alert = (
                    "⏰ <b>Handoff Alert!</b> "
                    + fname + " (<code>" + str(u.id) + "</code>) "
                    "has had <b>" + str(cur_turns) + "</b> AI chats. "
                    "Use /online to take over."
                )
                await ctx.bot.send_message(ADMIN_ID, alert, parse_mode=ParseMode.HTML)
            except Exception:
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🛡️  ADMIN HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _admin_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return
        return await fn(update, ctx)
    return wrapper


@_admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    online = await db_is_online()
    st     = "🟢 ONLINE" if online else "🔴 OFFLINE (AI active)"
    models = "\n".join("   • " + m for m in AI_MODELS)
    await update.message.reply_text(
        "🛡️ <b>Admin Panel</b>  |  " + st + "\n\n"
        "<b>AI:</b> OpenRouter via OpenAI SDK\n"
        "<b>Models:</b>\n" + models + "\n\n"
        "<b>Commands:</b>\n"
        "/online — Go online (AI off)\n"
        "/offline — Go offline (AI on)\n"
        "/testai — Test AI connection\n"
        "/reply &lt;id&gt; &lt;msg&gt; — Reply to user\n"
        "/broadcast &lt;msg&gt; — Mass text\n"
        "/broadcast_media — Mass photo/video\n"
        "/block &lt;id&gt; — Block user\n"
        "/unblock &lt;id&gt; — Unblock user\n"
        "/users — All users\n"
        "/stats — Statistics\n"
        "/history &lt;id&gt; — Message history\n"
        "/clearai &lt;id&gt; — Reset AI memory\n"
        "/cancel — Cancel pending action",
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def cmd_testai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    wait_msg = await update.message.reply_text("🔧 Testing AI connection...")
    result   = await test_ai()
    await wait_msg.edit_text(
        "🤖 <b>AI Test Results:</b>\n\n" + result
        + "\n\n<i>Key: .../" + OPENROUTER_KEY[-10:] + "</i>",
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def cmd_online(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await db_set_online(True)
    await update.message.reply_text(
        "🟢 You are <b>ONLINE</b>. AI disabled.",
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def cmd_offline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await db_set_online(False)
    await update.message.reply_text(
        "🔴 You are <b>OFFLINE</b>. 🤖 AI is now active.",
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def cmd_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text(
            "❌ Usage: <code>/reply &lt;user_id&gt; &lt;message&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        target = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    await _do_reply(ctx.bot, update.message, target, parts[2])


async def _do_reply(bot, admin_msg, target, text):
    user = await db_get_user(target)
    if not user:
        await admin_msg.reply_text(
            "⚠️ User <code>" + str(target) + "</code> not found.",
            parse_mode=ParseMode.HTML,
        )
        return
    if user.get("is_blocked"):
        await admin_msg.reply_text(
            "🚫 User <code>" + str(target) + "</code> is blocked.",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        await bot.send_message(
            target,
            "📩 <b>Reply from Admin:</b>\n\n" + text,
            parse_mode=ParseMode.HTML,
        )
        await db_log_msg(target, text, is_admin=True)
        await db_push_conv(target, "assistant", "[Admin replied]: " + text)
        await admin_msg.reply_text(
            "✅ Delivered to <code>" + str(target) + "</code>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await admin_msg.reply_text("❌ Failed: " + str(e))


@_admin_only
async def cmd_block(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_block(update, ctx, True)


@_admin_only
async def cmd_unblock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _do_block(update, ctx, False)


async def _do_block(update, ctx, block):
    parts = update.message.text.split()
    if len(parts) < 2:
        verb = "block" if block else "unblock"
        await update.message.reply_text(
            "❌ Usage: <code>/" + verb + " &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        target = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    ok = await db_set_blocked(target, block)
    if ok:
        icon = "🚫" if block else "✅"
        verb = "blocked" if block else "unblocked"
        await update.message.reply_text(
            icon + " User <code>" + str(target) + "</code> " + verb + ".",
            parse_mode=ParseMode.HTML,
        )
        try:
            note = "🚫 You have been restricted." if block else "✅ You have been unrestricted."
            await ctx.bot.send_message(target, note)
        except Exception:
            pass
    else:
        await update.message.reply_text(
            "⚠️ User <code>" + str(target) + "</code> not found.",
            parse_mode=ParseMode.HTML,
        )


@_admin_only
async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = await db_get_all_users()
    if not users:
        await update.message.reply_text("No users yet.")
        return
    lines = ["👥 <b>All Users (" + str(len(users)) + ")</b>\n"]
    for u in users[:30]:
        s    = "🚫" if u.get("is_blocked") else "✅"
        fn   = u.get("first_name", "")
        ln   = u.get("last_name",  "")
        name = (fn + " " + ln).strip() or "Unknown"
        un   = "@" + u["username"] if u.get("username") else "—"
        cnt  = str(u.get("message_count", 0))
        tid  = str(u["telegram_id"])
        lines.append(s + " <b>" + name + "</b> " + un + "\n   🆔 <code>" + tid + "</code> · " + cnt + " msgs")
    if len(users) > 30:
        lines.append("\n<i>+" + str(len(users) - 30) + " more</i>")
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)


@_admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s  = await db_stats()
    st = "🟢 Online" if s["admin_online"] else "🔴 Offline (AI active)"
    await update.message.reply_text(
        "📊 <b>Statistics</b>\n\n"
        "👥 Total: <b>" + str(s["total"])    + "</b>  "
        "✅ Active: <b>" + str(s["active"])   + "</b>  "
        "🚫 Blocked: <b>" + str(s["blocked"]) + "</b>\n"
        "📩 Messages: <b>" + str(s["messages"]) + "</b>\n"
        "⚡ Status: <b>" + st + "</b>",
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Usage: <code>/history &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        target = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    msgs = await db_get_history(target, 10)
    if not msgs:
        await update.message.reply_text(
            "No history for <code>" + str(target) + "</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    lines = ["📜 <b>History — <code>" + str(target) + "</code>:</b>\n"]
    for m in msgs:
        who = "🛡️ Admin" if m.get("is_admin") else "👤 User"
        ts  = m.get("timestamp", "")[-8:-3]
        txt = _msg_preview(m)
        lines.append("<i>" + ts + "</i> " + who + ": " + txt)
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@_admin_only
async def cmd_clearai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Usage: <code>/clearai &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        target = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    await db_clear_conv(target)
    await update.message.reply_text(
        "🤖 AI memory cleared for <code>" + str(target) + "</code>.",
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "❌ Usage: <code>/broadcast &lt;message&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    text  = parts[1].strip()
    users = await db_get_active_users()
    _pending_broadcast[ADMIN_ID] = {"type": "text", "text": text}
    await update.message.reply_text(
        "📢 <b>Preview:</b>\n\n" + text + "\n\n─────\n"
        "👥 <b>" + str(len(users)) + "</b> recipients",
        parse_mode=ParseMode.HTML,
        reply_markup=_bc_confirm_kb(),
    )


@_admin_only
async def cmd_broadcast_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _pending_broadcast[ADMIN_ID] = {"waiting_media": True}
    await update.message.reply_text(
        "📸 Send a <b>photo or video</b> to broadcast.\n/cancel to abort.",
        parse_mode=ParseMode.HTML,
    )


@_admin_only
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _pending_broadcast.pop(ADMIN_ID, None)
    _pending_reply.pop(ADMIN_ID, None)
    await update.message.reply_text("❌ Cancelled.")


@_admin_only
async def admin_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # FSM: reply to user
    if ADMIN_ID in _pending_reply and msg.text:
        target = _pending_reply.pop(ADMIN_ID)
        await _do_reply(ctx.bot, msg, target, msg.text)
        return

    # FSM: broadcast media
    bc = _pending_broadcast.get(ADMIN_ID, {})
    if bc.get("waiting_media"):
        users = await db_get_active_users()
        if msg.photo:
            _pending_broadcast[ADMIN_ID] = {
                "type":    "photo",
                "file_id": msg.photo[-1].file_id,
                "text":    msg.caption or "",
            }
        elif msg.video:
            _pending_broadcast[ADMIN_ID] = {
                "type":    "video",
                "file_id": msg.video.file_id,
                "text":    msg.caption or "",
            }
        else:
            await msg.reply_text("⚠️ Please send a photo or video.")
            return
        mtype = _pending_broadcast[ADMIN_ID]["type"]
        cap   = _pending_broadcast[ADMIN_ID]["text"]
        cap_display = cap if cap else "<i>none</i>"
        await msg.reply_text(
            "📢 <b>" + mtype.title() + " Broadcast</b>\n"
            "Caption: " + cap_display + "\n"
            "👥 <b>" + str(len(users)) + "</b> recipients",
            parse_mode=ParseMode.HTML,
            reply_markup=_bc_confirm_kb(),
        )
        return

    # Native Telegram reply-to
    if msg.reply_to_message and msg.text:
        ref = msg.reply_to_message.text or msg.reply_to_message.caption or ""
        match = re.search(r"<code>(\d{6,})</code>", ref)
        if not match:
            match = re.search(r"reply\s+(\d{6,})", ref)
        if match:
            await _do_reply(ctx.bot, msg, int(match.group(1)), msg.text)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  📲  CALLBACK HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    uid  = q.from_user.id

    if uid != ADMIN_ID:
        await q.answer("⛔ Admin only.")
        return
    await q.answer()

    # block / unblock
    if data.startswith("block:") or data.startswith("unblock:"):
        action = data.split(":")[0]
        tid    = int(data.split(":")[1])
        block  = action == "block"
        await db_set_blocked(tid, block)
        await q.edit_message_reply_markup(reply_markup=_admin_kb(tid, block))
        icon = "🚫" if block else "✅"
        verb = "blocked" if block else "unblocked"
        await q.message.reply_text(
            icon + " User <code>" + str(tid) + "</code> " + verb + ".",
            parse_mode=ParseMode.HTML,
        )
        try:
            note = "🚫 You have been restricted." if block else "✅ You have been unrestricted."
            await ctx.bot.send_message(tid, note)
        except Exception:
            pass

    # history
    elif data.startswith("history:"):
        tid  = int(data.split(":")[1])
        msgs = await db_get_history(tid, 10)
        if not msgs:
            await q.message.reply_text(
                "No history for <code>" + str(tid) + "</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        lines = ["📜 <b>History <code>" + str(tid) + "</code>:</b>\n"]
        for m in msgs:
            who = "🛡️" if m.get("is_admin") else "👤"
            ts  = m.get("timestamp", "")[-8:-3]
            txt = _msg_preview(m)
            lines.append("<i>" + ts + "</i> " + who + " " + txt)
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    # reply prompt
    elif data.startswith("reply:"):
        tid = int(data.split(":")[1])
        _pending_reply[ADMIN_ID] = tid
        await q.message.reply_text(
            "✏️ Type reply for <code>" + str(tid) + "</code>:\n<i>(/cancel to abort)</i>",
            parse_mode=ParseMode.HTML,
        )

    # AI log
    elif data.startswith("ailog:"):
        tid   = int(data.split(":")[1])
        conv  = await db_get_conv(tid)
        turns = await db_get_turns(tid)
        if not conv:
            await q.message.reply_text(
                "No AI log for <code>" + str(tid) + "</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        lines = ["🤖 <b>AI Log <code>" + str(tid) + "</code> (" + str(turns) + " turns):</b>\n"]
        for c in conv[-12:]:
            who = "🤖" if c["role"] == "assistant" else "👤"
            lines.append(who + " " + c["content"][:90])
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    # broadcast confirm
    elif data == "bc:confirm":
        bc = _pending_broadcast.pop(ADMIN_ID, None)
        if not bc:
            await q.message.reply_text("⚠️ No pending broadcast.")
            return
        await q.edit_message_reply_markup(reply_markup=None)
        users  = await db_get_active_users()
        total  = len(users)
        status = await q.message.reply_text(
            "📡 <b>Broadcasting to " + str(total) + " users…</b>",
            parse_mode=ParseMode.HTML,
        )
        sent = 0
        failed = 0
        auto_block = []

        for i, user in enumerate(users, 1):
            tid = user["telegram_id"]
            try:
                if bc["type"] == "text":
                    await ctx.bot.send_message(
                        tid,
                        "📢 <b>Announcement:</b>\n\n" + bc["text"],
                        parse_mode=ParseMode.HTML,
                    )
                elif bc["type"] == "photo":
                    cap = ("📢 " + bc["text"]) if bc.get("text") else None
                    await ctx.bot.send_photo(tid, bc["file_id"], caption=cap)
                elif bc["type"] == "video":
                    cap = ("📢 " + bc["text"]) if bc.get("text") else None
                    await ctx.bot.send_video(tid, bc["file_id"], caption=cap)
                sent += 1
            except Exception as e:
                failed += 1
                err = str(e).lower()
                if "blocked" in err or "deactivated" in err or "not found" in err:
                    auto_block.append(tid)

            if i % 10 == 0 or i == total:
                pct = int(i / total * 100)
                try:
                    await status.edit_text(
                        "📡 <b>Broadcasting…</b> " + str(pct) + "%"
                        " (" + str(i) + "/" + str(total) + ")\n"
                        "✅ " + str(sent) + "  ❌ " + str(failed),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.05)

        for tid in auto_block:
            await db_set_blocked(tid, True)

        extra = ("\n🚫 Auto-blocked: " + str(len(auto_block))) if auto_block else ""
        await status.edit_text(
            "✅ <b>Broadcast Done!</b>\n"
            "✅ Sent: " + str(sent) + "  ❌ Failed: " + str(failed) + extra,
            parse_mode=ParseMode.HTML,
        )

    # broadcast cancel
    elif data == "bc:cancel":
        _pending_broadcast.pop(ADMIN_ID, None)
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text("❌ Broadcast cancelled.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🚀  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    log.info("🚀 Starting Telegram Bot v5.0 (OpenAI SDK + OpenRouter)...")
    log.info("🔑 Key: sk-or-v1-...%s", OPENROUTER_KEY[-8:])
    log.info("🤖 Models: %s", AI_MODELS)

    app = Application.builder().token(BOT_TOKEN).build()

    AF = filters.User(ADMIN_ID)
    UF = ~AF

    # Admin commands
    app.add_handler(CommandHandler("start",           cmd_start,           filters=AF))
    app.add_handler(CommandHandler("online",          cmd_online,          filters=AF))
    app.add_handler(CommandHandler("offline",         cmd_offline,         filters=AF))
    app.add_handler(CommandHandler("testai",          cmd_testai,          filters=AF))
    app.add_handler(CommandHandler("reply",           cmd_reply,           filters=AF))
    app.add_handler(CommandHandler("block",           cmd_block,           filters=AF))
    app.add_handler(CommandHandler("unblock",         cmd_unblock,         filters=AF))
    app.add_handler(CommandHandler("users",           cmd_users,           filters=AF))
    app.add_handler(CommandHandler("stats",           cmd_stats,           filters=AF))
    app.add_handler(CommandHandler("history",         cmd_history,         filters=AF))
    app.add_handler(CommandHandler("clearai",         cmd_clearai,         filters=AF))
    app.add_handler(CommandHandler("broadcast",       cmd_broadcast,       filters=AF))
    app.add_handler(CommandHandler("broadcast_media", cmd_broadcast_media, filters=AF))
    app.add_handler(CommandHandler("cancel",          cmd_cancel,          filters=AF))

    # Admin input (FSM + reply-to)
    app.add_handler(MessageHandler(
        AF & (filters.TEXT | filters.PHOTO | filters.VIDEO),
        admin_input,
    ))

    # Callback buttons
    app.add_handler(CallbackQueryHandler(on_callback))

    # User
    app.add_handler(CommandHandler("start", user_start, filters=UF))
    app.add_handler(MessageHandler(
        UF & ~filters.COMMAND & (
            filters.TEXT    | filters.PHOTO   | filters.VIDEO  |
            filters.AUDIO   | filters.Document.ALL             |
            filters.VOICE   | filters.Sticker.ALL
        ),
        user_message,
    ))

    log.info("✅ Bot live! Admin ID: %s", ADMIN_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
