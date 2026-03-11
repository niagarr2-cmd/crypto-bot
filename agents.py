# Crypto AI Agents v4 - Caching, retry, rate limit protection

# CoinGecko + Alternative.me - all free APIs

# FIXED: cache TTL bug, duplicate API calls, sleep removed, bare except fixed

import asyncio
import re
import os
import logging
import time
import aiohttp
from datetime import datetime
from anthropic import AsyncAnthropic

logger = logging.getLogger(“agents”)

MODEL = os.getenv(“CLAUDE_MODEL”, “claude-sonnet-4-20250514”)
MAX_TOKENS = int(os.getenv(“CLAUDE_MAX_TOKENS”, “1500”))
API_TIMEOUT = int(os.getenv(“API_TIMEOUT”, “60”))

# Global cache for all API data

_cache = {}
CACHE_TTL_PRICE = 180      # 3 min for price data
CACHE_TTL_SENTIMENT = 300  # 5 min for sentiment
CACHE_TTL_GLOBAL = 300     # 5 min for global
CACHE_TTL_TRENDING = 300   # 5 min for trending
CACHE_TTL_COINS = 300      # 5 min for coins list

# FIX 1: cache_get now checks TTL before returning data

def cache_get(key):
if key in _cache:
data, ts = _cache[key]
if time.time() < ts:  # FIXED: was missing this check
return data
return None

def cache_set(key, data, ttl):
_cache[key] = (data, time.time() + ttl)
now = time.time()
expired = [k for k, (d, t) in list(_cache.items()) if t < now]
for k in expired:
del _cache[k]

def cache_valid(key):
if key in _cache:
data, ts = _cache[key]
return time.time() < ts
return False

def escape_claude_response(text):
text = re.sub(r”**(.*?)**”, r”\1”, text)
text = re.sub(r”*(.*?)*”, r”\1”, text)
text = re.sub(r”`.*?`”, “”, text, flags=re.DOTALL)
text = re.sub(r”`(.*?)`”, r”\1”, text)
text = re.sub(r”[(.*?)](.*?)”, r”\1”, text)
text = text.replace(”*”, “\*”)
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

async def _api_get(session, url, retries=3):
for attempt in range(retries):
try:
async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
if resp.status == 200:
return await resp.json()
elif resp.status == 429:
wait = 5 * (attempt + 1)
logger.warning(f”CoinGecko rate limit, waiting {wait}s”)
await asyncio.sleep(wait)
else:
logger.error(f”API error {resp.status} for {url}”)
return None
except asyncio.TimeoutError:
logger.warning(f”Timeout attempt {attempt+1} for {url}”)
await asyncio.sleep(2)
except Exception as e:
logger.error(f”API error: {e}”)
return None
return None

async def fetch_top_coins(limit=50):
cache_key = f”top_coins_{limit}”
if cache_valid(cache_key):
return cache_get(cache_key)
try:
async with aiohttp.ClientSession() as session:
url = (
f”https://api.coingecko.com/api/v3/coins/markets”
f”?vs_currency=usd&order=market_cap_desc”
f”&per_page={limit}&page=1&sparkline=false”
f”&price_change_percentage=1h,24h,7d”
)
data = await _api_get(session, url)
if not data:
cached = cache_get(cache_key)
return cached if cached else []
result = []
for coin in data:
result.append({
“id”: coin.get(“id”, “”),
“symbol”: coin.get(“symbol”, “”).upper(),
“name”: coin.get(“name”, “”),
“price”: coin.get(“current_price”, 0) or 0,
“market_cap”: coin.get(“market_cap”, 0) or 0,
“volume”: coin.get(“total_volume”, 0) or 0,
“change_1h”: coin.get(“price_change_percentage_1h_in_currency”, 0) or 0,
“change_24h”: coin.get(“price_change_percentage_24h”, 0) or 0,
“change_7d”: coin.get(“price_change_percentage_7d_in_currency”, 0) or 0,
“market_cap_rank”: coin.get(“market_cap_rank”, 0),
})
cache_set(cache_key, result, CACHE_TTL_COINS)
return result
except Exception as e:
logger.error(f”fetch_top_coins error: {e}”)
cached = cache_get(cache_key)
return cached if cached else []

async def fetch_price_data(coin_symbol, coin_id=None):
cache_key = f”price_{coin_symbol}_{coin_id}”
if cache_valid(cache_key):
return cache_get(cache_key)

