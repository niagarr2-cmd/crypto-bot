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

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

FEAR_GREED_URL = "https://api.alternative.me/fng/"


def escape_claude_response(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = text.replace("_", "\\_")
    return text.strip()


async def fetch_top_coins(limit: int = 50) -> list[dict]:
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
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    try:
        async with aiohttp.ClientSession() as session:
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


async def fetch_global_market() -> dict:
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{COINGECKO_BASE}/global",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {})
                else:
                    logger.warning(f"CoinGecko global status {resp.status}")
                    return {}
    except Exception as e:
        logger.error(f"CoinGecko fetch_global_market error: {e}")
        return {}


async def fetch_trending_coins() -> list[dict]:
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{COINGECKO_BASE}/search/trending",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("coins", [])
                else:
                    logger.warning(f"CoinGecko trending status {resp.status}")
                    return []
    except Exception as e:
        logger.error(f"CoinGecko fetch_trending error: {e}")
        return []


def calculate_rsi(ohlc_data: list, period: int = 14) -> float | None:
    if not ohlc_data or len(ohlc_data) < period + 1:
        return None

    closes = [candle[4] for candle in ohlc_data]

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

    import random
    v = random.randint(30, 70)
    return {"value": v, "label": "Neutral", "is_real": False}


_coins_cache: list[dict] = []
_coins_cache_time: float = 0
COINS_CACHE_TTL = 600


async def get_top_coins_cached(limit: int = 50) -> list[dict]:
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
    symbol_upper = symbol.upper()
    for coin in coins:
        if coin.get("symbol", "").upper() == symbol_upper:
            return coin
    return None


class PriceAgent:

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

            detail = await fetch_coin_detail(coin_id)
            rsi = None
            if detail and detail.get("ohlc"):
                rsi = calculate_rsi(detail["ohlc"])
            if rsi is None:
                rsi = 50.0

        else:
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

    async def trending_coins(self) -> str:
        coins = await fetch_trending_coins()

        if not coins:
            return "❌ Не удалось загрузить trending монеты."

        lines = []
        for i, item in enumerate(coins[:7], 1):
            coin = item.get("item", {})
            name = coin.get("name", "?")
            symbol = coin.get("symbol", "?").upper()
            rank = coin.get("market_cap_rank") or "?"
            score = coin.get("score", 0)
            lines.append(f"{i}. *{symbol}* — {name} (MCap rank: #{rank})")

        lines_text = "\n".join(lines)

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system="Эксперт по крипторынку. Краткий комментарий (2-3 предложения) по-русски. Без Markdown.",
                    messages=[{
                        "role": "user",
                        "content": f"Сейчас в тренде эти монеты:\n{lines_text}\nДай краткий комментарий.",
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            comment = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Trending comment error: {e}")
            comment = "Не удалось получить комментарий."

        return f"{lines_text}\n\n💬 *Комментарий:*\n{comment}"


class OrchestratorAgent:

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
        items = []
        for c in coins_data[:10]:
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

    async def global_market_summary(self) -> str:
        data = await fetch_global_market()

        if not data:
            return "❌ Не удалось загрузить глобальные данные рынка."

        total_mcap = data.get("total_market_cap", {}).get("usd", 0)
        total_volume = data.get("total_volume", {}).get("usd", 0)
        btc_dom = data.get("market_cap_percentage", {}).get("btc", 0)
        eth_dom = data.get("market_cap_percentage", {}).get("eth", 0)
        active_coins = data.get("active_cryptocurrencies", 0)
        mcap_change = data.get("market_cap_change_percentage_24h_usd", 0)

        mcap_t = total_mcap / 1e12
        vol_b = total_volume / 1e9
        mcap_emoji = "🟢" if mcap_change > 0 else "🔴"

        summary_lines = (
            f"💰 Общая капитализация: *${mcap_t:.2f}T* {mcap_emoji} ({mcap_change:+.2f}%)\n"
            f"📊 Объём 24ч: *${vol_b:.1f}B*\n"
            f"₿ Доминирование BTC: *{btc_dom:.1f}%*\n"
            f"Ξ Доминирование ETH: *{eth_dom:.1f}%*\n"
            f"🪙 Активных монет: *{active_coins:,}*"
        )

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system="Эксперт по крипторынку. Краткий анализ (3 предложения) по-русски. Без Markdown.",
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Глобальный крипторынок:\n"
                            f"Капитализация: ${mcap_t:.2f}T ({mcap_change:+.2f}% за 24ч)\n"
                            f"Объём: ${vol_b:.1f}B\n"
                            f"BTC доминирование: {btc_dom:.1f}%\n"
                            f"ETH доминирование: {eth_dom:.1f}%\n"
                            f"Дай краткий анализ ситуации."
                        ),
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            analysis = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Global market analysis error: {e}")
            analysis = "Не удалось получить анализ."

        return f"{summary_lines}\n\n📋 *Анализ:*\n{analysis}"

    async def futures_signals(self, coins: list[dict], direction: str = "long") -> str:
        direction_ru = "LONG 🟢" if direction == "long" else "SHORT 🔴"
        condition = "роста" if direction == "long" else "падения"

        candidates = []
        for c in coins[:20]:
            symbol = c.get("symbol", "?").upper()
            price = c.get("current_price", 0)
            change = c.get("price_change_percentage_24h") or 0
            volume = (c.get("total_volume") or 0) / 1e6
            high = c.get("high_24h") or price
            low = c.get("low_24h") or price
            candidates.append(
                f"{symbol}: ${price:,.4f} | 24ч: {change:+.2f}% | Vol: ${volume:.0f}M | H/L: ${high:,.2f}/${low:,.2f}"
            )

        candidates_text = "\n".join(candidates)

        system_prompt = (
            f"Ты профессиональный трейдер фьючерсного крипторынка. "
            f"Выбери топ-3 монеты для {direction_ru} позиций. "
            f"Для каждой укажи СТРОГО в формате:\n"
            f"МОНЕТА: символ\n"
            f"Направление: {direction_ru}\n"
            f"Вход: $цена\n"
            f"Стоп-лосс: $цена\n"
            f"Тейк-профит: $цена\n"
            f"Уверенность: X%\n"
            f"Причина: 1 предложение\n\n"
            f"Без Markdown. Разделяй сигналы линией ---"
        )

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=600,
                    system=system_prompt,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Данные рынка для {direction_ru} сигналов:\n{candidates_text}\n\n"
                            f"Выбери топ-3 кандидата для {condition}."
                        ),
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            signals = escape_claude_response(response.content[0].text)
        except asyncio.TimeoutError:
            signals = "Тайм-аут. Попробуйте позже."
        except Exception as e:
            logger.error(f"futures_signals error: {e}")
            signals = f"Ошибка генерации сигналов: {type(e).__name__}"

        return signals

    async def top_futures(self, coins: list[dict]) -> str:
        items = []
        sorted_coins = sorted(
            coins[:20],
            key=lambda c: c.get("total_volume") or 0,
            reverse=True,
        )

        for i, c in enumerate(sorted_coins[:10], 1):
            symbol = c.get("symbol", "?").upper()
            price = c.get("current_price", 0)
            change = c.get("price_change_percentage_24h") or 0
            volume = (c.get("total_volume") or 0) / 1e6
            emoji = "🟢" if change > 0 else "🔴"
            items.append(
                f"{i}. {emoji} *{symbol}*: ${price:,.2f} ({change:+.2f}%) | Vol: ${volume:.0f}M"
            )

        items_text = "\n".join(items)

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system="Эксперт по фьючерсному крипторынку. Краткий комментарий (2-3 предложения) по-русски. Без Markdown.",
                    messages=[{
                        "role": "user",
                        "content": f"Топ монеты по объёму торгов:\n{items_text}\nДай краткий комментарий по фьючерсному рынку.",
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            comment = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"top_futures error: {e}")
            comment = "Не удалось получить комментарий."

        return f"{items_text}\n\n💬 *Комментарий:*\n{comment}"


# ─────────────────────────────────────────────
# Polymarket API
# ─────────────────────────────────────────────

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"


async def fetch_polymarket_top(limit: int = 10) -> list[dict]:
    """Топ событий Polymarket по ликвидности."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{POLYMARKET_GAMMA}/events",
                params={
                    "limit": limit,
                    "active": "true",
                    "order": "liquidityClob",
                    "ascending": "false",
                    "closed": "false",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"Polymarket: loaded {len(data)} events")
                    return data if isinstance(data, list) else data.get("events", [])
                else:
                    logger.warning(f"Polymarket status {resp.status}")
                    return []
    except Exception as e:
        logger.error(f"Polymarket fetch error: {e}")
        return []



class PolymarketAgent:
    """Агент анализа рынка предсказаний Polymarket."""

    def __init__(self, client: AsyncAnthropic):
        self.client = client

    async def get_top_events(self) -> tuple[str, list[dict], str]:
        """Топ событий по ликвидности."""
        events = await fetch_polymarket_top(10)

        if not events:
            return "❌ Не удалось загрузить данные Polymarket.", [], ""

        lines = []
        events_for_ai = []
        event_buttons = []

        for i, event in enumerate(events[:10], 1):
            title = event.get("title") or event.get("question", "?")
            liquidity = float(event.get("liquidityClob") or event.get("liquidity") or 0)
            volume = float(event.get("volume") or 0)
            volume24 = float(event.get("volume24hr") or 0)

            liq_str = f"${liquidity/1e6:.1f}M" if liquidity >= 1e6 else f"${liquidity/1e3:.0f}K"
            vol_str = f"${volume/1e6:.1f}M" if volume >= 1e6 else f"${volume/1e3:.0f}K"
            vol24_str = f"${volume24/1e6:.1f}M" if volume24 >= 1e6 else f"${volume24/1e3:.0f}K"

            markets = event.get("markets", [])
            outcome_lines = []

            for m in markets[:3]:
                outcomes = m.get("outcomes", "[]")
                prices = m.get("outcomePrices", "[]")
                if isinstance(outcomes, str):
                    try:
                        import json as _j; outcomes = _j.loads(outcomes)
                    except Exception:
                        outcomes = []
                if isinstance(prices, str):
                    try:
                        import json as _j; prices = _j.loads(prices)
                    except Exception:
                        prices = []

                if outcomes and prices and len(outcomes) == len(prices):
                    for o, p in zip(outcomes[:2], prices[:2]):
                        try:
                            pct = round(float(p) * 100)
                            bar = "█" * round(pct/10) + "░" * (10 - round(pct/10))
                            emoji = "🟢" if o.lower() in ["yes","да"] else ("🔴" if o.lower() in ["no","нет"] else "🔵")
                            outcome_lines.append(f"   {emoji} {o}: *{pct}%* `{bar}`")
                        except Exception:
                            pass
                if outcome_lines:
                    break

            outcomes_text = "\n".join(outcome_lines) if outcome_lines else "   📊 Нет данных об исходах"

            line = (
                f"*{i}. {title}*\n"
                f"   💧 {liq_str} ликв. | 📊 {vol_str} объём | 24ч: {vol24_str}\n"
                f"{outcomes_text}"
            )
            lines.append(line)
            events_for_ai.append(f"{i}. {title} (ликв. {liq_str})")
            event_buttons.append({
                "index": i,
                "title": title[:35],
                "event": event,
            })

        events_text = "\n\n".join(lines)

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=(
                        "Ты эксперт по крипторынку и рынкам предсказаний. "
                        "Проанализируй топ события Polymarket КРАТКО (3 предложения) "
                        "как они могут повлиять на крипторынок. "
                        "По-русски. Без Markdown."
                    ),
                    messages=[{"role": "user", "content": f"Топ события Polymarket:\n{'\n'.join(events_for_ai)}\nКак влияют на крипту?"}],
                ),
                timeout=API_TIMEOUT,
            )
            analysis = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Polymarket AI error: {e}")
            analysis = "Не удалось получить AI анализ."

        return events_text, event_buttons, analysis

    async def get_event_detail(self, event: dict) -> str:
        """Детальная информация по одному событию."""
        title = event.get("title") or event.get("question", "?")
        description = event.get("description", "")
        liquidity = float(event.get("liquidityClob") or event.get("liquidity") or 0)
        volume = float(event.get("volume") or 0)
        markets = event.get("markets", [])

        liq_str = f"${liquidity/1e6:.2f}M" if liquidity >= 1e6 else f"${liquidity/1e3:.0f}K"
        vol_str = f"${volume/1e6:.2f}M" if volume >= 1e6 else f"${volume/1e3:.0f}K"
        sep = "─" * 28

        lines = [f"🎯 *{title}*", sep, f"💧 Ликвидность: *{liq_str}* | 📊 Объём: *{vol_str}*"]

        if description:
            lines.append(f"\n📋 _{description[:250]}_")

        lines.append("")

        for m in markets[:8]:
            q = m.get("question", "")
            outcomes = m.get("outcomes", "[]")
            prices = m.get("outcomePrices", "[]")
            if isinstance(outcomes, str):
                try:
                    import json as _j; outcomes = _j.loads(outcomes)
                except Exception:
                    outcomes = []
            if isinstance(prices, str):
                try:
                    import json as _j; prices = _j.loads(prices)
                except Exception:
                    prices = []

            if q and q != title:
                lines.append(f"❓ *{q}*")

            if outcomes and prices and len(outcomes) == len(prices):
                for o, p in zip(outcomes, prices):
                    try:
                        pct = round(float(p) * 100)
                        bar = "█" * round(pct/10) + "░" * (10 - round(pct/10))
                        emoji = "🟢" if o.lower() in ["yes","да"] else ("🔴" if o.lower() in ["no","нет"] else "🔵")
                        lines.append(f"{emoji} *{o}*: {pct}% `{bar}`")
                    except Exception:
                        pass
            lines.append("")

        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL,
                    max_tokens=300,
                    system="Эксперт крипторынка. Кратко (2-3 предложения) объясни как событие влияет на крипту. По-русски. Без Markdown.",
                    messages=[{"role": "user", "content": f"Событие Polymarket: {title}. Как влияет на крипту?"}],
                ),
                timeout=API_TIMEOUT,
            )
            lines.append(f"🤖 *AI анализ:*\n{escape_claude_response(response.content[0].text)}")
        except Exception:
            pass

        return "\n".join(lines)
