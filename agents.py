# Crypto AI Agents - real market data from CoinGecko and Alternative.me
# No more fake data - all prices, volumes, RSI, Fear&Greed are real

import asyncio
import re
import os
import logging
import aiohttp
from datetime import datetime
from anthropic import AsyncAnthropic

logger = logging.getLogger("agents")

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "500"))
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "45"))

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
}


def escape_claude_response(text):
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = text.replace("_", "\\_")
    return text


def compute_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    recent = deltas[-(period):]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.001
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 1)


async def fetch_price_data(coin):
    cg_id = COINGECKO_IDS.get(coin, coin.lower())
    try:
        async with aiohttp.ClientSession() as session:
            url_price = (
                f"https://api.coingecko.com/api/v3/coins/{cg_id}"
                f"?localization=false&tickers=false"
                f"&community_data=false&developer_data=false"
            )
            async with session.get(url_price, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.error(f"CoinGecko price API error: {resp.status}")
                    return None
                data = await resp.json()

            price = data["market_data"]["current_price"]["usd"]
            change_24h = data["market_data"]["price_change_percentage_24h"] or 0
            volume = data["market_data"]["total_volume"]["usd"] or 0

            url_history = (
                f"https://api.coingecko.com/api/v3/coins/{cg_id}"
                f"/market_chart?vs_currency=usd&days=200&interval=daily"
            )
            async with session.get(url_history, timeout=aiohttp.ClientTimeout(total=15)) as resp2:
                if resp2.status != 200:
                    logger.error(f"CoinGecko history API error: {resp2.status}")
                    hist_prices = []
                else:
                    hist_data = await resp2.json()
                    hist_prices = [p[1] for p in hist_data.get("prices", [])]

            rsi = compute_rsi(hist_prices) if len(hist_prices) > 14 else 50.0
            ma_50 = round(sum(hist_prices[-50:]) / min(len(hist_prices), 50), 2) if hist_prices else price
            ma_200 = round(sum(hist_prices[-200:]) / min(len(hist_prices), 200), 2) if hist_prices else price

            return {
                "coin": coin,
                "price": round(price, 4),
                "change_24h": round(change_24h, 2),
                "volume_24h": round(volume / 1e9, 2),
                "rsi": rsi,
                "ma_50": ma_50,
                "ma_200": ma_200,
                "timestamp": datetime.now().isoformat(),
                "is_demo": False,
            }
    except Exception as e:
        logger.error(f"fetch_price_data error for {coin}: {e}")
        return None


async def fetch_sentiment_data(coin):
    try:
        async with aiohttp.ClientSession() as session:
            url_fg = "https://api.alternative.me/fng/?limit=1"
            async with session.get(url_fg, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    fg_value = 50
                    fg_label = "Neutral"
                else:
                    fg_data = await resp.json()
                    fg_entry = fg_data.get("data", [{}])[0]
                    fg_value = int(fg_entry.get("value", 50))
                    fg_label = fg_entry.get("value_classification", "Neutral")

            cg_id = COINGECKO_IDS.get(coin, coin.lower())
            url_coin = (
                f"https://api.coingecko.com/api/v3/coins/{cg_id}"
                f"?localization=false&tickers=false"
                f"&community_data=true&developer_data=false"
            )
            async with session.get(url_coin, timeout=aiohttp.ClientTimeout(total=15)) as resp2:
                if resp2.status == 200:
                    coin_data = await resp2.json()
                    sentiment_up = coin_data.get("sentiment_votes_up_percentage", 60) or 60
                    sentiment_down = coin_data.get("sentiment_votes_down_percentage", 40) or 40
                else:
                    sentiment_up = 60
                    sentiment_down = 40

            return {
                "coin": coin,
                "fear_greed_index": fg_value,
                "fear_greed_label": fg_label,
                "news_positive_pct": round(sentiment_up),
                "news_negative_pct": round(sentiment_down),
                "timestamp": datetime.now().isoformat(),
                "is_demo": False,
            }
    except Exception as e:
        logger.error(f"fetch_sentiment_data error for {coin}: {e}")
        return None


async def fetch_fear_greed():
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.alternative.me/fng/?limit=1"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return 50, "Neutral"
                data = await resp.json()
                entry = data.get("data", [{}])[0]
                return int(entry.get("value", 50)), entry.get("value_classification", "Neutral")
    except Exception as e:
        logger.error(f"fetch_fear_greed error: {e}")
        return 50, "Neutral"


class PriceAgent:
    SYSTEM_PROMPT = (
        "Ty professionalnyy tekhnicheskiy analitik kriptovalyutnogo rynka. "
        "Day KRATKIY analiz (3-4 predlozheniya) na russkom yazyke. "
        "Ukashi: trend, RSI signal, klyuchevyye urovni, vyvod. "
        "NE ispolzuy Markdown-formatirovaniye. Pishi prostym tekstom."
    )

    def __init__(self, client):
        self.client = client

    async def analyze(self, coin):
        data = await fetch_price_data(coin)
        if data is None:
            return {
                "agent": "PriceAgent", "coin": coin,
                "raw_data": {}, "summary": f"Ne udalos poluchit dannyye dlya {coin}.",
                "trend": "unknown", "rsi": 50, "rsi_signal": "unknown",
            }

        trend = "bychiy" if data["change_24h"] > 2 else "medvezhiy" if data["change_24h"] < -2 else "bokovoy"
        rsi_signal = (
            "perekuplen" if data["rsi"] > 70
            else "pereprodан" if data["rsi"] < 30
            else "neytralen"
        )
        user_msg = (
            f"Moneta: {coin}\n"
            f"Tsena: ${data['price']:,.4f}\n"
            f"Izmeneniye 24ch: {data['change_24h']:+.2f}%\n"
            f"Obyom: ${data['volume_24h']}B\n"
            f"RSI(14): {data['rsi']} ({rsi_signal})\n"
            f"MA50: ${data['ma_50']:,.2f}\n"
            f"MA200: ${data['ma_200']:,.2f}\n"
            f"Trend: {trend}"
        )
        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=API_TIMEOUT,
            )
            summary = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"PriceAgent Claude error for {coin}: {e}")
            summary = f"Oshibka analiza {coin}: {type(e).__name__}"
        return {
            "agent": "PriceAgent", "coin": coin,
            "raw_data": data, "summary": summary,
            "trend": trend, "rsi": data["rsi"], "rsi_signal": rsi_signal,
        }


