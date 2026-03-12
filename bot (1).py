"""
Crypto AI Agents — Telegram бот.

v3: Платежи (Stars/ЮКасса/TON), тарифы Free/Premium/VIP, топ-50 монет.
"""

import os
import time
import logging
import asyncio
from datetime import datetime, date
from collections import defaultdict

from anthropic import AsyncAnthropic
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, PreCheckoutQuery,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
    ContextTypes,
)

from agents import (
    PriceAgent, SentimentAgent, OrchestratorAgent,
    PolymarketAgent,
    get_top_coins_cached, find_coin_in_list,
)
from payments import (
    Plan, PLAN_CONFIG, YUKASSA_TOKEN, TON_WALLET,
    PREMIUM_STARS, VIP_STARS, PREMIUM_RUB, VIP_RUB,
    PREMIUM_TON, VIP_TON,
    get_user_plan, get_subscription_info,
    activate_subscription, can_use_analysis, can_use_trading,
    get_stats, user_subscriptions,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

FREE_ANALYSES_PER_DAY = int(os.getenv("FREE_ANALYSES_PER_DAY", "1"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "30"))

_admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: set[int] = set()
for _a in _admin_ids_raw.split(","):
    _a = _a.strip()
    if _a.isdigit():
        ADMIN_IDS.add(int(_a))

logger.info(f"Admin IDs: {ADMIN_IDS}")

user_last_request: dict[int, float] = defaultdict(float)
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
    if is_admin(user_id):
        return True, 0

    plan = get_user_plan(user_id)
    config = PLAN_CONFIG[plan]
    limit = config["analyses_per_day"]

    if limit >= 999:
        return True, 0

    today = date.today()
    if user_id in user_daily_usage:
        saved_date, count = user_daily_usage[user_id]
        if saved_date == today:
            if count >= limit:
                return False, count
            return True, count
    return True, 0


def increment_daily_usage(user_id: int):
    if is_admin(user_id):
        return
    plan = get_user_plan(user_id)
    if PLAN_CONFIG[plan]["analyses_per_day"] >= 999:
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
        [InlineKeyboardButton("📊 Анализ монеты (топ-50)", callback_data="choose_coin")],
        [InlineKeyboardButton("🌐 Обзор рынка",            callback_data="market_overview")],
        [InlineKeyboardButton("🔥 Trending",               callback_data="trending")],
        [InlineKeyboardButton("😱 Fear & Greed",           callback_data="fear_greed")],
        [InlineKeyboardButton("📈 Глобальный рынок",       callback_data="global_market")],
        [InlineKeyboardButton("📉 Trading / Фьючерсы",     callback_data="trading")],
        [InlineKeyboardButton("🎯 Polymarket",              callback_data="polymarket")],
        [InlineKeyboardButton("👤 Мой аккаунт",            callback_data="my_account")],
        [InlineKeyboardButton("💳 Подписка",               callback_data="subscription")],
        [InlineKeyboardButton("ℹ️ Об агентах",             callback_data="about")],
    ])


def back_keyboard(callback: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data=callback)],
    ])


def coins_keyboard(coins: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    per_page = 10
    start = page * per_page
    page_coins = coins[start: start + per_page]

    rows = []
    for i in range(0, len(page_coins), 5):
        row = []
        for c in page_coins[i: i + 5]:
            sym = c["symbol"].upper()
            row.append(InlineKeyboardButton(sym, callback_data=f"analyze_{sym}"))
        rows.append(row)

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


def trading_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Long сигналы",  callback_data="trading_long")],
        [InlineKeyboardButton("🔴 Short сигналы", callback_data="trading_short")],
        [InlineKeyboardButton("📊 Топ фьючерсы",  callback_data="trading_top")],
        [InlineKeyboardButton("⬅️ Назад",         callback_data="back_main")],
    ])


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐️ Premium — 199₽/мес", callback_data="buy_premium")],
        [InlineKeyboardButton("💎 VIP — 499₽/мес",      callback_data="buy_vip")],
        [InlineKeyboardButton("⬅️ Назад",               callback_data="back_main")],
    ])


