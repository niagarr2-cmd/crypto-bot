“””
Crypto AI Agents — Telegram бот.

Исправления относительно исходного ТЗ:

1. AsyncAnthropic — реальный параллелизм через asyncio.gather()
1. Rate limiting — защита от спама (1 запрос / 30 сек на пользователя)
1. Обработка всех ошибок — бот никогда не падает для пользователя
1. Корректный Markdown — экранирование ответов Claude
1. Graceful shutdown — корректное завершение
1. Пометка демо-данных — честность перед пользователем
   “””

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

from agents import PriceAgent, SentimentAgent, OrchestratorAgent

# ─────────────────────────────────────────────

# Конфигурация

# ─────────────────────────────────────────────

logging.basicConfig(
format=”%(asctime)s | %(levelname)s | %(name)s | %(message)s”,
level=logging.INFO,
)
logger = logging.getLogger(**name**)

TELEGRAM_TOKEN = os.environ[“TELEGRAM_TOKEN”]
ANTHROPIC_API_KEY = os.environ[“ANTHROPIC_API_KEY”]

# Async-клиент — критически важно для параллелизма!

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

COINS = [“BTC”, “ETH”, “BNB”, “SOL”, “XRP”]

# Rate limiting: минимум секунд между запросами одного юзера

RATE_LIMIT_SECONDS = int(os.getenv(“RATE_LIMIT_SECONDS”, “30”))
user_last_request: dict[int, float] = defaultdict(float)

# ─────────────────────────────────────────────

# Утилиты

# ─────────────────────────────────────────────

def check_rate_limit(user_id: int) -> tuple[bool, int]:
“””
Проверка rate limit.
Возвращает (разрешено, секунд_до_следующего_запроса).
“””
now = time.time()
last = user_last_request[user_id]
elapsed = now - last

```
if elapsed < RATE_LIMIT_SECONDS:
    remaining = int(RATE_LIMIT_SECONDS - elapsed)
    return False, remaining

user_last_request[user_id] = now
return True, 0
```

def main_keyboard() -> InlineKeyboardMarkup:
return InlineKeyboardMarkup([
[InlineKeyboardButton(“📊 Анализ монеты”, callback_data=“choose_coin”)],
[InlineKeyboardButton(“🌐 Обзор рынка”, callback_data=“market_overview”)],
[InlineKeyboardButton(“😱 Fear & Greed”, callback_data=“fear_greed”)],
[InlineKeyboardButton(“ℹ️ Об агентах”, callback_data=“about”)],
])

def back_keyboard(callback: str = “back_main”) -> InlineKeyboardMarkup:
return InlineKeyboardMarkup([
[InlineKeyboardButton(“⬅️ Назад”, callback_data=callback)],
])

async def safe_edit_message(
query, text: str, parse_mode: str = “Markdown”, reply_markup=None
):
“””
Безопасная отправка сообщения.
Если Markdown ломается — отправляем без форматирования.
“””
try:
await query.edit_message_text(
text,
parse_mode=parse_mode,
reply_markup=reply_markup,
)
except Exception as e:
logger.warning(f”Markdown parse error, fallback to plain text: {e}”)
# Убираем все Markdown-символы и отправляем plain text
clean_text = text.replace(”*”, “”).replace(”_”, “”).replace(”`”, “”)
try:
await query.edit_message_text(
clean_text,
reply_markup=reply_markup,
)
except Exception as e2:
logger.error(f”Failed to send even plain text: {e2}”)

# ─────────────────────────────────────────────

# Хэндлеры

# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Команда /start — приветствие и главное меню.”””
await update.message.reply_text(
“🤖 *Crypto AI Agents Bot*\n\n”
“Система из 3 AI-агентов на базе Claude:\n”
“• 📈 *PriceAgent* — технический анализ\n”
“• 🧠 *SentimentAgent* — настроения рынка\n”
“• 🎯 *OrchestratorAgent* — итоговая рекомендация\n\n”
“⚡ Агенты работают параллельно (30-60 сек)\n\n”
“⚠️ *Сейчас работает на демо-данных.*\n”
“*Реальные данные бирж — в следующем обновлении.*\n\n”
“Выберите действие:”,
parse_mode=“Markdown”,
reply_markup=main_keyboard(),
)

async def choose_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Меню выбора монеты.”””
query = update.callback_query
await query.answer()

```
keyboard = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(f"🪙 {c}", callback_data=f"analyze_{c}")
        for c in COINS[:3]
    ],
    [
        InlineKeyboardButton(f"🪙 {c}", callback_data=f"analyze_{c}")
        for c in COINS[3:]
    ],
    [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
])
await safe_edit_message(
    query,
    "🪙 Выберите монету для анализа:",
    reply_markup=keyboard,
)
```

async def analyze_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Полный анализ монеты тремя агентами.”””
query = update.callback_query
await query.answer()

```
user_id = query.from_user.id
coin = query.data.split("_")[1]