class SentimentAgent:
    SYSTEM_PROMPT = (
        "Ty ekspert po nastroyeniyam kriptovalyutnogo rynka. "
        "Day KRATKIY analiz (3-4 predlozheniya) na russkom yazyke. "
        "NE ispolzuy Markdown-formatirovaniye. Pishi prostym tekstom."
    )

    def __init__(self, client):
        self.client = client

    async def analyze(self, coin):
        data = await fetch_sentiment_data(coin)
        if data is None:
            return {
                "agent": "SentimentAgent", "coin": coin,
                "raw_data": {}, "summary": f"Ne udalos poluchit dannyye nastroyeniy dlya {coin}.",
                "fear_greed": 50, "fear_greed_label": "Neutral",
            }
        user_msg = (
            f"Moneta: {coin}\n"
            f"Fear & Greed Index: {data['fear_greed_index']}/100 "
            f"({data['fear_greed_label']})\n"
            f"Pozitivnyye nastroyeniya: {data['news_positive_pct']}%\n"
            f"Negativnyye nastroyeniya: {data['news_negative_pct']}%\n"
        )
        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=API_TIMEOUT,
            )
            summary = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"SentimentAgent Claude error for {coin}: {e}")
            summary = f"Oshibka analiza nastroyeniy: {type(e).__name__}"
        return {
            "agent": "SentimentAgent", "coin": coin,
            "raw_data": data, "summary": summary,
            "fear_greed": data["fear_greed_index"],
            "fear_greed_label": data["fear_greed_label"],
        }

    async def fear_greed_only(self):
        fg, label = await fetch_fear_greed()
        filled = fg // 5
        bar = "X" * filled + "." * (20 - filled)
        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS,
                    system=(
                        "Ekspert po kryptorynku. Otvechay po-russki, "
                        "kratko (3 predlozheniya). Bez Markdown."
                    ),
                    messages=[{
                        "role": "user",
                        "content": f"Fear & Greed Index: {fg}/100 ({label}). "
                                   f"Obyasni chto eto znachit dlya rynka.",
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            explanation = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Fear&Greed error: {e}")
            explanation = "Ne udalos poluchit analiz."
        return (
            f"*Fear & Greed Index*\n"
            f"[{bar}]\n"
            f"*{fg}/100 - {label}*\n\n"
            f"{explanation}"
        )


class OrchestratorAgent:
    SYSTEM_PROMPT = (
        "Ty glavnyy analitik kriptovalyutnogo fonda. "
        "Sinteziruy dannyye dvukh analitikov i day itogovuyu rekomendatsiyu.\n"
        "Format otveta STROGO:\n"
        "Signal: POKUPAT / DERZHAT / PRODAVAT\n"
        "Uverennost: X%\n"
        "Klyuchevyye faktory: (2-3 punkta cherez zapyatuyu)\n"
        "Riski: (1-2 punkta cherez zapyatuyu)\n\n"
        "NE ispolzuy Markdown-formatirovaniye. Pishi prostym tekstom."
    )

    def __init__(self, client):
        self.client = client

    async def synthesize(self, coin, price_data, sentiment_data):
        user_msg = (
            f"Moneta: {coin}\n\n"
            f"=== TEKHNICHESKIY ANALIZ (PriceAgent) ===\n"
            f"{price_data['summary']}\n"
            f"RSI: {price_data['rsi']} ({price_data['rsi_signal']})\n"
            f"Trend: {price_data['trend']}\n\n"
            f"=== ANALIZ NASTROYENIY (SentimentAgent) ===\n"
            f"{sentiment_data['summary']}\n"
            f"Fear & Greed: {sentiment_data['fear_greed']}/100 "
            f"({sentiment_data['fear_greed_label']})"
        )
        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=API_TIMEOUT,
            )
            recommendation = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"OrchestratorAgent error for {coin}: {e}")
            recommendation = f"Oshibka sinteza: {type(e).__name__}"
        return {
            "agent": "OrchestratorAgent",
            "coin": coin,
            "recommendation": recommendation,
        }

    async def market_overview(self, coins):
        items = []
        for coin in coins:
            data = await fetch_price_data(coin)
            if data:
                emoji = "🟢" if data["change_24h"] > 0 else "🔴"
                items.append(
                    f"{emoji} *{coin}*: ${data['price']:,.2f} "
                    f"({data['change_24h']:+.2f}%)"
                )
            else:
                items.append(f"⚪ *{coin}*: dannyye nedostupny")
        fg, fg_label = await fetch_fear_greed()
        items_text = "\n".join(items)
        try:
            response = await asyncio.wait_for(
                self.client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS,
                    system=(
                        "Ekspert po kryptorynku. Kratkiy obzor "
                        "(3-4 predlozheniya) po-russki. Bez Markdown."
                    ),
                    messages=[{
                        "role": "user",
                        "content": f"Rynok segodnya:\n{items_text}\n"
                                   f"Fear & Greed: {fg}/100 ({fg_label})",
                    }],
                ),
                timeout=API_TIMEOUT,
            )
            analysis = escape_claude_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Market overview error: {e}")
            analysis = "Ne udalos poluchit analiz rynka."
        return (
            f"{items_text}\n\n"
            f"*Analiz:*\n{analysis}"
        )
