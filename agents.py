# Crypto AI Agents - Tri agenta dlya analiza kryptorynka.

# AsyncAnthropic vmesto sinhronnogo Anthropic

# Obrabotka oshibok Claude API

# Ekranirovaniye Markdown-simvolov dlya Telegram

import asyncio
import random
import re
import os
import logging
from datetime import datetime
from anthropic import AsyncAnthropic

logger = logging.getLogger(“agents”)

MODEL = os.getenv(“CLAUDE_MODEL”, “claude-sonnet-4-20250514”)
MAX_TOKENS = int(os.getenv(“CLAUDE_MAX_TOKENS”, “500”))
API_TIMEOUT = int(os.getenv(“API_TIMEOUT”, “45”))

def escape_markdown(text):
escape_chars = r”[`[]()~>#+-=|{}.!]”
return re.sub(escape_chars, lambda m: “\” + m.group(), text)

def escape_claude_response(text):
text = re.sub(r”**(.*?)**”, r”\1”, text)
text = re.sub(r”*(.*?)*”, r”\1”, text)
text = re.sub(r”`.*?`”, “”, text, flags=re.DOTALL)
text = re.sub(r”`(.*?)`”, r”\1”, text)
text = re.sub(r”[(.*?)](.*?)”, r”\1”, text)
text = text.replace(”*”, “\*”)
return text

def fake_price_data(coin):
base_prices = {
“BTC”: 65000, “ETH”: 3200, “BNB”: 580,
“SOL”: 170, “XRP”: 0.62,
}
price = base_prices.get(coin, 100) * random.uniform(0.95, 1.05)
change_24h = random.uniform(-8, 8)
volume = random.uniform(1e9, 5e10)
rsi = random.uniform(25, 80)
ma_50 = price * random.uniform(0.92, 1.05)
ma_200 = price * random.uniform(0.85, 1.10)
return {
“coin”: coin,
“price”: round(price, 4),
“change_24h”: round(change_24h, 2),
“volume_24h”: round(volume / 1e9, 2),
“rsi”: round(rsi, 1),
“ma_50”: round(ma_50, 2),
“ma_200”: round(ma_200, 2),
“timestamp”: datetime.now().isoformat(),
“is_demo”: True,
}

def fake_sentiment_data(coin):
labels = [“Extreme Fear”, “Fear”, “Neutral”, “Greed”, “Extreme Greed”]
fg_value = random.randint(10, 90)
fg_label = labels[min(fg_value // 20, 4)]
positive_pct = random.randint(30, 75)
return {
“coin”: coin,
“fear_greed_index”: fg_value,
“fear_greed_label”: fg_label,
“news_positive_pct”: positive_pct,
“news_negative_pct”: 100 - positive_pct,
“social_mentions_24h”: random.randint(5000, 200000),
“trending_score”: round(random.uniform(1, 10), 1),
“is_demo”: True,
}

class PriceAgent:
SYSTEM_PROMPT = (
“Ty professionalnyy tekhnicheskiy analitik kriptovalyutnogo rynka. “
“Day KRATKIY analiz (3-4 predlozheniya) na russkom yazyke. “
“Ukashi: trend, RSI signal, klyuchevyye urovni, vyvod. “
“NE ispolzuy Markdown-formatirovaniye. Pishi prostym tekstom.”
)

```
def __init__(self, client):
    self.client = client

async def analyze(self, coin):
    data = fake_price_data(coin)
    trend = "bychiy" if data["change_24h"] > 0 else "medvezhiy"
    if abs(data["change_24h"]) < 1.0:
        trend = "bokovoy"
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
        summary = f"Timeout analiza {coin}. Poprobuyte pozhe."
    except Exception as e:
        logger.error(f"PriceAgent error for {coin}: {e}")
        summary = f"Oshibka analiza {coin}: {type(e).__name__}"
    return {
        "agent": "PriceAgent",
        "coin": coin,
        "raw_data": data,
        "summary": summary,
        "trend": trend,
        "rsi": data["rsi"],
        "rsi_signal": rsi_signal,
    }
```

class SentimentAgent:
SYSTEM_PROMPT = (
“Ty ekspert po nastroyeniyam kriptovalyutnogo rynka. “
“Day KRATKIY analiz (3-4 predlozheniya) na russkom yazyke. “
“NE ispolzuy Markdown-formatirovaniye. Pishi prostym tekstom.”
)

```
def __init__(self, client):
    self.client = client

async def analyze(self, coin):
    data = fake_sentiment_data(coin)
    user_msg = (
        f"Moneta: {coin}\n"
        f"Fear & Greed Index: {data['fear_greed_index']}/100 "
        f"({data['fear_greed_label']})\n"
        f"Pozitivnyye novosti: {data['news_positive_pct']}%\n"
        f"Negativnyye novosti: {data['news_negative_pct']}%\n"
        f"Upominaniya v sotssetyakh za 24ch: {data['social_mentions_24h']:,}\n"
        f"Trend-skor: {data['trending_score']}/10"
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
        summary = f"Timeout analiza nastroyeniy {coin}."
    except Exception as e:
        logger.error(f"SentimentAgent error for {coin}: {e}")
        summary = f"Oshibka analiza nastroyeniy: {type(e).__name__}"
    return {
        "agent": "SentimentAgent",
        "coin": coin,
        "raw_data": data,
        "summary": summary,
        "fear_greed": data["fear_greed_index"],
        "fear_greed_label": data["fear_greed_label"],
    }

async def fear_greed_only(self):
    fg = random.randint(10, 90)
    thresholds = [
        (25, "Extreme Fear"),
        (45, "Fear"),
        (55, "Neutral"),
        (75, "Greed"),
        (101, "Extreme Greed"),
    ]
    label = next(lbl for thresh, lbl in thresholds if fg < thresh)
    filled = fg // 5
    bar = "X" * filled + "." * (20 - filled)
    try:
        response = await asyncio.wait_for(
            self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
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
        explanation = "Ne udalos poluchit analiz. Poprobuyte pozhe."
    return (
        f"*Fear & Greed Index*\n"
        f"[{bar}]\n"
        f"*{fg}/100 - {label}*\n\n"
        f"{explanation}"
    )
```

class OrchestratorAgent:
SYSTEM_PROMPT = (
“Ty glavnyy analitik kriptovalyutnogo fonda. “
“Sinteziruy dannyye dvukh analitikov i day itogovuyu rekomendatsiyu.\n”
“Format otveta STROGO:\n”
“Signal: POKUPAT / DERZHAT / PRODAVAT\n”
“Uverennost: X%\n”
“Klyuchevyye faktory: (2-3 punkta cherez zapyatuyu)\n”
“Riski: (1-2 punkta cherez zapyatuyu)\n\n”
“NE ispolzuy Markdown-formatirovaniye. Pishi prostym tekstom.”
)

```
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
        recommendation = "DERZHAT\nNe udalos zavershit analiz vovremya."
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
        data = fake_price_data(coin)
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
                    "Ekspert po kryptorynku. Kratkiy obzor "
                    "(3-4 predlozheniya) po-russki. Bez Markdown."
                ),
                messages=[{
                    "role": "user",
                    "content": f"Rynok segodnya:\n{items_text}\n"
                               f"Fear & Greed: {fg}/100",
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
```