def payment_method_keyboard(plan: str) -> InlineKeyboardMarkup:
    stars = PREMIUM_STARS if plan == "premium" else VIP_STARS
    ton = PREMIUM_TON if plan == "premium" else VIP_TON
    rub = 199 if plan == "premium" else 499

    rows = [
        [InlineKeyboardButton(f"⭐️ Telegram Stars ({stars} Stars)", callback_data=f"pay_stars_{plan}")],
    ]
    if YUKASSA_TOKEN:
        rows.append([InlineKeyboardButton(f"💳 ЮКасса ({rub}₽)", callback_data=f"pay_yukassa_{plan}")])
    if TON_WALLET:
        rows.append([InlineKeyboardButton(f"💎 TON ({ton} TON)", callback_data=f"pay_ton_{plan}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="subscription")])
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
# Хэндлеры — старт и навигация
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    plan = get_user_plan(user_id)
    plan_name = PLAN_CONFIG[plan]["name"]
    admin_note = "👑 _Режим администратора_\n" if is_admin(user_id) else ""

    await update.message.reply_text(
        "🤖 *Crypto AI Agents Bot*\n\n"
        f"{admin_note}"
        "Система из 3 AI-агентов на базе Claude:\n"
        "• 📈 *PriceAgent* — технический анализ\n"
        "• 🧠 *SentimentAgent* — настроения рынка\n"
        "• 🎯 *OrchestratorAgent* — итоговая рекомендация\n\n"
        "📡 Данные: CoinGecko API (реальные)\n"
        f"💳 Ваш тариф: *{plan_name}*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
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
    query = update.callback_query
    await query.answer()


# ─────────────────────────────────────────────
# Хэндлеры — подписка и оплата
# ─────────────────────────────────────────────

async def subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    info = get_subscription_info(user_id)
    plan = info["plan"]
    config = info["config"]
    sep = "─" * 28

    if plan == Plan.FREE:
        status = "🆓 Free — 1 анализ/день, без Trading сигналов"
    elif plan == Plan.PREMIUM:
        days = info["days_left"]
        status = f"⭐️ Premium — безлимит + Trading\nОсталось дней: {days}"
    else:
        days = info["days_left"]
        status = f"💎 VIP — всё включено + приоритет\nОсталось дней: {days}"

    await safe_edit_message(
        query,
        f"💳 *Подписка*\n{sep}\n\n"
        f"Текущий тариф: {status}\n\n"
        f"{sep}\n"
        f"🆓 *Free* — 1 анализ/день\n"
        f"⭐️ *Premium* — 199₽/мес\n"
        f"   • Безлимитные анализы\n"
        f"   • Trading / Фьючерсы\n"
        f"   • Все разделы\n\n"
        f"💎 *VIP* — 499₽/мес\n"
        f"   • Всё из Premium\n"
        f"   • Приоритетная обработка\n"
        f"   • Эксклюзивные сигналы\n\n"
        f"Оплата: Stars / ЮКасса / TON",
        reply_markup=subscription_keyboard(),
    )


async def buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    plan = query.data.split("_")[1]
    plan_name = "⭐️ Premium" if plan == "premium" else "💎 VIP"
    price = 199 if plan == "premium" else 499

    await safe_edit_message(
        query,
        f"💳 *Оплата {plan_name}*\n\n"
        f"Цена: *{price}₽/месяц*\n\n"
        f"Выберите способ оплаты:",
        reply_markup=payment_method_keyboard(plan),
    )


async def pay_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    plan = query.data.split("_")[2]
    stars = PREMIUM_STARS if plan == "premium" else VIP_STARS
    plan_name = "Premium" if plan == "premium" else "VIP"

    prices = [LabeledPrice(label=f"Crypto AI {plan_name}", amount=stars)]

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=f"⭐️ Crypto AI {plan_name}",
        description=f"Подписка {plan_name} на 30 дней — безлимитные анализы + Trading сигналы",
        payload=f"plan_{plan}",
        currency="XTR",
        prices=prices,
    )

    await safe_edit_message(
        query,
        f"⭐️ Счёт на оплату отправлен!\n{stars} Telegram Stars за {plan_name}.",
        reply_markup=back_keyboard("subscription"),
    )


async def pay_yukassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not YUKASSA_TOKEN:
        await safe_edit_message(
            query,
            "❌ ЮКасса не настроена. Используйте Telegram Stars или TON.",
            reply_markup=back_keyboard("subscription"),
        )
        return

    plan = query.data.split("_")[2]
    amount = PREMIUM_RUB if plan == "premium" else VIP_RUB
    plan_name = "Premium" if plan == "premium" else "VIP"

    prices = [LabeledPrice(label=f"Crypto AI {plan_name}", amount=amount)]

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=f"💳 Crypto AI {plan_name}",
        description=f"Подписка {plan_name} на 30 дней",
        payload=f"plan_{plan}",
        provider_token=YUKASSA_TOKEN,
        currency="RUB",
        prices=prices,
        start_parameter=f"crypto-ai-{plan}",
    )

    await safe_edit_message(
        query,
        f"💳 Счёт на оплату отправлен!\n{amount // 100}₽ через ЮКасса.",
        reply_markup=back_keyboard("subscription"),
    )


async def pay_ton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not TON_WALLET:
        await safe_edit_message(
            query,
            "❌ TON кошелёк не настроен. Используйте Telegram Stars или ЮКасса.",
            reply_markup=back_keyboard("subscription"),
        )
        return

    plan = query.data.split("_")[2]
    ton = PREMIUM_TON if plan == "premium" else VIP_TON
    plan_name = "Premium" if plan == "premium" else "VIP"

    await safe_edit_message(
        query,
        f"💎 *Оплата через TON*\n\n"
        f"Отправьте *{ton} TON* на кошелёк:\n"
        f"`{TON_WALLET}`\n\n"
        f"В комментарии укажите ваш Telegram ID:\n"
        f"`{query.from_user.id}`\n\n"
        f"После оплаты нажмите кнопку ниже или напишите /check_ton",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Я оплатил", callback_data=f"ton_check_{plan}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="subscription")],
        ]),
    )


