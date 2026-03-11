# Crypto AI Agents Bot v4 - Subscription system + caching
# 1 free analysis per day, VIP = unlimited
# Admin can add/remove VIP users

import os
import time
import logging
import asyncio
import json
from datetime import datetime, date
from collections import defaultdict

from anthropic import AsyncAnthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from agents import (
    PriceAgent, SentimentAgent, OrchestratorAgent,
    fetch_top_coins, fetch_trending, fetch_global_data, fetch_fear_greed,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "30"))
FREE_ANALYSES_PER_DAY = int(os.getenv("FREE_ANALYSES_PER_DAY", "1"))

user_last_request = defaultdict(float)
coins_cache = {"data": [], "updated": 0}
CACHE_TTL = 300

# Subscription data (in-memory, resets on restart)
# For production: use Redis or database
vip_users = set()
user_daily_usage = {}  # {user_id: {"date": "2026-03-11", "count": 0}}

VIP_FILE = "/tmp/vip_users.json"


def load_vip():
    global vip_users
    try:
        with open(VIP_FILE, "r") as f:
            vip_users = set(json.load(f))
            logger.info(f"Loaded {len(vip_users)} VIP users")
    except:
        vip_users = set()


def save_vip():
    try:
        with open(VIP_FILE, "w") as f:
            json.dump(list(vip_users), f)
    except Exception as e:
        logger.error(f"Failed to save VIP: {e}")


def is_vip(user_id):
    return user_id in vip_users or user_id in ADMIN_IDS


def check_daily_limit(user_id):
    if is_vip(user_id):
        return True, 0
    today = date.today().isoformat()
    usage = user_daily_usage.get(user_id, {"date": "", "count": 0})
    if usage["date"] != today:
        usage = {"date": today, "count": 0}
        user_daily_usage[user_id] = usage
    if usage["count"] >= FREE_ANALYSES_PER_DAY:
        return False, FREE_ANALYSES_PER_DAY - usage["count"]
    return True, FREE_ANALYSES_PER_DAY - usage["count"]


def use_analysis(user_id):
    if is_vip(user_id):
        return
    today = date.today().isoformat()
    usage = user_daily_usage.get(user_id, {"date": "", "count": 0})
    if usage["date"] != today:
        usage = {"date": today, "count": 0}
    usage["count"] += 1
    user_daily_usage[user_id] = usage


def check_rate_limit(user_id):
    now = time.time()
    last = user_last_request[user_id]
    if now - last < RATE_LIMIT_SECONDS:
        return False, int(RATE_LIMIT_SECONDS - (now - last))
    user_last_request[user_id] = now
    return True, 0


async def get_coins():
    now = time.time()
    if now - coins_cache["updated"] > CACHE_TTL or not coins_cache["data"]:
        coins = await fetch_top_coins(50)
        if coins:
            coins_cache["data"] = coins
            coins_cache["updated"] = now
    return coins_cache["data"]


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Анализ монеты", callback_data="coins_page_0")],
        [InlineKeyboardButton("🌐 Обзор рынка (Топ-10)", callback_data="market_overview")],
        [InlineKeyboardButton("🔥 Trending сейчас", callback_data="trending")],
        [InlineKeyboardButton("😱 Fear & Greed", callback_data="fear_greed")],
        [InlineKeyboardButton("📈 Глобальный рынок", callback_data="global_data")],
        [InlineKeyboardButton("👤 Мой аккаунт", callback_data="my_account")],
        [InlineKeyboardButton("ℹ️ Об агентах", callback_data="about")],
    ])


def back_keyboard(callback="back_main"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data=callback)],
    ])


def subscribe_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Оформить VIP подписку", callback_data="subscribe")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
    ])


