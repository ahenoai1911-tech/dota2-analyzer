import asyncio
import logging
import os
import httpx
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    InlineQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── НАСТРОЙКИ ────────────────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
WEBAPP_URL  = os.getenv("WEBAPP_URL", "https://your-domain.com")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Простая БД в памяти (замени на Redis/SQLite для продакшена)
# Структура: { user_id: {"tracked": [player_id, ...], "last_seen": {...}} }
user_data: dict = {}


# ════════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════════

async def fetch_player(query: str) -> dict | None:
    """Запрос к нашему бэкенду."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BACKEND_URL}/player",
                params={"query": query},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error(f"fetch_player error: {e}")
        return None


def rank_name(tier: int | None) -> str:
    ranks = {
        10: "Herald", 20: "Guardian", 30: "Crusader",
        40: "Archon",  50: "Legend",   60: "Ancient",
        70: "Divine",  80: "Immortal",
    }
    if not tier:
        return "Uncalibrated"
    return ranks.get(int(tier / 10) * 10, "Unknown")


def rank_emoji(tier: int | None) -> str:
    emojis = {
        10:"🪵", 20:"🛡️", 30:"⚒️", 40:"🗡️",
        50:"⚔️", 60:"🏅", 70:"💎", 80:"🌟",
    }
    if not tier:
        return "❓"
    return emojis.get(int(tier / 10) * 10, "❓")


def wr_emoji(wr: float) -> str:
    if wr >= 55: return "🟢"
    if wr >= 50: return "🟡"
    return "🔴"


def kda_emoji(kda: float) -> str:
    if kda >= 4: return "🌟"
    if kda >= 2.5: return "👍"
    return "💀"


def format_player_card(data: dict) -> str:
    """Форматирует красивую карточку игрока для Telegram."""
    p = data["profile"]
    s = data["stats"]
    advice = data.get("advice", [])

    rank_e = rank_emoji(p.get("rank"))
    rank_n = rank_name(p.get("rank"))
    wr_e   = wr_emoji(s["winrate"])
    kda_e  = kda_emoji(s["avg_kda"])

    # Топ-совет
    top_advice = ""
    if advice:
        top_advice = f"\n💡 <i>{advice[0]['text']}</i>"

    return (
        f"⚔️ <b>{p['name']}</b>\n"
        f"{rank_e} <b>{rank_n}</b>  ·  ID: <code>{p['account_id']}</code>\n"
        f"{'─' * 28}\n"
        f"{wr_e} WinRate: <b>{s['winrate']}%</b>  "
        f"(<b>{s['wins']}W</b> / {s['losses']}L · {s['total_games']} игр)\n"
        f"{kda_e} KDA: <b>{s['avg_kda']}</b>  "
        f"({s['avg_kills']} / <b>{s['avg_deaths']}</b> / {s['avg_assists']})\n"
        f"💰 GPM: <b>{s['avg_gpm']}</b>  ·  "
        f"✨ XPM: <b>{s['avg_xpm']}</b>\n"
        f"{'─' * 28}"
        f"{top_advice}"
    )


def main_keyboard(player_id: int | None = None) -> InlineKeyboardMarkup:
    """Главная клавиатура."""
    buttons = [
        [InlineKeyboardButton("🚀 Открыть приложение", web_app=WebAppInfo(url=WEBAPP_URL))],
        [
            InlineKeyboardButton("🔍 Найти игрока", callback_data="cmd_search"),
            InlineKeyboardButton("📊 Топ героев", callback_data="cmd_heroes"),
        ],
        [
            InlineKeyboardButton("ℹ️ Помощь", callback_data="cmd_help"),
            InlineKeyboardButton("⚙️ Настройки", callback_data="cmd_settings"),
        ],
    ]
    if player_id:
        buttons.insert(1, [
            InlineKeyboardButton("🔔 Отслеживать", callback_data=f"track_{player_id}"),
            InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{player_id}"),
        ])
    return InlineKeyboardMarkup(buttons)


def player_keyboard(player_id: int, name: str) -> InlineKeyboardMarkup:
    """Клавиатура под карточкой игрока."""
    safe_name = name[:20]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 Отслеживать", callback_data=f"track_{player_id}"),
            InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{player_id}"),
        ],
        [
            InlineKeyboardButton("🚀 Открыть в приложении", web_app=WebAppInfo(
                url=f"{WEBAPP_URL}?player={player_id}"
            )),
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data="cmd_start")],
    ])


# ════════════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/start"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, <b>{user.first_name}</b>!\n\n"
        f"⚔️ <b>Dota 2 Analyzer</b> — анализирую статистику игроков\n\n"
        f"<b>Что умею:</b>\n"
        f"• 📊 Показывать WinRate, KDA, GPM/XPM\n"
        f"• 🏆 Топ героев по играм\n"
        f"• 💡 Давать советы по улучшению игры\n"
        f"• 🔔 Отслеживать игроков\n"
        f"• 🔍 Работать в инлайн-режиме\n\n"
        f"Используй кнопки ниже или команды:\n"
        f"/player <code>ник</code> — найти игрока\n"
        f"/track — отслеживаемые игроки\n"
        f"/help — помощь",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/help"""
    text = (
        "📖 <b>Все команды:</b>\n\n"
        "/start — главное меню\n"
        "/player <code>ник</code> — статистика игрока\n"
        "/player <code>steamID</code> — по Steam ID\n"
        "/track — список отслеживаемых\n"
        "/untrack <code>steamID</code> — перестать следить\n"
        "/top — мировой топ игроков\n"
        "/help — это сообщение\n\n"
        "🔍 <b>Инлайн-режим:</b>\n"
        "В любом чате напиши <code>@твой_бот ник_игрока</code>\n"
        "и получи карточку игрока прямо в чат!\n\n"
        "🌐 <b>Web App:</b>\n"
        "Полный интерфейс с графиками и таблицами — кнопка 🚀"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("◀️ Назад", callback_data="cmd_start")
    ]])
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cmd_player(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/player <query>"""
    if not ctx.args:
        await update.message.reply_text(
            "❓ Укажи ник или Steam ID:\n<code>/player Miracle-</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    query = " ".join(ctx.args)
    msg = await update.message.reply_text(f"⏳ Ищу <b>{query}</b>...", parse_mode=ParseMode.HTML)

    data = await fetch_player(query)
    if not data:
        await msg.edit_text("❌ Игрок не найден. Проверь ник или попробуй Steam ID.")
        return

    card = format_player_card(data)
    pid  = data["profile"].get("account_id")
    name = data["profile"].get("name", "")

    await msg.edit_text(
        card,
        parse_mode=ParseMode.HTML,
        reply_markup=player_keyboard(pid, name),
    )


async def cmd_track_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/track — список отслеживаемых игроков"""
    uid   = update.effective_user.id
    udata = user_data.get(uid, {})
    tracked = udata.get("tracked", [])

    if not tracked:
        await update.message.reply_text(
            "📋 У тебя нет отслеживаемых игроков.\n\n"
            "Найди игрока командой /player и нажми 🔔 Отслеживать",
        )
        return

    lines = ["🔔 <b>Отслеживаемые игроки:</b>\n"]
    for pid in tracked:
        lines.append(f"• <code>{pid}</code> — /player {pid}")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Обновить всех", callback_data="track_refresh_all"),
        InlineKeyboardButton("🗑 Очистить", callback_data="track_clear"),
    ]])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def cmd_untrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/untrack <player_id>"""
    uid = update.effective_user.id
    if not ctx.args:
        await update.message.reply_text("Укажи Steam ID: <code>/untrack 105248644</code>", parse_mode=ParseMode.HTML)
        return

    pid = ctx.args[0]
    udata = user_data.setdefault(uid, {"tracked": []})
    if pid in udata["tracked"]:
        udata["tracked"].remove(pid)
        await update.message.reply_text(f"✅ Игрок <code>{pid}</code> удалён из отслеживания.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"❓ Игрок <code>{pid}</code> не найден в списке.", parse_mode=ParseMode.HTML)


async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/top — мировой топ"""
    msg = await update.message.reply_text("⏳ Загружаю мировой топ...")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.opendota.com/api/players/top")
            r.raise_for_status()
            players = r.json()[:10]

        lines = ["🌍 <b>Топ-10 игроков мира (OpenDota):</b>\n"]
        for i, p in enumerate(players, 1):
            name = p.get("personaname", "Unknown")
            pid  = p.get("account_id", "")
            lines.append(f"{i}. <b>{name}</b> — <code>/player {pid}</code>")

        await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text("❌ Не удалось загрузить топ. Попробуй позже.")
        logger.error(f"cmd_top error: {e}")


