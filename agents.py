"""
Crypto AI Agents — Три агента для анализа крипторынка.

Исправления относительно исходного ТЗ:
1. AsyncAnthropic вместо синхронного Anthropic (реальный параллелизм)
2. Обработка ошибок Claude API
3. Экранирование Markdown-символов для Telegram
4. Пометка "демо-данные" в выводе (пока нет реального API)
5. Rate limiting готовность
"""

import asyncio
import random
import re
import os
import logging
from datetime import datetime
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

# Конфигурация через env переменные
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "500"))

# Таймаут для Claude API запросов (секунды)
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "45"))


def escape_markdown(text: str) -> str:
    """
    Экранирование спецсимволов Markdown V1 для Telegram.
    Claude может вернуть любые символы — без этого бот падает.
    """
    # Сохраняем наши собственные * и _ (для форматирования),
    # но экранируем те, что пришли от Claude
    # Простой подход: убираем markdown из ответов Claude
    escape_chars = r"[\`\[\]()~>#\+\-=|{}.!]"
    return re.sub(escape_chars, lambda m: "\\" + m.group(), text)


def escape_claude_response(text: str) -> str:
    """
    Безопасная обработка ответа Claude для Telegram Markdown.
    Убираем все Markdown-конструкции, которые Claude мог добавить.
    """
    # Убираем ** bold ** → просто текст
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    # Убираем * italic * → просто текст
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    # Убираем ``` code blocks ```
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Убираем `inline code`
    text = re.sub(r"`(.*?)`", r"\1", text)
    # Убираем [links](url)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    # Экранируем оставшиеся опасные символы для Markdown V1
    # В Markdown V1 опасны: _ * [ ] ( ) ~ ` > # + - = | { } . !
    # Но мы используем Markdown (не V2), так что опасны только _ * ` [
    text = text.replace("_", "\\_")
    return text


# ─────────────────────────────────────────────
# Генераторы демо-данных (заглушки)
# TODO: Заменить на CoinGecko API / Binance API
# ─────────────────────────────────────────────

def _fake_price_data(coin: str) -> dict:
    """Генерация демо-данных цены. ЗАГЛУШКА — не реальные данные!"""
    base_prices = {
        "BTC": 65000, "ETH": 3200, "BNB": 580,
        "SOL": 170, "XRP": 0.62,
    }
    price = base_prices.get(coin, 100) * random.uniform(0.95, 1.05)
    change_24h = random.uniform(-8, 8)
    volume = random.uniform(1e9, 5e10)
    rsi = random.uniform(25, 80)
    ma_50 = price * random.uniform(0.92, 1.05)
    ma_200 = price * random.uniform(0.85, 1.10)

    return {
        "coin": coin,
        "price": round(price, 4),
        "change_24h": round(change_24h, 2),
        "volume_24h": round(volume / 1e9, 2),
        "rsi": round(rsi, 1),
        "ma_50": round(ma_50, 2),
        "ma_200": round(ma_200, 2),
        "timestamp": datetime.now().isoformat(),
        "is_demo": True,  # Флаг: данные не настоящие
    }