async def ton_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    plan = query.data.split("_")[2]
    plan_enum = Plan.PREMIUM if plan == "premium" else Plan.VIP

    await safe_edit_message(
        query,
        "⏳ *Проверка оплаты TON*\n\n"
        "Ваша заявка отправлена администратору.\n"
        "После подтверждения оплаты подписка будет активирована в течение 1 часа.\n\n"
        "_Для ускорения напишите /support_",
        reply_markup=back_keyboard("subscription"),
    )

    for admin_id in ADMIN_IDS:
        try:
            plan_name = "Premium" if plan == "premium" else "VIP"
            ton = PREMIUM_TON if plan == "premium" else VIP_TON
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"💎 *TON оплата — требует проверки*\n\n"
                     f"Пользователь: {query.from_user.full_name} (@{query.from_user.username})\n"
                     f"ID: `{query.from_user.id}`\n"
                     f"Тариф: {plan_name}\n"
                     f"Сумма: {ton} TON\n\n"
                     f"Для активации: /activate {query.from_user.id} {plan}",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload

    if payload.startswith("plan_"):
        plan_str = payload.replace("plan_", "")
        plan = Plan.PREMIUM if plan_str == "premium" else Plan.VIP
        activate_subscription(user_id, plan)

        plan_name = PLAN_CONFIG[plan]["name"]
        await update.message.reply_text(
            f"✅ *Оплата прошла успешно!*\n\n"
            f"Тариф *{plan_name}* активирован на 30 дней.\n"
            f"Наслаждайтесь безлимитными анализами! 🚀",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        logger.info(f"Payment successful: user={user_id}, plan={plan}")


# ─────────────────────────────────────────────
# Хэндлеры — монеты и анализ
# ─────────────────────────────────────────────

async def choose_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await safe_edit_message(query, "⏳ Загружаю топ-50 монет...")

    coins = await get_top_coins_cached(50)
    if not coins:
        await safe_edit_message(query, "❌ Не удалось загрузить список монет.", reply_markup=back_keyboard())
        return

    context.bot_data["top_coins"] = coins

    await safe_edit_message(
        query,
        f"🪙 Топ-{len(coins)} монет по капитализации\nВыберите для анализа:",
        reply_markup=coins_keyboard(coins, page=0),
    )


async def coins_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    coin = query.data.split("_", 1)[1]

    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(
            query,
            f"⏱ Подождите {wait_sec} сек перед следующим запросом.",
            reply_markup=back_keyboard("choose_coin"),
        )
        return

    daily_ok, _ = check_daily_limit(user_id)
    if not daily_ok:
        plan = get_user_plan(user_id)
        await safe_edit_message(
            query,
            f"🚫 *Дневной лимит исчерпан*\n\n"
            f"Ваш тариф: {PLAN_CONFIG[plan]['name']} — 1 анализ/день.\n\n"
            f"Для безлимитного доступа оформите Premium или VIP подписку 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Оформить подписку", callback_data="subscription")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
            ]),
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
        coins = await get_top_coins_cached(50)
        coin_data = find_coin_in_list(coin, coins) if coins else None

        price_data, sentiment_data = await asyncio.gather(
            PriceAgent(client).analyze(coin, coin_data),
            SentimentAgent(client).analyze(coin),
        )

        final = await OrchestratorAgent(client).synthesize(coin, price_data, sentiment_data)
        increment_daily_usage(user_id)

        timestamp = datetime.now().strftime("%H:%M %d.%m.%Y")
        sep = "─" * 28
        price_str = f"${price_data['price']:,.4f}" if price_data.get('price') else "N/A"
        change_str = f"{price_data['change_24h']:+.2f}%" if price_data.get('change_24h') is not None else "N/A"
        data_source = "📡 Реальные данные (CoinGecko)" if price_data.get('is_real') else "⚠️ Демо-данные"

        plan = get_user_plan(user_id)
        if is_admin(user_id):
            limit_str = "👑 Admin — без лимитов"
        elif PLAN_CONFIG[plan]["analyses_per_day"] >= 999:
            limit_str = f"{PLAN_CONFIG[plan]['name']} — безлимит"
        else:
            _, new_used = user_daily_usage.get(user_id, (date.today(), 0))
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
            f"❌ Ошибка при анализе {coin}.\n\nОшибка: {type(e).__name__}",
            reply_markup=back_keyboard("choose_coin"),
        )