# ════════════════════════════════════════════════════════════════════════
#  CALLBACK BUTTONS
# ════════════════════════════════════════════════════════════════════════

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    uid  = update.effective_user.id

    # ── /start ──
    if data == "cmd_start":
        user = update.effective_user
        await query.edit_message_text(
            f"👋 Привет, <b>{user.first_name}</b>!\n\n"
            f"⚔️ <b>Dota 2 Analyzer</b>\n\n"
            f"/player <code>ник</code> — найти игрока\n"
            f"/track — отслеживаемые\n"
            f"/help — помощь",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

    # ── help ──
    elif data == "cmd_help":
        await cmd_help(update, ctx)

    # ── search hint ──
    elif data == "cmd_search":
        await query.edit_message_text(
            "🔍 <b>Поиск игрока:</b>\n\n"
            "Отправь команду:\n"
            "<code>/player Miracle-</code>\n"
            "<code>/player 105248644</code>\n\n"
            "или используй инлайн-режим:\n"
            "<code>@бот Dendi</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="cmd_start")
            ]]),
        )

    # ── heroes hint ──
    elif data == "cmd_heroes":
        await query.edit_message_text(
            "🏆 <b>Топ героев игрока:</b>\n\n"
            "Найди игрока и открой вкладку <b>Герои</b> в приложении:\n\n"
            "<code>/player Miracle-</code>  →  🚀 Открыть приложение",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="cmd_start")
            ]]),
        )

    # ── settings ──
    elif data == "cmd_settings":
        udata    = user_data.get(uid, {})
        notif_on = udata.get("notifications", True)
        icon     = "🔔" if notif_on else "🔕"
        label    = "Выключить уведомления" if notif_on else "Включить уведомления"

        await query.edit_message_text(
            f"⚙️ <b>Настройки</b>\n\n"
            f"{icon} Уведомления: <b>{'Вкл' if notif_on else 'Выкл'}</b>\n"
            f"📋 Отслеживаемых игроков: <b>{len(udata.get('tracked', []))}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(label, callback_data="toggle_notif")],
                [InlineKeyboardButton("◀️ Назад", callback_data="cmd_start")],
            ]),
        )

    # ── toggle notifications ──
    elif data == "toggle_notif":
        udata = user_data.setdefault(uid, {"tracked": [], "notifications": True})
        udata["notifications"] = not udata.get("notifications", True)
        icon  = "🔔" if udata["notifications"] else "🔕"
        await query.answer(f"{icon} Уведомления {'включены' if udata['notifications'] else 'выключены'}", show_alert=True)
        # Обновим меню
        await on_callback(update, ctx)

    # ── track player ──
    elif data.startswith("track_") and not data.startswith("track_refresh") and not data.startswith("track_clear"):
        pid   = data.split("_", 1)[1]
        udata = user_data.setdefault(uid, {"tracked": [], "notifications": True})
        if pid not in udata["tracked"]:
            udata["tracked"].append(pid)
            await query.answer(f"✅ Игрок {pid} добавлен в отслеживание!", show_alert=True)
        else:
            await query.answer("ℹ️ Игрок уже в списке отслеживания", show_alert=True)

    # ── refresh player ──
    elif data.startswith("refresh_"):
        pid  = data.split("_", 1)[1]
        await query.answer("⏳ Обновляю...")
        new_data = await fetch_player(pid)
        if new_data:
            card = format_player_card(new_data)
            name = new_data["profile"].get("name", "")
            await query.edit_message_text(
                card + "\n\n<i>🔄 Данные обновлены</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=player_keyboard(int(pid), name),
            )
        else:
            await query.answer("❌ Не удалось обновить данные", show_alert=True)

    # ── refresh all tracked ──
    elif data == "track_refresh_all":
        uid_data = user_data.get(uid, {})
        tracked  = uid_data.get("tracked", [])
        if not tracked:
            await query.answer("Список пуст", show_alert=True)
            return
        await query.answer("⏳ Обновляю всех...")
        results = []
        for pid in tracked[:5]:  # Максимум 5 чтобы не ждать долго
            d = await fetch_player(pid)
            if d:
                s = d["stats"]
                p = d["profile"]
                results.append(
                    f"• <b>{p['name']}</b> — WR: {wr_emoji(s['winrate'])}{s['winrate']}%  KDA: {s['avg_kda']}"
                )
        if results:
            await query.edit_message_text(
                "🔔 <b>Обновлённая статистика:</b>\n\n" + "\n".join(results),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="cmd_start")
                ]]),
            )

    # ── clear tracking ──
    elif data == "track_clear":
        user_data.setdefault(uid, {})["tracked"] = []
        await query.answer("🗑 Список очищен", show_alert=True)
        await query.edit_message_text(
            "✅ Список отслеживания очищен.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="cmd_start")
            ]]),
        )