# Rate limiting
allowed, wait_seconds = check_rate_limit(user_id)
if not allowed:
    await safe_edit_message(
        query,
        f"⏱ Подождите {wait_seconds} сек перед следующим запросом.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data="choose_coin")],
        ]),
    )
    return

await safe_edit_message(
    query,
    f"⏳ Агенты анализируют *{coin}*...\n\n"
    f"🔄 PriceAgent + SentimentAgent работают параллельно\n"
    f"Обычно занимает 30-60 секунд.",
)

try:
    # ПАРАЛЛЕЛЬНЫЙ запрос — теперь РЕАЛЬНО параллельный!
    price_data, sentiment_data = await asyncio.gather(
        PriceAgent(client).analyze(coin),
        SentimentAgent(client).analyze(coin),
    )

    # Оркестратор синтезирует результаты
    final = await OrchestratorAgent(client).synthesize(
        coin, price_data, sentiment_data
    )

    timestamp = datetime.now().strftime("%H:%M %d.%m.%Y")
    separator = "─" * 30

    text = (
        f"📊 *Анализ {coin}* — {timestamp}\n"
        f"{separator}\n\n"
        f"📈 *PriceAgent:*\n{price_data['summary']}\n\n"
        f"🧠 *SentimentAgent:*\n{sentiment_data['summary']}\n\n"
        f"🎯 *OrchestratorAgent:*\n{final['recommendation']}\n\n"
        f"{separator}\n"
        f"⚠️ _Демо-данные. Не является финансовым советом._"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"analyze_{coin}")],
        [InlineKeyboardButton("🪙 Другая монета", callback_data="choose_coin")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="back_main")],
    ])

    await safe_edit_message(query, text, reply_markup=keyboard)

except Exception as e:
    logger.error(f"analyze_coin error for {coin}: {e}", exc_info=True)
    await safe_edit_message(
        query,
        f"❌ Ошибка при анализе {coin}.\n"
        f"Попробуйте позже или выберите другую монету.\n\n"
        f"Ошибка: {type(e).__name__}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Повторить", callback_data=f"analyze_{coin}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="choose_coin")],
        ]),
    )
```

async def market_overview(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Обзор всего рынка.”””
query = update.callback_query
await query.answer()

```
user_id = query.from_user.id
allowed, wait_seconds = check_rate_limit(user_id)
if not allowed:
    await safe_edit_message(
        query,
        f"⏱ Подождите {wait_seconds} сек.",
        reply_markup=back_keyboard(),
    )
    return

await safe_edit_message(query, "⏳ Сканирую рынок...")

try:
    overview = await OrchestratorAgent(client).market_overview(COINS)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="market_overview")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])

    separator = "─" * 30
    await safe_edit_message(
        query,
        f"🌐 *Обзор рынка*\n{separator}\n\n"
        f"{overview}\n\n"
        f"{separator}\n"
        f"⚠️ _Демо-данные. Не является финансовым советом._",
        reply_markup=keyboard,
    )