# ─────────────────────────────────────────────
# Хэндлеры — рынок
# ─────────────────────────────────────────────

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
            [InlineKeyboardButton("⬅️ Назад",   callback_data="back_main")],
        ])

        await safe_edit_message(
            query,
            f"🌐 *Обзор рынка*\n{sep}\n\n{overview}\n\n{sep}\n"
            f"📡 _Реальные данные (CoinGecko)_\n_Не является финансовым советом_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"market_overview error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при получении обзора.", reply_markup=back_keyboard())


async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(query, f"⏱ Подождите {wait_sec} сек.", reply_markup=back_keyboard())
        return

    await safe_edit_message(query, "⏳ Загружаю trending монеты...")

    try:
        trending_text = await SentimentAgent(client).trending_coins()
        sep = "─" * 28

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="trending")],
            [InlineKeyboardButton("⬅️ Назад",   callback_data="back_main")],
        ])

        await safe_edit_message(
            query,
            f"🔥 *Trending монеты*\n{sep}\n\n{trending_text}\n\n{sep}\n📡 _Данные: CoinGecko_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"trending error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при получении trending.", reply_markup=back_keyboard())


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
            [InlineKeyboardButton("⬅️ Назад",   callback_data="back_main")],
        ])

        await safe_edit_message(
            query,
            f"😱 *Fear & Greed*\n{sep}\n\n{fg_text}",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"fear_greed error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при получении индекса.", reply_markup=back_keyboard())