async def safe_edit_message(query, text, parse_mode="Markdown", reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Markdown error: {e}")
        clean = text.replace("*", "").replace("_", "").replace("`", "")
        try:
            await query.edit_message_text(clean, reply_markup=reply_markup)
        except Exception as e2:
            logger.error(f"Plain text also failed: {e2}")


async def start(update, context):
    user_id = update.effective_user.id
    vip_badge = " 💎 VIP" if is_vip(user_id) else ""
    allowed, remaining = check_daily_limit(user_id)
    limit_info = "Безлимитный доступ" if is_vip(user_id) else f"Бесплатных анализов сегодня: {remaining}"

    await update.message.reply_text(
        f"🤖 *Crypto AI Agents Bot v4*{vip_badge}\n\n"
        f"Система AI-агентов на базе Claude:\n"
        f"• 📈 *PriceAgent* — технический анализ\n"
        f"• 🧠 *SentimentAgent* — настроения рынка\n"
        f"• 🎯 *OrchestratorAgent* — рекомендации\n\n"
        f"📡 Реальные данные CoinGecko\n"
        f"🪙 Топ-50 монет по капитализации\n"
        f"⚡ Фьючерсы, таймфреймы, точки входа\n\n"
        f"📊 {limit_info}\n\n"
        f"Выберите действие:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def my_account(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or "нет"
    is_v = is_vip(user_id)
    allowed, remaining = check_daily_limit(user_id)
    today_usage = user_daily_usage.get(user_id, {"date": "", "count": 0})
    used_today = today_usage["count"] if today_usage["date"] == date.today().isoformat() else 0

    status = "💎 VIP (безлимитный доступ)" if is_v else "🆓 Бесплатный план"
    text = (
        f"👤 *Мой аккаунт*\n\n"
        f"ID: `{user_id}`\n"
        f"Username: @{username}\n"
        f"Статус: {status}\n\n"
    )
    if not is_v:
        text += (
            f"📊 Использовано сегодня: {used_today}/{FREE_ANALYSES_PER_DAY}\n"
            f"📊 Осталось: {max(0, remaining)}\n\n"
            f"💎 *VIP подписка — $5/мес*\n"
            f"Безлимитные анализы всех монет\n"
        )

    buttons = []
    if not is_v:
        buttons.append([InlineKeyboardButton("💎 Оформить VIP", callback_data="subscribe")])
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])

    await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(buttons))


async def subscribe(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    text = (
        f"💎 *VIP Подписка — $5/мес*\n\n"
        f"Что входит:\n"
        f"• Безлимитные анализы всех монет\n"
        f"• Детальные рекомендации по таймфреймам\n"
        f"• Фьючерсные сигналы с точками входа\n"
        f"• Приоритетная скорость анализа\n\n"
        f"Способы оплаты:\n\n"
        f"💰 *USDT (TRC-20):*\n"
        f"`Адрес будет указан после настройки`\n\n"
        f"💎 *TON:*\n"
        f"`Адрес будет указан после настройки`\n\n"
        f"После оплаты отправьте скриншот или хэш транзакции "
        f"администратору и ваш ID: `{user_id}`\n\n"
        f"Или напишите /support для связи с админом."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Связаться с админом", callback_data="contact_admin")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
    ])
    await safe_edit_message(query, text, reply_markup=keyboard)


async def contact_admin(update, context):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(
        query,
        "📞 Для оформления VIP подписки напишите администратору.\n\n"
        "Укажите ваш ID при обращении.",
        reply_markup=back_keyboard(),
    )


async def coins_page(update, context):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("_")[-1])
    coins = await get_coins()
    if not coins:
        await safe_edit_message(query, "Не удалось загрузить. Попробуйте через минуту.", reply_markup=back_keyboard())
        return
    per_page = 15
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_coins = coins[start_idx:end_idx]
    total_pages = (len(coins) + per_page - 1) // per_page

    buttons = []
    row = []
    for c in page_coins:
        emoji = "🟢" if c["change_24h"] > 0 else "🔴"
        row.append(InlineKeyboardButton(
            f"{emoji}{c['symbol']}",
            callback_data=f"analyze_{c['symbol']}_{c['id']}"
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"coins_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"coins_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])

    user_id = query.from_user.id
    vip_badge = " 💎" if is_vip(user_id) else ""
    allowed, remaining = check_daily_limit(user_id)
    limit_text = "" if is_vip(user_id) else f"\n📊 Бесплатных анализов: {remaining}"

    await safe_edit_message(
        query,
        f"🪙 *Топ-50 монет{vip_badge}* (стр. {page+1}/{total_pages})\n"
        f"🟢 рост / 🔴 падение за 24ч{limit_text}\n\nВыберите монету:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def analyze_coin(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    parts = query.data.split("_")
    coin = parts[1]
    coin_id = parts[2] if len(parts) > 2 and parts[2] else None

    # Check daily limit
    allowed, remaining = check_daily_limit(user_id)
    if not allowed:
        await safe_edit_message(
            query,
            f"🔒 *Лимит исчерпан*\n\n"
            f"Вы использовали {FREE_ANALYSES_PER_DAY} бесплатный анализ сегодня.\n\n"
            f"💎 Оформите VIP подписку за $5/мес для безлимитного доступа\n"
            f"или подождите до завтра.",
            reply_markup=subscribe_keyboard(),
        )
        return

    # Rate limit
    rate_ok, wait = check_rate_limit(user_id)
    if not rate_ok:
        await safe_edit_message(
            query, f"⏱ Подождите {wait} сек.",
            reply_markup=back_keyboard("coins_page_0"),
        )
        return

    await safe_edit_message(
        query,
        f"⏳ Агенты анализируют *{coin}*...\n\n"
        f"📡 Загрузка данных CoinGecko\n"
        f"🔄 Параллельный анализ (30-90 сек)",
    )

    try:
        price_data, sentiment_data = await asyncio.gather(
            PriceAgent(client).analyze(coin, coin_id),
            SentimentAgent(client).analyze(coin, coin_id),
        )
        final = await OrchestratorAgent(client).synthesize(coin, price_data, sentiment_data)

        # Count usage AFTER successful analysis
        use_analysis(user_id)

        timestamp = datetime.now().strftime("%H:%M %d.%m.%Y")
        raw = price_data.get("raw_data", {})
        price_line = ""
        if raw and raw.get("price", 0) > 0:
            price_line = (
                f"💰 ${raw.get('price', 0):,.6f} "
                f"({raw.get('change_24h', 0):+.2f}% 24ч)\n"
            )

        text = (
            f"📊 *Анализ {coin}* — {timestamp}\n"
            f"{price_line}\n"
            f"📈 *PriceAgent:*\n{price_data['summary']}\n\n"
            f"🧠 *SentimentAgent:*\n{sentiment_data['summary']}\n\n"
            f"🎯 *OrchestratorAgent:*\n{final['recommendation']}\n\n"
            f"📡 _CoinGecko + Alternative.me_\n"
            f"⚠️ _Не является финансовым советом._"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data=f"analyze_{coin}_{coin_id or ''}")],
            [InlineKeyboardButton("🪙 Другая монета", callback_data="coins_page_0")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
        ])
        await safe_edit_message(query, text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"analyze error {coin}: {e}", exc_info=True)
        await safe_edit_message(
            query, f"Ошибка анализа {coin}. Попробуйте через минуту.",
            reply_markup=back_keyboard("coins_page_0"),
        )