except Exception as e:
    logger.error(f"market_overview error: {e}", exc_info=True)
    await safe_edit_message(
        query,
        "❌ Ошибка при получении обзора рынка.",
        reply_markup=back_keyboard(),
    )
```

async def fear_greed(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Индекс Fear & Greed.”””
query = update.callback_query
await query.answer()

```
user_id = query.from_user.id
allowed, wait_seconds = check_rate_limit(user_id)
if not allowed:
    await safe_edit_message(
        query,
        f"⏱ Подождите {wait_seconds} сек.",
        reply_markup=back_keyboard(),
    )
    return

await safe_edit_message(query, "⏳ Анализирую настроения рынка...")

try:
    fg_text = await SentimentAgent(client).fear_greed_only()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="fear_greed")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])

    separator = "─" * 30
    await safe_edit_message(
        query,
        f"😱 *Fear & Greed*\n{separator}\n\n{fg_text}",
        reply_markup=keyboard,
    )
except Exception as e:
    logger.error(f"fear_greed error: {e}", exc_info=True)
    await safe_edit_message(
        query,
        "❌ Ошибка при получении индекса.",
        reply_markup=back_keyboard(),
    )
```

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Информация о системе агентов.”””
query = update.callback_query
await query.answer()

```
await safe_edit_message(
    query,
    "ℹ️ *О системе агентов*\n\n"
    "📈 *PriceAgent*\n"
    "Анализирует цену, RSI(14), скользящие средние MA50/MA200, "
    "объём торгов. Определяет тренд и даёт технический анализ.\n\n"
    "🧠 *SentimentAgent*\n"
    "Анализирует Fear & Greed Index, соотношение позитивных/негативных "
    "новостей, активность в соцсетях, тренд-скор.\n\n"
    "🎯 *OrchestratorAgent*\n"
    "Синтезирует данные обоих агентов и формирует итоговую "
    "рекомендацию с уровнем уверенности.\n\n"
    "⚡️ PriceAgent и SentimentAgent работают *параллельно* "
    "через asyncio для максимальной скорости.\n\n"
    "🤖 Все агенты работают на базе *Claude* от Anthropic.\n\n"
    "⚠️ _Текущая версия использует демо-данные. "
    "Реальные данные бирж — скоро._",
    reply_markup=back_keyboard(),
)
```

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Возврат в главное меню.”””
query = update.callback_query
await query.answer()

```
await safe_edit_message(
    query,
    "🤖 *Crypto AI Agents Bot*\n\nВыберите действие:",
    reply_markup=main_keyboard(),
)
```

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
“”“Глобальный обработчик ошибок — бот не падает.”””
logger.error(f”Unhandled exception: {context.error}”, exc_info=context.error)

# ─────────────────────────────────────────────

# Запуск

# ─────────────────────────────────────────────

def main():
app = Application.builder().token(TELEGRAM_TOKEN).build()

```
# Хэндлеры
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(choose_coin, pattern=r"^choose_coin$"))
app.add_handler(CallbackQueryHandler(analyze_coin, pattern=r"^analyze_"))
app.add_handler(CallbackQueryHandler(market_overview, pattern=r"^market_overview$"))
app.add_handler(CallbackQueryHandler(fear_greed, pattern=r"^fear_greed$"))
app.add_handler(CallbackQueryHandler(about, pattern=r"^about$"))
app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))

# Глобальный обработчик ошибок
app.add_error_handler(error_handler)

logger.info("🤖 Crypto AI Agents Bot запущен")
logger.info(f"   Монеты: {', '.join(COINS)}")
logger.info(f"   Rate limit: {RATE_LIMIT_SECONDS} сек")

app.run_polling(allowed_updates=Update.ALL_TYPES)
```

if **name** == “**main**”:
main()