```
if not coin_id or coin_id == "" or coin_id == "None":
    known = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2",
        "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
        "ATOM": "cosmos", "LTC": "litecoin", "FIL": "filecoin",
        "NEAR": "near", "APT": "aptos", "ARB": "arbitrum",
        "OP": "optimism", "SUI": "sui", "SEI": "sei-network",
        "TIA": "celestia", "INJ": "injective-protocol",
        "PEPE": "pepe", "WIF": "dogwifcoin", "BONK": "bonk",
        "SHIB": "shiba-inu", "TRX": "tron", "TON": "the-open-network",
        "HBAR": "hedera-hashgraph", "VET": "vechain",
        "ALGO": "algorand", "FTM": "fantom", "AAVE": "aave",
        "MKR": "maker", "GRT": "the-graph", "RENDER": "render-token",
        "IMX": "immutable-x", "STX": "blockstack",
        "SAND": "the-sandbox", "MANA": "decentraland",
        "CRV": "curve-dao-token", "LDO": "lido-dao", "RUNE": "thorchain",
        "GALA": "gala", "HYPE": "hyperliquid",
    }
    coin_id = known.get(coin_symbol, None)
    if not coin_id:
        coins = await fetch_top_coins(50)
        for c in coins:
            if c["symbol"] == coin_symbol:
                coin_id = c["id"]
                break
        if not coin_id:
            coin_id = coin_symbol.lower()

try:
    async with aiohttp.ClientSession() as session:
        url = (
            f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            f"?localization=false&tickers=false"
            f"&community_data=true&developer_data=true"
        )
        data = await _api_get(session, url)
        if not data:
            return cache_get(cache_key)

        md = data.get("market_data", {})
        price = md.get("current_price", {}).get("usd", 0) or 0
        change_1h = md.get("price_change_percentage_1h_in_currency", {}).get("usd", 0) or 0
        change_24h = md.get("price_change_percentage_24h", 0) or 0
        change_7d = md.get("price_change_percentage_7d", 0) or 0
        change_30d = md.get("price_change_percentage_30d", 0) or 0
        volume = md.get("total_volume", {}).get("usd", 0) or 0
        market_cap = md.get("market_cap", {}).get("usd", 0) or 0
        ath = md.get("ath", {}).get("usd", 0) or 0
        ath_change = md.get("ath_change_percentage", {}).get("usd", 0) or 0
        high_24h = md.get("high_24h", {}).get("usd", 0) or 0
        low_24h = md.get("low_24h", {}).get("usd", 0) or 0
        sentiment_up = data.get("sentiment_votes_up_percentage", 0) or 0
        sentiment_down = data.get("sentiment_votes_down_percentage", 0) or 0
        community_score = data.get("community_score", 0) or 0
        developer_score = data.get("developer_score", 0) or 0

        # FIX 3: removed asyncio.sleep(1.5)

        url_hist = (
            f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            f"/market_chart?vs_currency=usd&days=200&interval=daily"
        )
        hist_data = await _api_get(session, url_hist)
        hist_prices = []
        if hist_data:
            hist_prices = [p[1] for p in hist_data.get("prices", [])]

        rsi = compute_rsi(hist_prices) if len(hist_prices) > 14 else 50.0
        ma_7 = round(sum(hist_prices[-7:]) / max(len(hist_prices[-7:]), 1), 2) if hist_prices else price
        ma_25 = round(sum(hist_prices[-25:]) / max(len(hist_prices[-25:]), 1), 2) if hist_prices else price
        ma_50 = round(sum(hist_prices[-50:]) / max(len(hist_prices[-50:]), 1), 2) if hist_prices else price
        ma_99 = round(sum(hist_prices[-99:]) / max(len(hist_prices[-99:]), 1), 2) if hist_prices else price
        ma_200 = round(sum(hist_prices[-200:]) / max(len(hist_prices[-200:]), 1), 2) if hist_prices else price

        result = {
            "coin": coin_symbol, "coin_id": coin_id, "price": round(price, 6),
            "change_1h": round(change_1h, 2), "change_24h": round(change_24h, 2),
            "change_7d": round(change_7d, 2), "change_30d": round(change_30d, 2),
            "volume_24h": round(volume / 1e9, 2), "market_cap": round(market_cap / 1e9, 2),
            "high_24h": round(high_24h, 6), "low_24h": round(low_24h, 6),
            "ath": round(ath, 2), "ath_change": round(ath_change, 1),
            "rsi": rsi, "ma_7": ma_7, "ma_25": ma_25,
            "ma_50": ma_50, "ma_99": ma_99, "ma_200": ma_200,
            "sentiment_up": round(sentiment_up), "sentiment_down": round(sentiment_down),
            "community_score": community_score, "developer_score": developer_score,
            "timestamp": datetime.now().isoformat(), "is_demo": False,
        }
        cache_set(cache_key, result, CACHE_TTL_PRICE)
        return result
except Exception as e:
    logger.error(f"fetch_price_data error for {coin_symbol}: {e}")
    return cache_get(cache_key)
```

