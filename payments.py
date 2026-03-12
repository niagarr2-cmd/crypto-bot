"""
Crypto AI Agents — Модуль платежей.

Поддерживает: Telegram Stars, ЮКасса, TON.
Тарифы: Free / Premium (199₽/мес) / VIP (499₽/мес)
"""

import os
import logging
import json
from datetime import datetime, date, timedelta
from enum import Enum

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Конфигурация платежей
# ─────────────────────────────────────────────

YUKASSA_TOKEN = os.getenv("YUKASSA_PAYMENT_TOKEN", "")
TON_WALLET = os.getenv("TON_WALLET_ADDRESS", "")

# Telegram Stars: 1 Star ≈ 0.013$ / ~1.2₽
PREMIUM_STARS = int(os.getenv("PREMIUM_STARS", "150"))   # ~199₽
VIP_STARS = int(os.getenv("VIP_STARS", "400"))            # ~499₽

# Цены в рублях (для ЮКасса, в копейках)
PREMIUM_RUB = 19900   # 199₽
VIP_RUB = 49900       # 499₽

# TON цены
PREMIUM_TON = float(os.getenv("PREMIUM_TON", "2.5"))
VIP_TON = float(os.getenv("VIP_TON", "6.0"))


# ─────────────────────────────────────────────
# Тарифы
# ─────────────────────────────────────────────

class Plan(str, Enum):
    FREE = "free"
    PREMIUM = "premium"
    VIP = "vip"


PLAN_CONFIG = {
    Plan.FREE: {
        "name": "🆓 Free",
        "analyses_per_day": 1,
        "trading_signals": False,
        "global_market": True,
        "trending": True,
        "priority": False,
        "price_rub": 0,
        "price_stars": 0,
        "price_ton": 0.0,
    },
    Plan.PREMIUM: {
        "name": "⭐️ Premium",
        "analyses_per_day": 999,
        "trading_signals": True,
        "global_market": True,
        "trending": True,
        "priority": False,
        "price_rub": 199,
        "price_stars": PREMIUM_STARS,
        "price_ton": PREMIUM_TON,
        "duration_days": 30,
    },
    Plan.VIP: {
        "name": "💎 VIP",
        "analyses_per_day": 999,
        "trading_signals": True,
        "global_market": True,
        "trending": True,
        "priority": True,
        "price_rub": 499,
        "price_stars": VIP_STARS,
        "price_ton": VIP_TON,
        "duration_days": 30,
    },
}


# ─────────────────────────────────────────────
# Хранилище подписок (in-memory, для prod — заменить на БД)
# ─────────────────────────────────────────────

# user_id -> {"plan": Plan, "expires": datetime | None}
user_subscriptions: dict[int, dict] = {}


def get_user_plan(user_id: int) -> Plan:
    """Получить текущий тариф пользователя."""
    sub = user_subscriptions.get(user_id)
    if not sub:
        return Plan.FREE

    if sub["expires"] and datetime.now() > sub["expires"]:
        user_subscriptions[user_id]["plan"] = Plan.FREE
        user_subscriptions[user_id]["expires"] = None
        return Plan.FREE

    return sub["plan"]


def get_subscription_info(user_id: int) -> dict:
    """Полная информация о подписке."""
    plan = get_user_plan(user_id)
    sub = user_subscriptions.get(user_id, {})
    expires = sub.get("expires")
    config = PLAN_CONFIG[plan]

    days_left = None
    if expires:
        delta = expires - datetime.now()
        days_left = max(0, delta.days)

    return {
        "plan": plan,
        "config": config,
        "expires": expires,
        "days_left": days_left,
    }


def activate_subscription(user_id: int, plan: Plan):
    """Активировать подписку пользователю."""
    duration = PLAN_CONFIG[plan].get("duration_days", 30)
    expires = datetime.now() + timedelta(days=duration)
    user_subscriptions[user_id] = {
        "plan": plan,
        "expires": expires,
        "activated_at": datetime.now(),
    }
    logger.info(f"Subscription activated: user={user_id}, plan={plan}, expires={expires}")


def can_use_analysis(user_id: int, daily_usage: dict) -> tuple[bool, str]:
    """Проверить может ли пользователь сделать анализ."""
    plan = get_user_plan(user_id)
    config = PLAN_CONFIG[plan]
    limit = config["analyses_per_day"]

    if limit >= 999:
        return True, ""

    today = date.today()
    if user_id in daily_usage:
        saved_date, count = daily_usage[user_id]
        if saved_date == today and count >= limit:
            return False, plan.value

    return True, ""


def can_use_trading(user_id: int) -> bool:
    """Проверить доступ к Trading сигналам."""
    plan = get_user_plan(user_id)
    return PLAN_CONFIG[plan]["trading_signals"]


def get_stats() -> dict:
    """Статистика подписок."""
    now = datetime.now()
    active = {
        Plan.FREE: 0,
        Plan.PREMIUM: 0,
        Plan.VIP: 0,
    }
    for uid, sub in user_subscriptions.items():
        plan = sub["plan"]
        expires = sub.get("expires")
        if plan == Plan.FREE or (expires and expires > now):
            active[plan] += 1

    return active
