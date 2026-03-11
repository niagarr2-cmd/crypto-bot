"""
Crypto AI Agents — Telegram бот.

v2: Реальные данные, топ-50 монет, лимит 1 анализ/сутки для free users.
"""

import os
import time
import logging
import asyncio
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
    get_top_coins_cached, find_coin_in_list,
)

# ─────────────────────────────────────────────
# Конфигурация
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Лимиты
FREE_ANALYSES_PER_DAY = int(os.getenv("FREE_ANALYSES_PER_DAY", "1"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "30"))

# Администраторы (через запятую: "123456,789012")
_admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: set[int] = set()
for _a in _admin_ids_raw.split(","):
    _a = _a.strip()
    if _a.isdigit():
        ADMIN_IDS.add(int(_a))

logger.info(f"Admin IDs: {ADMIN_IDS}")
logger.info(f"Free analyses per day: {FREE_ANALYSES_PER_DAY}")

# ─────────────────────────────────────────────
# State: rate limit + daily usage
# ─────────────────────────────────────────────

# user_id → timestamp последнего запроса
user_last_request: dict[int, float] = defaultdict(float)

# user_id → (дата, кол-во анализов сегодня)
user_daily_usage: dict[int, tuple[date, int]] = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    if is_admin(user_id):
        return True, 0
    now = time.time()
    elapsed = now - user_last_request[user_id]
    if elapsed < RATE_LIMIT_SECONDS:
        return False, int(RATE_LIMIT_SECONDS - elapsed)
    user_last_request[user_id] = now
    return True, 0


def check_daily_limit(user_id: int) -> tuple[bool, int]:
    """
    Проверка дневного лимита.
    Возвращает (разрешено, использовано_сегодня).
    """
    if is_admin(user_id):
        return True, 0

    today = date.today()
    if user_id in user_daily_usage:
        saved_date, count = user_daily_usage[user_id]
        if saved_date == today:
            if count >= FREE_ANALYSES_PER_DAY:
                return False, count
            return True, count
    return True, 0


def increment_daily_usage(user_id: int):
    if is_admin(user_id):
        return
    today = date.today()
    if user_id in user_daily_usage:
        saved_date, count = user_daily_usage[user_id]
        if saved_date == today:
            user_daily_usage[user_id] = (today, count + 1)
            return
    user_daily_usage[user_id] = (today, 1)


# ─────────────────────────────────────────────
# Клавиатуры
# ─────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Анализ монеты", callback_data="choose_coin")],
        [InlineKeyboardButton("🌐 Обзор рынка", callback_data="market_overview")],
        [InlineKeyboardButton("😱 Fear & Greed", callback_data="fear_greed")],
        [InlineKeyboardButton("ℹ️ Об агентах", callback_data="about")],
    ])


def back_keyboard(callback: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data=callback)],
    ])