# FIX 5: fetch_sentiment_data reuses fetch_fear_greed — no duplicate API call

async def fetch_sentiment_data(coin_symbol):
cache_key = f”sentiment_{coin_symbol}”
if cache_valid(cache_key):
return cache_get(cache_key)
fg_value, fg_label = await fetch_fear_greed()
result = {“fear_greed_index”: fg_value, “fear_greed_label”: fg_label}
cache_set(cache_key, result, CACHE_TTL_SENTIMENT)
return result

async def fetch_fear_greed():
cache_key = “fear_greed”
if cache_valid(cache_key):
d = cache_get(cache_key)
return d[0], d[1]
try:
async with aiohttp.ClientSession() as session:
url = “https://api.alternative.me/fng/?limit=1”
data = await _api_get(session, url)
if not data:
return 50, “Neutral”
entry = data.get(“data”, [{}])[0]
fg = int(entry.get(“value”, 50))
label = entry.get(“value_classification”, “Neutral”)
cache_set(cache_key, (fg, label), CACHE_TTL_SENTIMENT)
return fg, label
except Exception as e:  # FIX 4: was bare except
logger.error(f”fetch_fear_greed error: {e}”)
return 50, “Neutral”

async def fetch_trending():
cache_key = “trending”
if cache_valid(cache_key):
return cache_get(cache_key)
try:
async with aiohttp.ClientSession() as session:
url = “https://api.coingecko.com/api/v3/search/trending”
data = await _api_get(session, url)
if not data:
cached = cache_get(cache_key)
return cached if cached else []
coins = data.get(“coins”, [])
result = []
for item in coins[:10]:
c = item.get(“item”, {})
result.append({
“name”: c.get(“name”, “”),
“symbol”: c.get(“symbol”, “”).upper(),
“market_cap_rank”: c.get(“market_cap_rank”, 0),
“id”: c.get(“id”, “”),
})
cache_set(cache_key, result, CACHE_TTL_TRENDING)
return result
except Exception as e:
logger.error(f”fetch_trending error: {e}”)
cached = cache_get(cache_key)
return cached if cached else []

async def fetch_global_data():
cache_key = “global_data”
if cache_valid(cache_key):
return cache_get(cache_key)
try:
async with aiohttp.ClientSession() as session:
url = “https://api.coingecko.com/api/v3/global”
raw = await _api_get(session, url)
if not raw:
return cache_get(cache_key)
data = raw.get(“data”, {})
result = {
“total_market_cap”: round(data.get(“total_market_cap”, {}).get(“usd”, 0) / 1e12, 2),
“total_volume”: round(data.get(“total_volume”, {}).get(“usd”, 0) / 1e9, 1),
“btc_dominance”: round(data.get(“market_cap_percentage”, {}).get(“btc”, 0), 1),
“eth_dominance”: round(data.get(“market_cap_percentage”, {}).get(“eth”, 0), 1),
“active_coins”: data.get(“active_cryptocurrencies”, 0),
“market_cap_change_24h”: round(data.get(“market_cap_change_percentage_24h_usd”, 0), 2),
}
cache_set(cache_key, result, CACHE_TTL_GLOBAL)
return result
except Exception as e:
logger.error(f”fetch_global_data error: {e}”)
return cache_get(cache_key)

class PriceAgent:
SYSTEM_PROMPT = (
“IMPORTANT: You MUST reply ONLY in Russian using Cyrillic alphabet. “
“Never use Latin/transliteration. “
“You are a professional crypto technical analyst. “
“Give a DETAILED analysis (6-8 sentences) covering: “
“1) Current trend and key levels “
“2) RSI and MA signals “
“3) Short-term outlook (1-7 days) “
“4) Mid-term outlook (1-4 weeks) “
“5) Long-term outlook (1-3 months) “
“6) Futures trading suggestion with entry zones and leverage recommendation “
“No Markdown formatting. Plain text only. ONLY Cyrillic Russian.”
)

