# Crypto AI Agents Bot v3 - Top 50 coins, detailed analysis, trending, global data

import os
import time
import logging
import asyncio
from datetime import datetime
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

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "30"))
user_last_request = defaultdict(float)
coins_cache = {"data": [], "updated": 0}
CACHE_TTL = 300


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
        [InlineKeyboardButton("ℹ️ Об агентах", callback_data="about")],
    ])


def back_keyboard(callback="back_main"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data=callback)],
    ])


async def safe_edit_message(query, text, parse_mode="Markdown", reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Markdown error, fallback: {e}")
        clean = text.replace("*", "").replace("_", "").replace("`", "")
        try:
            await query.edit_message_text(clean, reply_markup=reply_markup)
        except Exception as e2:
            logger.error(f"Plain text also failed: {e2}")


async def start(update, context):
    await update.message.reply_text(
        "🤖 *Crypto AI Agents Bot v3*\n\n"
        "Система AI-агентов на базе Claude:\n"
        "• 📈 *PriceAgent* — технический анализ\n"
        "• 🧠 *SentimentAgent* — настроения рынка\n"
        "• 🎯 *OrchestratorAgent* — рекомендации\n\n"
        "📡 Реальные данные CoinGecko\n"
        "🪙 Топ-50 монет по капитализации\n"
        "⚡ Фьючерсы, таймфреймы, точки входа\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def coins_page(update, context):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("_")[-1])
    coins = await get_coins()
    if not coins:
        await safe_edit_message(query, "Не удалось загрузить список монет.", reply_markup=back_keyboard())
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
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"coins_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️ Далее", callback_data=f"coins_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])

    await safe_edit_message(
        query,
        f"🪙 *Топ-50 монет* (стр. {page+1}/{total_pages})\n"
        f"🟢 рост / 🔴 падение за 24ч\n\nВыберите монету:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def analyze_coin(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    parts = query.data.split("_")
    coin = parts[1]
    coin_id = parts[2] if len(parts) > 2 else None

    allowed, wait_seconds = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(
            query, f"⏱ Подождите {wait_seconds} сек.",
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
        timestamp = datetime.now().strftime("%H:%M %d.%m.%Y")
        raw = price_data.get("raw_data", {})
        price_line = ""
        if raw:
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
            query, f"Ошибка анализа {coin}. Попробуйте позже.",
            reply_markup=back_keyboard("coins_page_0"),
        )


async def market_overview(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    allowed, wait = check_rate_limit(user_id)
    if not allowed:
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
        await safe_edit_message(query, "Ошибка загрузки обзора.", reply_markup=back_keyboard())


async def trending(update, context):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(query, "⏳ Загрузка трендовых монет...")
    try:
        coins = await fetch_trending()
        if not coins:
            await safe_edit_message(query, "Не удалось загрузить тренды.", reply_markup=back_keyboard())
            return
        lines = []
        for i, c in enumerate(coins, 1):
            rank = f"#{c['market_cap_rank']}" if c['market_cap_rank'] else "new"
            lines.append(f"{i}. *{c['symbol']}* ({c['name']}) — {rank}")
        buttons = []
        for c in coins[:6]:
            buttons.append([InlineKeyboardButton(
                f"📊 Анализ {c['symbol']}", callback_data=f"analyze_{c['symbol']}_"
            )])
        buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="trending")])
        buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])
        await safe_edit_message(
            query,
            f"🔥 *Trending сейчас*\n\n" + "\n".join(lines) + "\n\n📡 _CoinGecko Trending_",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        logger.error(f"trending error: {e}", exc_info=True)
        await safe_edit_message(query, "Ошибка загрузки трендов.", reply_markup=back_keyboard())


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
            f"🟡 Доминация BTC: *{data['btc_dominance']}%*\n"
            f"🔵 Доминация ETH: *{data['eth_dominance']}%*\n\n"
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
        await safe_edit_message(query, "Ошибка загрузки.", reply_markup=back_keyboard())


async def fear_greed(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    allowed, wait = check_rate_limit(user_id)
    if not allowed:
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
        "ℹ️ *Crypto AI Agents Bot v3*\n\n"
        "📈 *PriceAgent*\n"
        "Технический анализ: RSI, MA7/25/50/99/200, "
        "объёмы, уровни, ATH. Рекомендации по "
        "краткосроку, среднесроку, долгосроку и фьючерсам.\n\n"
        "🧠 *SentimentAgent*\n"
        "Fear & Greed, настроения сообщества, "
        "community/developer scores.\n\n"
        "🎯 *OrchestratorAgent*\n"
        "Синтез: сигнал, уверенность, таймфреймы, "
        "фьючерсные рекомендации с точками входа.\n\n"
        "🪙 Топ-50 монет по капитализации\n"
        "🔥 Trending монеты в реальном времени\n"
        "📈 Глобальные данные рынка\n"
        "📡 CoinGecko + Alternative.me\n"
        "⚡ Параллельная работа агентов",
        reply_markup=back_keyboard(),
    )


async def back_main(update, context):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(
        query,
        "🤖 *Crypto AI Agents Bot v3*\n\nВыберите действие:",
        reply_markup=main_keyboard(),
    )


async def error_handler(update, context):
    logger.error(f"Unhandled: {context.error}", exc_info=context.error)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(coins_page, pattern=r"^coins_page_"))
    app.add_handler(CallbackQueryHandler(analyze_coin, pattern=r"^analyze_"))
    app.add_handler(CallbackQueryHandler(market_overview, pattern=r"^market_overview$"))
    app.add_handler(CallbackQueryHandler(trending, pattern=r"^trending$"))
    app.add_handler(CallbackQueryHandler(global_data, pattern=r"^global_data$"))
    app.add_handler(CallbackQueryHandler(fear_greed, pattern=r"^fear_greed$"))
    app.add_handler(CallbackQueryHandler(about, pattern=r"^about$"))
    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_error_handler(error_handler)
    logger.info("Crypto AI Agents Bot v3 started - REAL DATA + TOP 50")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