def coins_keyboard(coins: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    """
    Клавиатура с монетами — постраничная, по 5 монет на строку, 2 строки = 10 на страницу.
    """
    per_page = 10
    start = page * per_page
    page_coins = coins[start: start + per_page]

    rows = []
    # По 5 монет в строку
    for i in range(0, len(page_coins), 5):
        row = []
        for c in page_coins[i: i + 5]:
            sym = c["symbol"].upper()
            row.append(InlineKeyboardButton(sym, callback_data=f"analyze_{sym}"))
        rows.append(row)

    # Навигация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"coins_page_{page - 1}"))
    total_pages = (len(coins) + per_page - 1) // per_page
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if start + per_page < len(coins):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"coins_page_{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────

async def safe_edit_message(query, text: str, parse_mode: str = "Markdown", reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Markdown parse error, fallback: {e}")
        clean = text.replace("*", "").replace("_", "").replace("`", "")
        try:
            await query.edit_message_text(clean, reply_markup=reply_markup)
        except Exception as e2:
            logger.error(f"Failed to send even plain text: {e2}")


# ─────────────────────────────────────────────
# Хэндлеры
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admin_note = " 👑 _Режим администратора_\n" if is_admin(user_id) else ""
    limit_note = (
        "_Без лимитов (admin)_"
        if is_admin(user_id)
        else f"_Бесплатно: {FREE_ANALYSES_PER_DAY} анализ/день_"
    )

    await update.message.reply_text(
        "🤖 *Crypto AI Agents Bot*\n\n"
        f"{admin_note}"
        "Система из 3 AI-агентов на базе Claude:\n"
        "• 📈 *PriceAgent* — технический анализ\n"
        "• 🧠 *SentimentAgent* — настроения рынка\n"
        "• 🎯 *OrchestratorAgent* — итоговая рекомендация\n\n"
        "📡 Данные: CoinGecko API (реальные)\n"
        f"{limit_note}\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def choose_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await safe_edit_message(query, "⏳ Загружаю топ-50 монет...")

    coins = await get_top_coins_cached(50)
    if not coins:
        await safe_edit_message(
            query,
            "❌ Не удалось загрузить список монет. Попробуйте позже.",
            reply_markup=back_keyboard(),
        )
        return

    # Сохраним список монет в контексте для пагинации
    context.bot_data["top_coins"] = coins

    await safe_edit_message(
        query,
        f"🪙 Топ-{len(coins)} монет по капитализации\nВыберите для анализа:",
        reply_markup=coins_keyboard(coins, page=0),
    )


async def coins_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение страниц монет."""
    query = update.callback_query
    await query.answer()

    page = int(query.data.split("_")[-1])
    coins = context.bot_data.get("top_coins", [])

    if not coins:
        coins = await get_top_coins_cached(50)
        context.bot_data["top_coins"] = coins

    if not coins:
        await safe_edit_message(query, "❌ Список монет недоступен.", reply_markup=back_keyboard())
        return

    await safe_edit_message(
        query,
        f"🪙 Топ-{len(coins)} монет — страница {page + 1}\nВыберите для анализа:",
        reply_markup=coins_keyboard(coins, page=page),
    )


async def analyze_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Полный анализ монеты тремя агентами."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    coin = query.data.split("_", 1)[1]  # analyze_BTC → BTC

    # Rate limit
    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(
            query,
            f"⏱ Подождите {wait_sec} сек перед следующим запросом.",
            reply_markup=back_keyboard("choose_coin"),
        )
        return

    # Daily limit
    daily_ok, used_today = check_daily_limit(user_id)
    if not daily_ok:
        await safe_edit_message(
            query,
            f"🚫 Дневной лимит исчерпан ({FREE_ANALYSES_PER_DAY} анализ/день).\n"
            f"Приходите завтра! 😊\n\n"
            f"_Хотите больше анализов? Свяжитесь с администратором._",
            reply_markup=back_keyboard(),
        )
        return

    await safe_edit_message(
        query,
        f"⏳ Агенты анализируют *{coin}*...\n\n"
        f"📡 Загружаю данные с CoinGecko\n"
        f"🔄 PriceAgent + SentimentAgent работают параллельно\n"
        f"⏱ Обычно 30-60 секунд",
    )

    try:
        # Получить данные монеты
        coins = await get_top_coins_cached(50)
        coin_data = find_coin_in_list(coin, coins) if coins else None

        if not coin_data:
            logger.warning(f"Coin {coin} not found in top-50, using fallback")

        # Параллельный анализ
        price_data, sentiment_data = await asyncio.gather(
            PriceAgent(client).analyze(coin, coin_data),
            SentimentAgent(client).analyze(coin),
        )

        final = await OrchestratorAgent(client).synthesize(coin, price_data, sentiment_data)

        # Засчитать использование
        increment_daily_usage(user_id)

        # Формируем ответ
        timestamp = datetime.now().strftime("%H:%M %d.%m.%Y")
        sep = "─" * 28

        price_str = f"${price_data['price']:,.4f}" if price_data.get('price') else "N/A"
        change_str = f"{price_data['change_24h']:+.2f}%" if price_data.get('change_24h') is not None else "N/A"
        data_source = "📡 Реальные данные (CoinGecko)" if price_data.get('is_real') else "⚠️ Демо-данные"

        # Остаток лимита
        if is_admin(user_id):
            limit_str = "👑 Admin — без лимитов"
        else:
            _, new_used = user_daily_usage.get(user_id, (date.today(), 0))
            remaining = FREE_ANALYSES_PER_DAY - new_used
            limit_str = f"Использовано сегодня: {new_used}/{FREE_ANALYSES_PER_DAY}"

        text = (
            f"📊 *Анализ {coin}* — {timestamp}\n"
            f"💰 Цена: *{price_str}* ({change_str})\n"
            f"{sep}\n\n"
            f"📈 *PriceAgent:*\n{price_data['summary']}\n\n"
            f"🧠 *SentimentAgent:*\n{sentiment_data['summary']}\n\n"
            f"🎯 *OrchestratorAgent:*\n{final['recommendation']}\n\n"
            f"{sep}\n"
            f"{data_source}\n"
            f"_{limit_str}_\n"
            f"_Не является финансовым советом_"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🪙 Другая монета", callback_data="choose_coin")],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data="back_main")],
        ])

        await safe_edit_message(query, text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"analyze_coin error for {coin}: {e}", exc_info=True)
        await safe_edit_message(
            query,
            f"❌ Ошибка при анализе {coin}.\n"
            f"Попробуйте позже.\n\nОшибка: {type(e).__name__}",
            reply_markup=back_keyboard("choose_coin"),
        )


async def market_overview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(query, f"⏱ Подождите {wait_sec} сек.", reply_markup=back_keyboard())
        return

    await safe_edit_message(query, "⏳ Загружаю данные рынка с CoinGecko...")

    try:
        coins = await get_top_coins_cached(50)
        if not coins:
            await safe_edit_message(query, "❌ Не удалось загрузить данные рынка.", reply_markup=back_keyboard())
            return

        overview = await OrchestratorAgent(client).market_overview(coins)
        sep = "─" * 28

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="market_overview")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
        ])

        await safe_edit_message(
            query,
            f"🌐 *Обзор рынка*\n{sep}\n\n"
            f"{overview}\n\n"
            f"{sep}\n"
            f"📡 _Реальные данные (CoinGecko)_\n"
            f"_Не является финансовым советом_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"market_overview error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при получении обзора.", reply_markup=back_keyboard())


async def fear_greed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(query, f"⏱ Подождите {wait_sec} сек.", reply_markup=back_keyboard())
        return

    await safe_edit_message(query, "⏳ Получаю Fear & Greed Index...")

    try:
        fg_text = await SentimentAgent(client).fear_greed_only()
        sep = "─" * 28

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="fear_greed")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
        ])

        await safe_edit_message(
            query,
            f"😱 *Fear & Greed*\n{sep}\n\n{fg_text}",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"fear_greed error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при получении индекса.", reply_markup=back_keyboard())


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(
        query,
        "ℹ️ *О системе агентов*\n\n"
        "📈 *PriceAgent*\n"
        "Реальные данные с CoinGecko: цена, объём, изменение 24ч, "
        "RSI(14) из OHLC данных, High/Low уровни.\n\n"
        "🧠 *SentimentAgent*\n"
        "Fear & Greed Index от Alternative.me (обновляется ежедневно).\n\n"
        "🎯 *OrchestratorAgent*\n"
        "Синтезирует данные обоих агентов, даёт рекомендацию "
        "с уровнем уверенности и ключевыми факторами.\n\n"
        "⚡️ PriceAgent и SentimentAgent работают *параллельно*.\n"
        "🤖 Все агенты на базе *Claude* от Anthropic.\n"
        "📡 Данные: CoinGecko API + Alternative.me",
        reply_markup=back_keyboard(),
    )


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(
        query,
        "🤖 *Crypto AI Agents Bot*\n\nВыберите действие:",
        reply_markup=main_keyboard(),
    )


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заглушка для неактивных кнопок (например, номер страницы)."""
    query = update.callback_query
    await query.answer()


# Admin команды
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика использования — только для admin."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔️ Нет доступа.")
        return

    today = date.today()
    active_users = sum(
        1 for uid, (d, c) in user_daily_usage.items() if d == today
    )
    total_analyses = sum(
        c for uid, (d, c) in user_daily_usage.items() if d == today
    )

    await update.message.reply_text(
        f"📊 *Статистика сегодня*\n"
        f"Активных пользователей: {active_users}\n"
        f"Всего анализов: {total_analyses}\n"
        f"Лимит для free: {FREE_ANALYSES_PER_DAY}/день",
        parse_mode="Markdown",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)


# ─────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", cmd_stats))

    app.add_handler(CallbackQueryHandler(choose_coin, pattern=r"^choose_coin$"))
    app.add_handler(CallbackQueryHandler(coins_page, pattern=r"^coins_page_\d+$"))
    app.add_handler(CallbackQueryHandler(analyze_coin, pattern=r"^analyze_"))
    app.add_handler(CallbackQueryHandler(market_overview, pattern=r"^market_overview$"))
    app.add_handler(CallbackQueryHandler(fear_greed, pattern=r"^fear_greed$"))
    app.add_handler(CallbackQueryHandler(about, pattern=r"^about$"))
    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))

    app.add_error_handler(error_handler)

    logger.info("🤖 Crypto AI Agents Bot v2 запущен")
    logger.info(f"   Монеты: топ-50 с CoinGecko (реальные данные)")
    logger.info(f"   Rate limit: {RATE_LIMIT_SECONDS} сек")
    logger.info(f"   Free analyses/day: {FREE_ANALYSES_PER_DAY}")
    logger.info(f"   Admins: {ADMIN_IDS}")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