async def market_overview(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    rate_ok, wait = check_rate_limit(user_id)
    if not rate_ok:
        await safe_edit_message(query, f"⏱ Подождите {wait} сек.", reply_markup=back_keyboard())
        return
    await safe_edit_message(query, "⏳ Загрузка обзора рынка...")
    try:
        coins = await get_coins()
        top10 = coins[:10] if coins else []
        overview = await OrchestratorAgent(client).market_overview(top10)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="market_overview")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
        ])
        await safe_edit_message(
            query,
            f"🌐 *Обзор рынка*\n\n{overview}\n\n"
            f"📡 _CoinGecko_\n⚠️ _Не является финансовым советом._",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"market_overview error: {e}", exc_info=True)
        await safe_edit_message(query, "Ошибка. Попробуйте через минуту.", reply_markup=back_keyboard())


async def trending(update, context):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(query, "⏳ Загрузка трендовых монет...")
    try:
        coins = await fetch_trending()
        if not coins:
            await safe_edit_message(query, "Не удалось загрузить.", reply_markup=back_keyboard())
            return
        lines = []
        for i, c in enumerate(coins, 1):
            rank = f"#{c['market_cap_rank']}" if c.get('market_cap_rank') else "new"
            lines.append(f"{i}. *{c['symbol']}* ({c['name']}) — {rank}")
        buttons = []
        for c in coins[:6]:
            cid = c.get("id", "")
            buttons.append([InlineKeyboardButton(
                f"📊 Анализ {c['symbol']}", callback_data=f"analyze_{c['symbol']}_{cid}"
            )])
        buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="trending")])
        buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])
        await safe_edit_message(
            query,
            f"🔥 *Trending сейчас*\n\n" + "\n".join(lines) + "\n\n📡 _CoinGecko_",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        logger.error(f"trending error: {e}", exc_info=True)
        await safe_edit_message(query, "Ошибка.", reply_markup=back_keyboard())


async def global_data(update, context):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(query, "⏳ Загрузка глобальных данных...")
    try:
        data = await fetch_global_data()
        fg, fg_label = await fetch_fear_greed()
        if not data:
            await safe_edit_message(query, "Не удалось загрузить.", reply_markup=back_keyboard())
            return
        text = (
            f"📈 *Глобальный крипторынок*\n\n"
            f"💰 Капитализация: *${data['total_market_cap']}T*\n"
            f"📊 Объём 24ч: *${data['total_volume']}B*\n"
            f"📈 Изменение 24ч: *{data['market_cap_change_24h']:+.2f}%*\n\n"
            f"🟡 BTC: *{data['btc_dominance']}%*\n"
            f"🔵 ETH: *{data['eth_dominance']}%*\n\n"
            f"😱 Fear & Greed: *{fg}/100 ({fg_label})*\n"
            f"🪙 Активных монет: *{data['active_coins']:,}*\n\n"
            f"📡 _CoinGecko + Alternative.me_"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="global_data")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
        ])
        await safe_edit_message(query, text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"global_data error: {e}", exc_info=True)
        await safe_edit_message(query, "Ошибка.", reply_markup=back_keyboard())