def _fake_sentiment_data(coin: str) -> dict:
    """Генерация демо-данных настроений. ЗАГЛУШКА — не реальные данные!"""
    labels = ["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
    fg_value = random.randint(10, 90)
    fg_label = labels[min(fg_value // 20, 4)]
    positive_pct = random.randint(30, 75)

    return {
        "coin": coin,
        "fear_greed_index": fg_value,
        "fear_greed_label": fg_label,
        "news_positive_pct": positive_pct,
        "news_negative_pct": 100 - positive_pct,
        "social_mentions_24h": random.randint(5000, 200000),
        "trending_score": round(random.uniform(1, 10), 1),
        "is_demo": True,
    }


# ─────────────────────────────────────────────
# Агенты
# ─────────────────────────────────────────────

class PriceAgent:
    """Агент технического анализа цены."""

    SYSTEM_PROMPT = (
        "Ты профессиональный технический аналитик криптовалютного рынка. "
        "Дай КРАТКИЙ анализ (3-4 предложения) на русском языке. "
        "Укажи: тренд, RSI сигнал, ключевые уровни, вывод. "
        "НЕ используй Markdown-форматирование (звёздочки, подчёркивания и т.д.). "
        "Пиши простым текстом."
    )

    def __init__(self, client: AsyncAnthropic):
        self.client = client

    async def analyze(self, coin: str) -> dict:
        data = _fake_price_data(coin)

        trend = "бычий" if data["change_24h"] > 0 else "медвежий"
        if abs(data["change_24h"]) < 1.0:
            trend = "боковой"

        rsi_signal = (
            "перекуплен" if data["rsi"] > 70
            else "перепродан" if data["rsi"] < 30
            else "нейтрален"
        )

        user_msg = (
            f"Монета: {coin}\n"
            f"Цена: ${data['price']:,.4f}\n"
            f"Изменение 24ч: {data['change_24h']:+.2f}%\n"
            f"Объём: ${data['volume_24h']}B\n"
            f"RSI(14): {data['rsi']} ({rsi_signal})\n"
            f"MA50: ${data['ma_50']:,.2f}\n"
            f"MA200: ${data['ma_200']:,.2f}\n"
            f"Тренд: {trend}"
        )

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=API_TIMEOUT,
            )
            summary = escape_claude_response(response.content[0].text)
        except asyncio.TimeoutError:
            logger.error(f"PriceAgent timeout for {coin}")
            summary = f"Тайм-аут анализа {coin}. Попробуйте позже."
        except Exception as e:
            logger.error(f"PriceAgent error for {coin}: {e}")
            summary = f"Ошибка анализа {coin}: {type(e).__name__}"

        return {
            "agent": "PriceAgent",
            "coin": coin,
            "raw_data": data,
            "summary": summary,
            "trend": trend,
            "rsi": data["rsi"],
            "rsi_signal": rsi_signal,
        }


class SentimentAgent:
    """Агент анализа настроений рынка."""

    SYSTEM_PROMPT = (
        "Ты эксперт по настроениям криптовалютного рынка. "
        "Дай КРАТКИЙ анализ (3-4 предложения) на русском языке. "
        "НЕ используй Markdown-форматирование. Пиши простым текстом."
    )

    def __init__(self, client: AsyncAnthropic):
        self.client = client

    async def analyze(self, coin: str) -> dict:
        data = _fake_sentiment_data(coin)

        user_msg = (
            f"Монета: {coin}\n"
            f"Fear & Greed Index: {data['fear_greed_index']}/100 "
            f"({data['fear_greed_label']})\n"
            f"Позитивные новости: {data['news_positive_pct']}%\n"
            f"Негативные новости: {data['news_negative_pct']}%\n"
            f"Упоминания в соцсетях за 24ч: {data['social_mentions_24h']:,}\n"
            f"Тренд-скор: {data['trending_score']}/10"
        )

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=API_TIMEOUT,
            )
            summary = escape_claude_response(response.content[0].text)
        except asyncio.TimeoutError:
            logger.error(f"SentimentAgent timeout for {coin}")
            summary = f"Тайм-аут анализа настроений {coin}."
        except Exception as e:
            logger.error(f"SentimentAgent error for {coin}: {e}")
            summary = f"Ошибка анализа настроений: {type(e).__name__}"

        return {
            "agent": "SentimentAgent",
            "coin": coin,
            "raw_data": data,
            "summary": summary,
            "fear_greed": data["fear_greed_index"],
            "fear_greed_label": data["fear_greed_label"],
        }

    async def fear_greed_only(self) -> str:
        """Отдельный запрос только для Fear & Greed индекса."""
        fg = random.randint(10, 90)

        thresholds = [
            (25, "Extreme Fear 😱"),
            (45, "Fear 😨"),
            (55, "Neutral 😐"),
            (75, "Greed 🤑"),
            (101, "Extreme Greed 🚀"),
        ]
        label = next(lbl for thresh, lbl in thresholds if fg < thresh)

        filled = fg // 5
        bar = "█" * filled + "░" * (20 - filled)

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=(
                        "Эксперт по крипторынку. Отвечай по-русски, "
                        "кратко (3 предложения). Без Markdown."
                    ),
                    messages=[{
                        "role": "user",
                        "content": f"Fear & Greed Index: {fg}/100 ({label}). "
                                   f"Объясни что это значит для рынка.",
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            explanation = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Fear&Greed error: {e}")
            explanation = "Не удалось получить анализ. Попробуйте позже."

        return (
            f"📊 *Fear & Greed Index*\n"
            f"`[{bar}]`\n"
            f"*{fg}/100 — {label}*\n\n"
            f"{explanation}\n\n"
            f"⚠️ _Демо-данные (случайные)_"
        )


class OrchestratorAgent:
    """Главный агент — синтезирует данные и даёт рекомендацию."""

    SYSTEM_PROMPT = (
        "Ты главный аналитик криптовалютного фонда. "
        "Синтезируй данные двух аналитиков и дай итоговую рекомендацию.\n"
        "Формат ответа СТРОГО:\n"
        "Сигнал: 🟢 ПОКУПАТЬ / 🟡 ДЕРЖАТЬ / 🔴 ПРОДАВАТЬ\n"
        "Уверенность: X%\n"
        "Ключевые факторы: (2-3 пункта через запятую)\n"
        "Риски: (1-2 пункта через запятую)\n\n"
        "НЕ используй Markdown-форматирование. Пиши простым текстом."
    )

    def __init__(self, client: AsyncAnthropic):
        self.client = client

    async def synthesize(
        self, coin: str, price_data: dict, sentiment_data: dict
    ) -> dict:
        user_msg = (
            f"Монета: {coin}\n\n"
            f"=== ТЕХНИЧЕСКИЙ АНАЛИЗ (PriceAgent) ===\n"
            f"{price_data['summary']}\n"
            f"RSI: {price_data['rsi']} ({price_data['rsi_signal']})\n"
            f"Тренд: {price_data['trend']}\n\n"
            f"=== АНАЛИЗ НАСТРОЕНИЙ (SentimentAgent) ===\n"
            f"{sentiment_data['summary']}\n"
            f"Fear & Greed: {sentiment_data['fear_greed']}/100 "
            f"({sentiment_data['fear_greed_label']})"
        )

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=API_TIMEOUT,
            )
            recommendation = escape_claude_response(response.content[0].text)
        except asyncio.TimeoutError:
            logger.error(f"OrchestratorAgent timeout for {coin}")
            recommendation = "🟡 ДЕРЖАТЬ\nНе удалось завершить анализ вовремя."
        except Exception as e:
            logger.error(f"OrchestratorAgent error for {coin}: {e}")
            recommendation = f"Ошибка синтеза: {type(e).__name__}"

        return {
            "agent": "OrchestratorAgent",
            "coin": coin,
            "recommendation": recommendation,
        }

    async def market_overview(self, coins: list) -> str:
        """Обзор всех монет разом."""
        items = []
        for coin in coins:
            data = _fake_price_data(coin)
            emoji = "🟢" if data["change_24h"] > 0 else "🔴"
            items.append(
                f"{emoji} *{coin}*: ${data['price']:,.2f} "
                f"({data['change_24h']:+.2f}%)"
            )

        fg = random.randint(15, 85)
        items_text = "\n".join(items)

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=(
                        "Эксперт по крипторынку. Краткий обзор "
                        "(3-4 предложения) по-русски. Без Markdown."
                    ),
                    messages=[{
                        "role": "user",
                        "content": f"Рынок сегодня:\n{items_text}\n"
                                   f"Fear & Greed: {fg}/100",
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            analysis = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Market overview error: {e}")
            analysis = "Не удалось получить анализ рынка."

        return (
            f"{items_text}\n\n"
            f"📋 *Анализ:*\n{analysis}"
        )
