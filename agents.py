"""
Crypto AI Agents — Три агента для анализа крипторынка.

v2: Реальные данные через CoinGecko API + Alternative.me Fear&Greed
"""

import asyncio
import re
import os
import logging
import aiohttp
from datetime import datetime
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "500"))
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "45"))

# CoinGecko
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

# Alternative.me Fear & Greed
FEAR_GREED_URL = "https://api.alternative.me/fng/"


def escape_claude_response(text: str) -> str:
    """Убираем Markdown из ответов Claude для Telegram."""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = text.replace("_", "\\_")
    return text.strip()


# ─────────────────────────────────────────────
# CoinGecko API
# ─────────────────────────────────────────────

async def fetch_top_coins(limit: int = 50) -> list[dict]:
    """
    Получить топ-N монет по капитализации с CoinGecko.
    Возвращает список: [{id, symbol, name, current_price, ...}]
    """
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": limit,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{COINGECKO_BASE}/coins/markets",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"CoinGecko: loaded {len(data)} coins")
                    return data
                else:
                    logger.warning(f"CoinGecko status {resp.status}")
                    return []
    except Exception as e:
        logger.error(f"CoinGecko fetch_top_coins error: {e}")
        return []


async def fetch_coin_detail(coin_id: str) -> dict | None:
    """
    Детальные данные по монете: RSI, MA — из /coins/{id}.
    CoinGecko Free не даёт RSI напрямую, считаем из OHLC.
    """
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    try:
        async with aiohttp.ClientSession() as session:
            # OHLC за последние 14 дней для RSI
            async with session.get(
                f"{COINGECKO_BASE}/coins/{coin_id}/ohlc",
                params={"vs_currency": "usd", "days": "14"},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                ohlc = await resp.json()
                return {"ohlc": ohlc}
    except Exception as e:
        logger.error(f"CoinGecko fetch_coin_detail error {coin_id}: {e}")
        return None


def calculate_rsi(ohlc_data: list, period: int = 14) -> float | None:
    """Расчёт RSI из OHLC данных CoinGecko."""
    if not ohlc_data or len(ohlc_data) < period + 1:
        return None

    closes = [candle[4] for candle in ohlc_data]  # close price

    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    if not gains:
        return None

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


async def fetch_fear_greed() -> dict:
    """Fear & Greed Index от Alternative.me."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                FEAR_GREED_URL,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    item = data["data"][0]
                    return {
                        "value": int(item["value"]),
                        "label": item["value_classification"],
                        "is_real": True,
                    }
    except Exception as e:
        logger.error(f"Fear&Greed fetch error: {e}")

    # Fallback
    import random
    v = random.randint(30, 70)
    return {"value": v, "label": "Neutral", "is_real": False}


# Кэш топ-монет (обновляем раз в 10 минут)
_coins_cache: list[dict] = []
_coins_cache_time: float = 0
COINS_CACHE_TTL = 600  # секунд


async def get_top_coins_cached(limit: int = 50) -> list[dict]:
    """Топ монеты с кэшированием."""
    import time
    global _coins_cache, _coins_cache_time

    if _coins_cache and (time.time() - _coins_cache_time) < COINS_CACHE_TTL:
        return _coins_cache[:limit]

    coins = await fetch_top_coins(limit)
    if coins:
        _coins_cache = coins
        _coins_cache_time = time.time()

    return _coins_cache[:limit] if _coins_cache else []


def find_coin_in_list(symbol: str, coins: list[dict]) -> dict | None:
    """Найти монету по символу (BTC, ETH, ...)."""
    symbol_upper = symbol.upper()
    for coin in coins:
        if coin.get("symbol", "").upper() == symbol_upper:
            return coin
    return None


# ─────────────────────────────────────────────
# Агенты
# ─────────────────────────────────────────────

class PriceAgent:
    """Агент технического анализа цены."""

    SYSTEM_PROMPT = (
        "Ты профессиональный технический аналитик криптовалютного рынка. "
        "Дай КРАТКИЙ анализ (3-4 предложения) на русском языке. "
        "Укажи: тренд, RSI сигнал, ключевые уровни, вывод. "
        "НЕ используй Markdown-форматирование. Пиши простым текстом."
    )

    def __init__(self, client: AsyncAnthropic):
        self.client = client

    async def analyze(self, coin: str, coin_data: dict | None = None) -> dict:
        is_real = False

        if coin_data:
            price = coin_data.get("current_price", 0)
            change_24h = coin_data.get("price_change_percentage_24h") or 0
            volume = (coin_data.get("total_volume") or 0) / 1e9
            market_cap = coin_data.get("market_cap") or 0
            high_24h = coin_data.get("high_24h") or price
            low_24h = coin_data.get("low_24h") or price
            coin_id = coin_data.get("id", coin.lower())
            is_real = True

            # Попробуем получить RSI
            detail = await fetch_coin_detail(coin_id)
            rsi = None
            if detail and detail.get("ohlc"):
                rsi = calculate_rsi(detail["ohlc"])
            if rsi is None:
                rsi = 50.0  # нейтральное значение если не удалось

        else:
            # Fallback: демо
            import random
            price = 100 * random.uniform(0.95, 1.05)
            change_24h = random.uniform(-5, 5)
            volume = random.uniform(0.1, 5)
            high_24h = price * 1.03
            low_24h = price * 0.97
            rsi = random.uniform(35, 65)
            is_real = False

        trend = "бычий" if change_24h > 0 else ("медвежий" if change_24h < -1 else "боковой")
        rsi_signal = (
            "перекуплен" if rsi > 70 else
            "перепродан" if rsi < 30 else
            "нейтрален"
        )

        user_msg = (
            f"Монета: {coin}\n"
            f"Цена: ${price:,.4f}\n"
            f"Изменение 24ч: {change_24h:+.2f}%\n"
            f"Объём 24ч: ${volume:.2f}B\n"
            f"Макс 24ч: ${high_24h:,.4f} / Мин 24ч: ${low_24h:,.4f}\n"
            f"RSI(14): {rsi} ({rsi_signal})\n"
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
            summary = f"Тайм-аут анализа {coin}. Попробуйте позже."
        except Exception as e:
            logger.error(f"PriceAgent error for {coin}: {e}")
            summary = f"Ошибка анализа {coin}: {type(e).__name__}"

        return {
            "agent": "PriceAgent",
            "coin": coin,
            "price": price,
            "change_24h": change_24h,
            "summary": summary,
            "trend": trend,
            "rsi": rsi,
            "rsi_signal": rsi_signal,
            "is_real": is_real,
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
        fg_data = await fetch_fear_greed()
        fg = fg_data["value"]
        fg_label = fg_data["label"]
        is_real = fg_data["is_real"]

        user_msg = (
            f"Монета: {coin}\n"
            f"Fear & Greed Index: {fg}/100 ({fg_label})\n"
            f"Данные: {'реальные' if is_real else 'демо'}"
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
            summary = f"Тайм-аут анализа настроений {coin}."
        except Exception as e:
            logger.error(f"SentimentAgent error for {coin}: {e}")
            summary = f"Ошибка анализа настроений: {type(e).__name__}"

        return {
            "agent": "SentimentAgent",
            "coin": coin,
            "summary": summary,
            "fear_greed": fg,
            "fear_greed_label": fg_label,
            "is_real": is_real,
        }

    async def fear_greed_only(self) -> str:
        """Fear & Greed Index — отдельный запрос."""
        fg_data = await fetch_fear_greed()
        fg = fg_data["value"]
        label_raw = fg_data["label"]
        is_real = fg_data["is_real"]

        emoji_map = {
            "Extreme Fear": "😱",
            "Fear": "😨",
            "Neutral": "😐",
            "Greed": "🤑",
            "Extreme Greed": "🚀",
        }
        emoji = emoji_map.get(label_raw, "📊")
        label = f"{label_raw} {emoji}"

        filled = fg // 5
        bar = "█" * filled + "░" * (20 - filled)

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system="Эксперт по крипторынку. Отвечай по-русски, кратко (3 предложения). Без Markdown.",
                    messages=[{
                        "role": "user",
                        "content": f"Fear & Greed Index: {fg}/100 ({label_raw}). Объясни что это значит для рынка.",
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            explanation = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Fear&Greed analysis error: {e}")
            explanation = "Не удалось получить анализ. Попробуйте позже."

        source_note = "✅ Реальные данные (Alternative.me)" if is_real else "⚠️ Демо-данные"

        return (
            f"📊 *Fear & Greed Index*\n"
            f"`[{bar}]`\n"
            f"*{fg}/100 — {label}*\n\n"
            f"{explanation}\n\n"
            f"{source_note}"
        )


class OrchestratorAgent:
    """Главный агент — синтезирует и даёт рекомендацию."""

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

    async def synthesize(self, coin: str, price_data: dict, sentiment_data: dict) -> dict:
        user_msg = (
            f"Монета: {coin}\n\n"
            f"=== ТЕХНИЧЕСКИЙ АНАЛИЗ ===\n"
            f"{price_data['summary']}\n"
            f"RSI: {price_data['rsi']} ({price_data['rsi_signal']})\n"
            f"Тренд: {price_data['trend']}\n\n"
            f"=== АНАЛИЗ НАСТРОЕНИЙ ===\n"
            f"{sentiment_data['summary']}\n"
            f"Fear & Greed: {sentiment_data['fear_greed']}/100 ({sentiment_data['fear_greed_label']})"
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
            recommendation = "🟡 ДЕРЖАТЬ\nНе удалось завершить анализ вовремя."
        except Exception as e:
            logger.error(f"OrchestratorAgent error for {coin}: {e}")
            recommendation = f"Ошибка синтеза: {type(e).__name__}"

        return {
            "agent": "OrchestratorAgent",
            "coin": coin,
            "recommendation": recommendation,
        }

    async def market_overview(self, coins_data: list[dict]) -> str:
        """Обзор рынка по реальным данным."""
        items = []
        for c in coins_data[:10]:  # топ-10 для обзора
            symbol = c.get("symbol", "?").upper()
            price = c.get("current_price", 0)
            change = c.get("price_change_percentage_24h") or 0
            emoji = "🟢" if change > 0 else "🔴"
            items.append(f"{emoji} *{symbol}*: ${price:,.2f} ({change:+.2f}%)")

        fg_data = await fetch_fear_greed()
        fg = fg_data["value"]
        items_text = "\n".join(items)

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system="Эксперт по крипторынку. Краткий обзор (3-4 предложения) по-русски. Без Markdown.",
                    messages=[{
                        "role": "user",
                        "content": f"Топ монеты сейчас:\n{items_text}\nFear & Greed: {fg}/100",
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            analysis = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Market overview error: {e}")
            analysis = "Не удалось получить анализ рынка."

        return f"{items_text}\n\n📋 *Анализ:*\n{analysis}"