async def fear_greed(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    rate_ok, wait = check_rate_limit(user_id)
    if not rate_ok:
        await safe_edit_message(query, f"⏱ Подождите {wait} сек.", reply_markup=back_keyboard())
        return
    await safe_edit_message(query, "⏳ Загрузка Fear & Greed Index...")
    try:
        fg_text = await SentimentAgent(client).fear_greed_only()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="fear_greed")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
        ])
        await safe_edit_message(
            query,
            f"😱 *Fear & Greed*\n\n{fg_text}\n\n📡 _Alternative.me_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"fear_greed error: {e}", exc_info=True)
        await safe_edit_message(query, "Ошибка.", reply_markup=back_keyboard())


async def about(update, context):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(
        query,
        "ℹ️ *Crypto AI Agents Bot v4*\n\n"
        "📈 *PriceAgent* — RSI, MA7/25/50/99/200, ATH, "
        "краткосрок/среднесрок/долгосрок, фьючерсы\n\n"
        "🧠 *SentimentAgent* — Fear & Greed, "
        "community/developer scores\n\n"
        "🎯 *OrchestratorAgent* — сигнал, таймфреймы, "
        "точки входа\n\n"
        "🪙 Топ-50 монет | 🔥 Trending | 📈 Глобальный рынок\n"
        "📡 CoinGecko + Alternative.me\n"
        "💎 VIP — безлимитный доступ",
        reply_markup=back_keyboard(),
    )


# Admin commands
async def cmd_addvip(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /addvip USER_ID")
        return
    try:
        target_id = int(context.args[0])
        vip_users.add(target_id)
        save_vip()
        await update.message.reply_text(f"VIP добавлен: {target_id}")
    except ValueError:
        await update.message.reply_text("Неверный ID.")


async def cmd_removevip(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /removevip USER_ID")
        return
    try:
        target_id = int(context.args[0])
        vip_users.discard(target_id)
        save_vip()
        await update.message.reply_text(f"VIP удален: {target_id}")
    except ValueError:
        await update.message.reply_text("Неверный ID.")


async def cmd_listvip(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа.")
        return
    if not vip_users:
        await update.message.reply_text("VIP список пуст.")
        return
    text = "VIP пользователи:\n" + "\n".join([str(uid) for uid in vip_users])
    await update.message.reply_text(text)


async def cmd_stats(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа.")
        return
    today = date.today().isoformat()
    active_today = sum(1 for u in user_daily_usage.values() if u["date"] == today)
    total_analyses = sum(u["count"] for u in user_daily_usage.values() if u["date"] == today)
    text = (
        f"📊 Статистика:\n"
        f"VIP: {len(vip_users)}\n"
        f"Активных сегодня: {active_today}\n"
        f"Анализов сегодня: {total_analyses}"
    )
    await update.message.reply_text(text)


async def back_main(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    vip_badge = " 💎" if is_vip(user_id) else ""
    await safe_edit_message(
        query,
        f"🤖 *Crypto AI Agents Bot v4{vip_badge}*\n\nВыберите действие:",
        reply_markup=main_keyboard(),
    )


async def error_handler(update, context):
    logger.error(f"Unhandled: {context.error}", exc_info=context.error)


def main():
    load_vip()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addvip", cmd_addvip))
    app.add_handler(CommandHandler("removevip", cmd_removevip))
    app.add_handler(CommandHandler("listvip", cmd_listvip))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(coins_page, pattern=r"^coins_page_"))
    app.add_handler(CallbackQueryHandler(analyze_coin, pattern=r"^analyze_"))
    app.add_handler(CallbackQueryHandler(market_overview, pattern=r"^market_overview$"))
    app.add_handler(CallbackQueryHandler(trending, pattern=r"^trending$"))
    app.add_handler(CallbackQueryHandler(global_data, pattern=r"^global_data$"))
    app.add_handler(CallbackQueryHandler(fear_greed, pattern=r"^fear_greed$"))
    app.add_handler(CallbackQueryHandler(about, pattern=r"^about$"))
    app.add_handler(CallbackQueryHandler(my_account, pattern=r"^my_account$"))
    app.add_handler(CallbackQueryHandler(subscribe, pattern=r"^subscribe$"))
    app.add_handler(CallbackQueryHandler(contact_admin, pattern=r"^contact_admin$"))
    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_error_handler(error_handler)
    logger.info("Crypto AI Agents Bot v4 - subscriptions + caching")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