async def global_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(query, f"⏱ Подождите {wait_sec} сек.", reply_markup=back_keyboard())
        return

    await safe_edit_message(query, "⏳ Загружаю глобальные данные рынка...")

    try:
        global_text = await OrchestratorAgent(client).global_market_summary()
        sep = "─" * 28

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="global_market")],
            [InlineKeyboardButton("⬅️ Назад",   callback_data="back_main")],
        ])

        await safe_edit_message(
            query,
            f"📈 *Глобальный рынок*\n{sep}\n\n{global_text}\n\n{sep}\n"
            f"📡 _Данные: CoinGecko_\n_Не является финансовым советом_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"global_market error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при получении данных.", reply_markup=back_keyboard())


# ─────────────────────────────────────────────
# Хэндлеры — Trading (только Premium/VIP)
# ─────────────────────────────────────────────

async def trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not is_admin(user_id) and not can_use_trading(user_id):
        await safe_edit_message(
            query,
            "🔒 *Trading / Фьючерсы*\n\n"
            "Этот раздел доступен только для подписчиков *Premium* и *VIP*.\n\n"
            "Получите доступ к торговым сигналам с входом, стоп-лоссом и тейк-профитом!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Оформить подписку", callback_data="subscription")],
                [InlineKeyboardButton("⬅️ Назад",            callback_data="back_main")],
            ]),
        )
        return

    await safe_edit_message(
        query,
        "📉 *Trading / Фьючерсы*\n\n"
        "AI-агенты анализируют рынок фьючерсов и генерируют торговые сигналы.\n\n"
        "Выберите тип сигнала:",
        reply_markup=trading_keyboard(),
    )


async def trading_long(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_admin(user_id) and not can_use_trading(user_id):
        await safe_edit_message(query, "🔒 Доступно только для Premium/VIP.", reply_markup=back_keyboard("subscription"))
        return

    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(query, f"⏱ Подождите {wait_sec} сек.", reply_markup=back_keyboard("trading"))
        return

    await safe_edit_message(query, "⏳ Анализирую Long возможности...")

    try:
        coins = await get_top_coins_cached(50)
        signals = await OrchestratorAgent(client).futures_signals(coins, direction="long")
        sep = "─" * 28

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить",      callback_data="trading_long")],
            [InlineKeyboardButton("🔴 Short сигналы", callback_data="trading_short")],
            [InlineKeyboardButton("⬅️ Trading меню",  callback_data="trading")],
        ])

        await safe_edit_message(
            query,
            f"🟢 *Long сигналы (Фьючерсы)*\n{sep}\n\n{signals}\n\n{sep}\n"
            f"_Не является финансовым советом. Торгуйте осторожно!_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"trading_long error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при генерации сигналов.", reply_markup=back_keyboard("trading"))


async def trading_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_admin(user_id) and not can_use_trading(user_id):
        await safe_edit_message(query, "🔒 Доступно только для Premium/VIP.", reply_markup=back_keyboard("subscription"))
        return

    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(query, f"⏱ Подождите {wait_sec} сек.", reply_markup=back_keyboard("trading"))
        return

    await safe_edit_message(query, "⏳ Анализирую Short возможности...")

    try:
        coins = await get_top_coins_cached(50)
        signals = await OrchestratorAgent(client).futures_signals(coins, direction="short")
        sep = "─" * 28

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить",     callback_data="trading_short")],
            [InlineKeyboardButton("🟢 Long сигналы", callback_data="trading_long")],
            [InlineKeyboardButton("⬅️ Trading меню", callback_data="trading")],
        ])

        await safe_edit_message(
            query,
            f"🔴 *Short сигналы (Фьючерсы)*\n{sep}\n\n{signals}\n\n{sep}\n"
            f"_Не является финансовым советом. Торгуйте осторожно!_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"trading_short error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при генерации сигналов.", reply_markup=back_keyboard("trading"))


async def trading_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_admin(user_id) and not can_use_trading(user_id):
        await safe_edit_message(query, "🔒 Доступно только для Premium/VIP.", reply_markup=back_keyboard("subscription"))
        return

    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(query, f"⏱ Подождите {wait_sec} сек.", reply_markup=back_keyboard("trading"))
        return

    await safe_edit_message(query, "⏳ Загружаю топ фьючерсные пары...")

    try:
        coins = await get_top_coins_cached(50)
        top_futures = await OrchestratorAgent(client).top_futures(coins)
        sep = "─" * 28

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить",     callback_data="trading_top")],
            [InlineKeyboardButton("⬅️ Trading меню", callback_data="trading")],
        ])

        await safe_edit_message(
            query,
            f"📊 *Топ фьючерсные пары*\n{sep}\n\n{top_futures}\n\n{sep}\n"
            f"📡 _Данные: CoinGecko_\n_Не является финансовым советом_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"trading_top error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при загрузке данных.", reply_markup=back_keyboard("trading"))


# ─────────────────────────────────────────────
# Хэндлеры — аккаунт и инфо
# ─────────────────────────────────────────────

async def polymarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Топ события Polymarket по ликвидности + AI анализ."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    allowed, wait_sec = check_rate_limit(user_id)
    if not allowed:
        await safe_edit_message(query, f"⏱ Подождите {wait_sec} сек.", reply_markup=back_keyboard())
        return

    await safe_edit_message(query, "⏳ Загружаю топ события Polymarket...")

    try:
        result = await PolymarketAgent(client).get_top_events()
        sep = "─" * 28

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="polymarket")],
            [InlineKeyboardButton("⬅️ Назад",   callback_data="back_main")],
        ])

        await safe_edit_message(
            query,
            f"🎯 *Polymarket — Топ события*\n{sep}\n\n{result}\n\n"
            f"{sep}\n📡 _Данные: Polymarket Gamma API_\n_Не является финансовым советом_",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"polymarket error: {e}", exc_info=True)
        await safe_edit_message(query, "❌ Ошибка при загрузке данных Polymarket.", reply_markup=back_keyboard())


