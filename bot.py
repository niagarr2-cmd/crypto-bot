import os
import logging
from datetime import datetime
from anthropic import Anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from agents import PriceAgent, SentimentAgent, OrchestratorAgent

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY")
client = Anthropic(api_key=ANTHROPIC_API_KEY)
COINS = ["BTC", "ETH", "BNB", "SOL", "XRP"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 Анализ монеты", callback_data="choose_coin")],
        [InlineKeyboardButton("🌐 Рынок целиком", callback_data="market_overview")],
        [InlineKeyboardButton("😱 Fear & Greed", callback_data="fear_greed")],
        [InlineKeyboardButton("ℹ️ Об агентах", callback_data="about")],
    ]
    await update.message.reply_text(
        "🤖 *Crypto AI Agents Bot*\n\nСистема из 3 агентов на базе Claude:\n"
        "• 📈 *PriceAgent* — цены и тренды\n"
        "• 🧠 *SentimentAgent* — настроения рынка\n"
        "• 🎯 *OrchestratorAgent* — итоговая рекомендация\n\nВыберите действие:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def choose_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton(f"🪙 {c}", callback_data=f"analyze_{c}") for c in COINS[:3]],
        [InlineKeyboardButton(f"🪙 {c}", callback_data=f"analyze_{c}") for c in COINS[3:]],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ]
    await query.edit_message_text("Выберите монету:", reply_markup=InlineKeyboardMarkup(keyboard))

async def analyze_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coin = query.data.split("_")[1]
    await query.edit_message_text(f"⏳ Агенты анализируют *{coin}*...", parse_mode="Markdown")
    price_data = await PriceAgent(client).analyze(coin)
    sentiment_data = await SentimentAgent(client).analyze(coin)
    final = await OrchestratorAgent(client).synthesize(coin, price_data, sentiment_data)
    text = (f"📊 *Анализ {coin}* — {datetime.now().strftime('%H:%M %d.%m.%Y')}\n{'─'*35}\n\n"
            f"📈 *PriceAgent*\n{price_data['summary']}\n\n"
            f"🧠 *SentimentAgent*\n{sentiment_data['summary']}\n\n"
            f"🎯 *OrchestratorAgent*\n{final['recommendation']}\n\n"
            f"⚠️ _Не является финансовым советом_")
    keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data=f"analyze_{coin}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="choose_coin")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def market_overview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Сканирую рынок...", parse_mode="Markdown")
    overview = await OrchestratorAgent(client).market_overview(COINS)
    keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="market_overview")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]]
    await query.edit_message_text(f"🌐 *Обзор рынка*\n{'─'*35}\n\n{overview}\n\n⚠️ _Не является финансовым советом_",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def fear_greed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Анализирую...", parse_mode="Markdown")
    fg = await SentimentAgent(client).fear_greed_analysis()
    keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="fear_greed")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]]
    await query.edit_message_text(f"😱 *Fear & Greed*\n{'─'*35}\n\n{fg}\n\n⚠️ _Не является финансовым советом_",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]]
    await query.edit_message_text(
        "ℹ️ *Об агентах*\n\n📈 *PriceAgent*\nАнализирует цену, RSI, MA50/MA200.\n\n"
        "🧠 *SentimentAgent*\nАнализирует Fear & Greed и новости.\n\n"
        "🎯 *OrchestratorAgent*\nДаёт итоговую рекомендацию.\n\nРаботают на базе *Claude* от Anthropic.",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📊 Анализ монеты", callback_data="choose_coin")],
        [InlineKeyboardButton("🌐 Рынок целиком", callback_data="market_overview")],
        [InlineKeyboardButton("😱 Fear & Greed", callback_data="fear_greed")],
        [InlineKeyboardButton("ℹ️ Об агентах", callback_data="about")],
    ]
    await query.edit_message_text("🤖 *Crypto AI Agents Bot*\n\nВыберите действие:",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(choose_coin, pattern="^choose_coin$"))
    app.add_handler(CallbackQueryHandler(analyze_coin, pattern="^analyze_"))
    app.add_handler(CallbackQueryHandler(market_overview, pattern="^market_overview$"))
    app.add_handler(CallbackQueryHandler(fear_greed, pattern="^fear_greed$"))
    app.add_handler(CallbackQueryHandler(about, pattern="^about$"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="^back_main$"))
    logger.info("🤖 Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