```
def __init__(self, client):
    self.client = client

async def analyze(self, coin, coin_id=None):
    data = await fetch_price_data(coin, coin_id)
    if data is None:
        return {
            "agent": "PriceAgent", "coin": coin,
            "raw_data": {}, "summary": "Не удалось получить данные. Попробуйте через минуту.",
            "trend": "unknown", "rsi": 50, "rsi_signal": "unknown",
        }
    trend = "бычий" if data["change_24h"] > 2 else "медвежий" if data["change_24h"] < -2 else "боковой"
    rsi_signal = "перекуплен" if data["rsi"] > 70 else "перепродан" if data["rsi"] < 30 else "нейтрален"

    user_msg = (
        f"Монета: {coin}\n"
        f"Цена: ${data['price']:,.6f}\n"
        f"Изменение 1ч: {data['change_1h']:+.2f}%\n"
        f"Изменение 24ч: {data['change_24h']:+.2f}%\n"
        f"Изменение 7д: {data['change_7d']:+.2f}%\n"
        f"Изменение 30д: {data['change_30d']:+.2f}%\n"
        f"Объём 24ч: ${data['volume_24h']}B\n"
        f"Капитализация: ${data['market_cap']}B\n"
        f"Хай 24ч: ${data['high_24h']:,.6f}\n"
        f"Лоу 24ч: ${data['low_24h']:,.6f}\n"
        f"ATH: ${data['ath']:,.2f} ({data['ath_change']:+.1f}% от ATH)\n"
        f"RSI(14): {data['rsi']} ({rsi_signal})\n"
        f"MA7: ${data['ma_7']:,.2f} | MA25: ${data['ma_25']:,.2f}\n"
        f"MA50: ${data['ma_50']:,.2f} | MA99: ${data['ma_99']:,.2f}\n"
        f"MA200: ${data['ma_200']:,.2f}\n"
        f"Тренд: {trend}\n"
        f"Настроения: {data['sentiment_up']}% позитив / {data['sentiment_down']}% негатив"
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
        logger.error(f"PriceAgent error for {coin}: {e}")
        summary = f"Ошибка анализа: {type(e).__name__}"
    return {
        "agent": "PriceAgent", "coin": coin,
        "raw_data": data, "summary": summary,
        "trend": trend, "rsi": data["rsi"], "rsi_signal": rsi_signal,
    }
```

# FIX 2: SentimentAgent.analyze accepts optional price_data to avoid duplicate API call

class SentimentAgent:
SYSTEM_PROMPT = (
“IMPORTANT: You MUST reply ONLY in Russian using Cyrillic alphabet. “
“Never use Latin/transliteration. “
“You are a crypto market sentiment expert. “
“Give a DETAILED analysis (5-7 sentences) covering: “
“1) Fear & Greed interpretation “
“2) Community sentiment analysis “
“3) What this means for short-term and mid-term “
“4) Historical context of similar sentiment levels “
“No Markdown formatting. Plain text only. ONLY Cyrillic Russian.”
)

```
def __init__(self, client):
    self.client = client

async def analyze(self, coin, coin_id=None, price_data=None):
    if price_data is None:
        price_data = await fetch_price_data(coin, coin_id)
    sentiment = await fetch_sentiment_data(coin)
    if price_data is None:
        return {
            "agent": "SentimentAgent", "coin": coin,
            "raw_data": {}, "summary": "Не удалось получить данные. Попробуйте через минуту.",
            "fear_greed": sentiment["fear_greed_index"],
            "fear_greed_label": sentiment["fear_greed_label"],
        }
    user_msg = (
        f"Монета: {coin}\n"
        f"Fear & Greed Index: {sentiment['fear_greed_index']}/100 ({sentiment['fear_greed_label']})\n"
        f"Настроения сообщества: {price_data['sentiment_up']}% позитив / {price_data['sentiment_down']}% негатив\n"
        f"Community Score: {price_data['community_score']}\n"
        f"Developer Score: {price_data['developer_score']}\n"
        f"Изменение цены 24ч: {price_data['change_24h']:+.2f}%\n"
        f"Изменение цены 7д: {price_data['change_7d']:+.2f}%\n"
        f"Объём торгов: ${price_data['volume_24h']}B"
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
        logger.error(f"SentimentAgent error for {coin}: {e}")
        summary = f"Ошибка анализа настроений: {type(e).__name__}"
    return {
        "agent": "SentimentAgent", "coin": coin,
        "raw_data": price_data, "summary": summary,
        "fear_greed": sentiment["fear_greed_index"],
        "fear_greed_label": sentiment["fear_greed_label"],
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
                    "IMPORTANT: Reply ONLY in Russian using Cyrillic alphabet. "
                    "Never use Latin/transliteration. "
                    "You are a crypto market expert. "
                    "Give detailed analysis (4-5 sentences). No Markdown."
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
        explanation = "Не удалось получить анализ."
    return (
        f"*Fear & Greed Index*\n"
        f"[{bar}]\n"
        f"*{fg}/100 - {label}*\n\n"
        f"{explanation}"
    )
```