async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = query.from_user
    today = date.today()
    info = get_subscription_info(user_id)
    plan = info["plan"]
    config = info["config"]
    sep = "─" * 28

    if is_admin(user_id):
        plan_str = "👑 Администратор"
        analyses_info = "Без лимитов"
    elif plan == Plan.FREE:
        plan_str = "🆓 Free"
        if user_id in user_daily_usage:
            saved_date, count = user_daily_usage[user_id]
            used = count if saved_date == today else 0
        else:
            used = 0
        remaining = FREE_ANALYSES_PER_DAY - used
        analyses_info = f"{used}/{FREE_ANALYSES_PER_DAY} сегодня, осталось: {remaining}"
    else:
        plan_str = config["name"]
        days = info["days_left"]
        analyses_info = f"Безлимит (осталось дней: {days})"

    name = user.full_name or "Пользователь"
    username = f"@{user.username}" if user.username else "—"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Управление подпиской", callback_data="subscription")],
        [InlineKeyboardButton("⬅️ Назад",               callback_data="back_main")],
    ])

    await safe_edit_message(
        query,
        f"👤 *Мой аккаунт*\n{sep}\n\n"
        f"👤 Имя: *{name}*\n"
        f"🔗 Username: {username}\n"
        f"🆔 ID: `{user_id}`\n\n"
        f"📋 *Тариф:* {plan_str}\n"
        f"📊 *Анализы:* {analyses_info}\n"
        f"📉 *Trading:* {'✅ Доступен' if config['trading_signals'] or is_admin(user_id) else '🔒 Premium/VIP'}\n\n"
        f"{sep}\n_Для увеличения лимита оформите подписку_",
        reply_markup=keyboard,
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message(
        query,
        "ℹ️ *О системе агентов*\n\n"
        "📈 *PriceAgent*\n"
        "Реальные данные с CoinGecko: цена, объём, изменение 24ч, RSI(14), High/Low уровни.\n\n"
        "🧠 *SentimentAgent*\n"
        "Fear & Greed Index от Alternative.me (обновляется ежедневно).\n\n"
        "🎯 *OrchestratorAgent*\n"
        "Синтезирует данные обоих агентов, даёт рекомендацию с уровнем уверенности.\n\n"
        "📉 *Trading Agent* _(Premium/VIP)_\n"
        "Анализирует фьючерсный рынок, генерирует Long/Short сигналы с входом, стоп-лоссом и тейк-профитом.\n\n"
        "⚡️ Агенты работают *параллельно*.\n"
        "🤖 Все агенты на базе *Claude* от Anthropic.\n"
        "📡 Данные: CoinGecko API + Alternative.me",
        reply_markup=back_keyboard(),
    )


# ─────────────────────────────────────────────
# Admin команды
# ─────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔️ Нет доступа.")
        return

    today = date.today()
    active_today = sum(1 for uid, (d, c) in user_daily_usage.items() if d == today)
    total_analyses = sum(c for uid, (d, c) in user_daily_usage.items() if d == today)
    sub_stats = get_stats()

    await update.message.reply_text(
        f"📊 *Статистика*\n\n"
        f"Активных сегодня: {active_today}\n"
        f"Анализов сегодня: {total_analyses}\n\n"
        f"Подписки:\n"
        f"🆓 Free: {sub_stats[Plan.FREE]}\n"
        f"⭐️ Premium: {sub_stats[Plan.PREMIUM]}\n"
        f"💎 VIP: {sub_stats[Plan.VIP]}",
        parse_mode="Markdown",
    )


async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручная активация: /activate USER_ID plan"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔️ Нет доступа.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /activate USER_ID premium|vip")
        return

    try:
        target_id = int(args[0])
        plan_str = args[1].lower()
        plan = Plan.PREMIUM if plan_str == "premium" else Plan.VIP
        activate_subscription(target_id, plan)
        plan_name = PLAN_CONFIG[plan]["name"]
        await update.message.reply_text(f"✅ Подписка {plan_name} активирована для {target_id}")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"✅ *Ваша подписка активирована!*\n\nТариф: *{plan_name}*\nСрок: 30 дней\n\nНаслаждайтесь! 🚀",
                parse_mode="Markdown",
                reply_markup=main_keyboard(),
            )
        except Exception:
            pass
    except (ValueError, KeyError) as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)


