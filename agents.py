import asyncio
import json
import random
from datetime import datetime
from typing import Any

from anthropic import Anthropic

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1000


def _fake_price_data(coin: str) -> dict:
    base = {"BTC": 65000, "ETH": 3200, "BNB": 580, "SOL": 170, "XRP": 0.62}
    price = base.get(coin, 100) * random.uniform(0.95, 1.05)
    change_24h = random.uniform(-8, 8)
    volume = random.uniform(1e9, 5e10)
    rsi = random.uniform(30, 75)
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
    }


def _fake_sentiment_data(coin: str) -> dict:
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
    }


class PriceAgent:
    SYSTEM_PROMPT = """Ты профессиональный технический аналитик криптовалютного рынка.
Тебе дают рыночные данные: цену, изменение за 24ч, RSI, скользящие средние и объём.
Твоя задача — дать КРАТКИЙ (3-5 предложений) технический анализ на русском языке.
Укажи: текущий тренд (бычий/медвежий/боковой), уровни поддержки/сопротивления,
сигнал RSI (перекупленность/перепроданность) и общий вывод.
Будь конкретным и профессиональным. Не давай финансовых советов."""

    def __init__(self, client: Anthropic):
        self.client = client

    async def analyze(self, coin: str) -> dict[str, Any]:
        data = _fake_price_data(coin)
        trend = "бычий" if data["change_24h"] > 0 else "медвежий"
        ma_signal = "выше MA50" if data["price"] > data["ma_50"] else "ниже MA50"
        rsi_signal = (
            "перекуплен" if data["rsi"] > 70
            else "перепродан" if data["rsi"] < 30
            else "нейтрален"
        )
        user_message = f"""Монета: {coin}
Цена: ${data['price']:,}
Изменение за 24ч: {data['change_24h']:+.2f}%
Объём торгов: ${data['volume_24h']}B
RSI (14): {data['rsi']} — {rsi_signal}
MA50: ${data['ma_50']:,} (цена {ma_signal})
MA200: ${data['ma_200']:,}
Тренд: {trend}
Дай технический анализ."""
        response = self.client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return {
            "agent": "PriceAgent", "coin": coin,
            "raw_data": data, "summary": response.content[0].text,
            "trend": trend, "rsi": data["rsi"], "rsi_signal": rsi_signal,
        }


class SentimentAgent:
    SYSTEM_PROMPT = """Ты эксперт по анализу настроений криптовалютного рынка.
Тебе дают: индекс Fear & Greed, соотношение позитивных/негативных новостей,
количество упоминаний в соцсетях и тренд-скор.
Твоя задача — дать КРАТКИЙ (3-5 предложений) анализ настроений на русском языке."""

    def __init__(self, client: Anthropic):
        self.client = client

    async def analyze(self, coin: str) -> dict[str, Any]:
        data = _fake_sentiment_data(coin)
        user_message = f"""Монета: {coin}
Fear & Greed Index: {data['fear_greed_index']}/100 ({data['fear_greed_label']})
Новости позитивные: {data['news_positive_pct']}%
Новости негативные: {data['news_negative_pct']}%
Упоминаний в соцсетях за 24ч: {data['social_mentions_24h']:,}
Тренд-скор (1-10): {data['trending_score']}
Дай анализ настроений рынка."""
        response = self.client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return {
            "agent": "SentimentAgent", "coin": coin,
            "raw_data": data, "summary": response.content[0].text,
            "fear_greed": data["fear_greed_index"],
            "fear_greed_label": data["fear_greed_label"],
        }

    async def fear_greed_analysis(self) -> str:
        fg = random.randint(20, 80)
        labels = {
            range(0, 25): "Extreme Fear 😱", range(25, 45): "Fear 😨",
            range(45, 55): "Neutral 😐", range(55, 75): "Greed 🤑",
            range(75, 101): "Extreme Greed 🚀",
        }
        label = next(v for r, v in labels.items() if fg in r)
        bar = "█" * int(fg / 5) + "░" * (20 - int(fg / 5))
        response = self.client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system="Ты эксперт по криптовалютному рынку. Отвечай по-русски, кратко и по делу.",
            messages=[{"role": "user", "content": f"Fear & Greed Index: {fg}/100 ({label}). Объясни что это значит (3-4 предложения)."}],
        )
        return f"📊 *Fear & Greed Index*\n`[{bar}]`\n*{fg}/100 — {label}*\n\n{response.content[0].text}"


class OrchestratorAgent:
    SYSTEM_PROMPT = """Ты главный аналитик криптовалютного фонда.
Синтезируй данные от двух аналитиков и дай итоговую рекомендацию на русском.
Формат: 🔴/🟡/🟢 Сигнал, Уверенность %, Ключевые факторы, Риски."""

    def __init__(self, client: Anthropic):
        self.client = client

    async def synthesize(self, coin: str, price_data: dict, sentiment_data: dict) -> dict[str, Any]:
        user_message = f"""Монета: {coin}
=== PRICE AGENT ===
{price_data['summary']}
RSI: {price_data['rsi']} ({price_data['rsi_signal']}), Тренд: {price_data['trend']}
=== SENTIMENT AGENT ===
{sentiment_data['summary']}
Fear & Greed: {sentiment_data['fear_greed']}/100 ({sentiment_data['fear_greed_label']})
Дай итоговую рекомендацию."""
        response = self.client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return {"agent": "OrchestratorAgent", "coin": coin, "recommendation": response.content[0].text}

    async def market_overview(self, coins: list[str]) -> str:
        summaries = []
        for coin in coins:
            data = _fake_price_data(coin)
            emoji = "🟢" if data["change_24h"] > 0 else "🔴"
            summaries.append(f"{emoji} *{coin}*: ${data['price']:,} ({data['change_24h']:+.2f}%)")
        fg = random.randint(20, 80)
        response = self.client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system="Ты эксперт по криптовалютному рынку. Отвечай по-русски, кратко.",
            messages=[{"role": "user", "content": f"Данные рынка:\n{chr(10).join(summaries)}\nFear & Greed: {fg}/100\nДай краткий обзор рынка."}],
        )
        return f"{chr(10).join(summaries)}\n\n📋 *Анализ рынка:*\n{response.content[0].text}"