# ════════════════════════════════════════════════════════════════════════
#  INLINE MODE
# ════════════════════════════════════════════════════════════════════════

async def on_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Инлайн-режим: @бот Miracle-"""
    query = update.inline_query.query.strip()
    if not query or len(query) < 2:
        return

    data = await fetch_player(query)
    if not data:
        results = [
            InlineQueryResultArticle(
                id="not_found",
                title="❌ Игрок не найден",
                description=f"По запросу «{query}» ничего нет",
                input_message_content=InputTextMessageContent(
                    f"❌ Игрок «{query}» не найден в OpenDota"
                ),
            )
        ]
    else:
        card = format_player_card(data)
        p    = data["profile"]
        s    = data["stats"]
        results = [
            InlineQueryResultArticle(
                id=str(p.get("account_id", "1")),
                title=f"⚔️ {p['name']}",
                description=(
                    f"{rank_emoji(p.get('rank'))} {rank_name(p.get('rank'))} · "
                    f"WR: {s['winrate']}% · KDA: {s['avg_kda']}"
                ),
                input_message_content=InputTextMessageContent(
                    card, parse_mode=ParseMode.HTML
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🚀 Открыть полный анализ",
                        url=f"{WEBAPP_URL}?player={p.get('account_id')}",
                    )
                ]]),
            )
        ]

    await update.inline_query.answer(results, cache_time=60)


# ════════════════════════════════════════════════════════════════════════
#  WEB APP DATA
# ════════════════════════════════════════════════════════════════════════

async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Данные из Web App."""
    import json
    try:
        data = json.loads(update.message.web_app_data.data)
        player = data.get("player", "Unknown")
        wr     = data.get("wr", 0)
        kda    = data.get("kda", 0)

        await update.message.reply_text(
            f"📊 Ты посмотрел статистику игрока <b>{player}</b>\n"
            f"WR: {wr_emoji(wr)} {wr}%  ·  KDA: {kda_emoji(kda)} {kda}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )
    except Exception as e:
        logger.error(f"webapp data error: {e}")


# ════════════════════════════════════════════════════════════════════════
#  ФОНОВАЯ ЗАДАЧА — проверка отслеживаемых игроков
# ════════════════════════════════════════════════════════════════════════

async def check_tracked(app: Application) -> None:
    """
    Каждые 30 минут проверяем отслеживаемых игроков.
    Если WR изменился — уведомляем пользователя.
    """
    while True:
        await asyncio.sleep(30 * 60)  # 30 минут
        logger.info("Проверка отслеживаемых игроков...")

        for uid, udata in list(user_data.items()):
            if not udata.get("notifications", True):
                continue
            for pid in udata.get("tracked", []):
                try:
                    new = await fetch_player(str(pid))
                    if not new:
                        continue

                    prev = udata.get("last_seen", {}).get(str(pid), {})
                    new_wr = new["stats"]["winrate"]
                    old_wr = prev.get("winrate")

                    # Обновляем кэш
                    udata.setdefault("last_seen", {})[str(pid)] = {
                        "winrate": new_wr,
                        "kda": new["stats"]["avg_kda"],
                    }

                    # Уведомляем если WR изменился на 0.5%+
                    if old_wr and abs(new_wr - old_wr) >= 0.5:
                        direction = "вырос 📈" if new_wr > old_wr else "упал 📉"
                        await app.bot.send_message(
                            chat_id=uid,
                            text=(
                                f"🔔 <b>{new['profile']['name']}</b>\n"
                                f"WinRate {direction}: {old_wr}% → <b>{new_wr}%</b>"
                            ),
                            parse_mode=ParseMode.HTML,
                        )
                except Exception as e:
                    logger.error(f"Track check error uid={uid} pid={pid}: {e}")


# ════════════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ════════════════════════════════════════════════════════════════════════

def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан в .env!")

    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("player",  cmd_player))
    app.add_handler(CommandHandler("track",   cmd_track_list))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("top",     cmd_top))

    # Кнопки
    app.add_handler(CallbackQueryHandler(on_callback))

    # Инлайн
    app.add_handler(InlineQueryHandler(on_inline))

    # Web App данные
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))

    # Фоновая задача (проверка отслеживаемых)
    async def post_init(application: Application) -> None:
        asyncio.create_task(check_tracked(application))

    app.post_init = post_init

    logger.info("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