# ─────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("activate", cmd_activate))

    app.add_handler(CallbackQueryHandler(choose_coin,     pattern=r"^choose_coin$"))
    app.add_handler(CallbackQueryHandler(coins_page,      pattern=r"^coins_page_\d+$"))
    app.add_handler(CallbackQueryHandler(analyze_coin,    pattern=r"^analyze_"))
    app.add_handler(CallbackQueryHandler(market_overview, pattern=r"^market_overview$"))
    app.add_handler(CallbackQueryHandler(trending,        pattern=r"^trending$"))
    app.add_handler(CallbackQueryHandler(fear_greed,      pattern=r"^fear_greed$"))
    app.add_handler(CallbackQueryHandler(global_market,   pattern=r"^global_market$"))
    app.add_handler(CallbackQueryHandler(trading,         pattern=r"^trading$"))
    app.add_handler(CallbackQueryHandler(trading_long,    pattern=r"^trading_long$"))
    app.add_handler(CallbackQueryHandler(trading_short,   pattern=r"^trading_short$"))
    app.add_handler(CallbackQueryHandler(trading_top,     pattern=r"^trading_top$"))
    app.add_handler(CallbackQueryHandler(polymarket,      pattern=r"^polymarket$"))
    app.add_handler(CallbackQueryHandler(my_account,      pattern=r"^my_account$"))
    app.add_handler(CallbackQueryHandler(about,           pattern=r"^about$"))
    app.add_handler(CallbackQueryHandler(subscription,    pattern=r"^subscription$"))
    app.add_handler(CallbackQueryHandler(buy_plan,        pattern=r"^buy_(premium|vip)$"))
    app.add_handler(CallbackQueryHandler(pay_stars,       pattern=r"^pay_stars_(premium|vip)$"))
    app.add_handler(CallbackQueryHandler(pay_yukassa,     pattern=r"^pay_yukassa_(premium|vip)$"))
    app.add_handler(CallbackQueryHandler(pay_ton,         pattern=r"^pay_ton_(premium|vip)$"))
    app.add_handler(CallbackQueryHandler(ton_check,       pattern=r"^ton_check_(premium|vip)$"))
    app.add_handler(CallbackQueryHandler(back_main,       pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(noop,            pattern=r"^noop$"))

    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    app.add_error_handler(error_handler)

    logger.info("🤖 Crypto AI Agents Bot v3 запущен")
    logger.info(f"   Stars: Premium={PREMIUM_STARS} / VIP={VIP_STARS}")
    logger.info(f"   ЮКасса: {'настроена' if YUKASSA_TOKEN else 'не настроена'}")
    logger.info(f"   TON: {'настроен' if TON_WALLET else 'не настроен'}")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