class OrchestratorAgent:
SYSTEM_PROMPT = (
“IMPORTANT: You MUST reply ONLY in Russian using Cyrillic alphabet. “
“Never use Latin/transliteration. “
“You are the chief analyst of a crypto fund. “
“Synthesize data from two analysts and give a DETAILED recommendation.\n”
“Response format STRICTLY:\n”
“СИГНАЛ: ПОКУПАТЬ / ДЕРЖАТЬ / ПРОДАВАТЬ\n”
“Уверенность: X%\n\n”
“КРАТКОСРОК (1-7 дней): рекомендация и обоснование\n”
“СРЕДНЕСРОК (1-4 недели): рекомендация и обоснование\n”
“ДОЛГОСРОК (1-3 месяца): рекомендация и обоснование\n\n”
“ФЬЮЧЕРСЫ: направление (лонг/шорт), зона входа, “
“рекомендуемое плечо, стоп-лосс, тейк-профит\n\n”
“Ключевые факторы: (3-4 пункта)\n”
“Риски: (2-3 пункта)\n\n”
“No Markdown. Plain text only. ONLY Cyrillic Russian.”
)

```
def __init__(self, client):
    self.client = client

async def synthesize(self, coin, price_data, sentiment_data):
    raw = price_data.get("raw_data", {})
    user_msg = (
        f"Монета: {coin}\n"
        f"Цена: ${raw.get('price', 0):,.6f}\n"
        f"ATH: ${raw.get('ath', 0):,.2f} ({raw.get('ath_change', 0):+.1f}%)\n\n"
        f"=== ТЕХНИЧЕСКИЙ АНАЛИЗ ===\n"
        f"{price_data['summary']}\n"
        f"RSI: {price_data['rsi']} ({price_data['rsi_signal']})\n"
        f"Тренд: {price_data['trend']}\n\n"
        f"=== АНАЛИЗ НАСТРОЕНИЙ ===\n"
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
        recommendation = f"Ошибка синтеза: {type(e).__name__}"
    return {
        "agent": "OrchestratorAgent", "coin": coin,
        "recommendation": recommendation,
    }

async def market_overview(self, coins_data):
    items = []
    for cd in coins_data:
        emoji = "🟢" if cd["change_24h"] > 0 else "🔴"
        items.append(
            f"{emoji} *{cd['symbol']}*: ${cd['price']:,.2f} "
            f"({cd['change_24h']:+.2f}%)"
        )
    fg, fg_label = await fetch_fear_greed()
    global_data = await fetch_global_data()
    items_text = "\n".join(items)
    global_text = ""
    if global_data:
        global_text = (
            f"\nКапитализация: ${global_data['total_market_cap']}T\n"
            f"Объём 24ч: ${global_data['total_volume']}B\n"
            f"BTC: {global_data['btc_dominance']}% | ETH: {global_data['eth_dominance']}%\n"
            f"Изменение 24ч: {global_data['market_cap_change_24h']:+.2f}%"
        )
    try:
        response = await asyncio.wait_for(
            self.client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS,
                system=(
                    "IMPORTANT: Reply ONLY in Russian using Cyrillic alphabet. "
                    "Never use Latin/transliteration. "
                    "You are a crypto market expert. "
                    "Give detailed overview (5-7 sentences) with recommendations. No Markdown."
                ),
                messages=[{
                    "role": "user",
                    "content": f"Рынок сегодня:\n{items_text}\n{global_text}\n"
                               f"Fear & Greed: {fg}/100 ({fg_label})\n"
                               f"Какие монеты интересны для торговли?",
                }],
            ),
            timeout=API_TIMEOUT,
        )
        analysis = escape_claude_response(response.content[0].text)
    except Exception as e:
        logger.error(f"Market overview error: {e}")
        analysis = "Не удалось получить анализ рынка."
    return (
        f"{items_text}\n{global_text}\n\n"
        f"*Анализ:*\n{analysis}"
    )
```
