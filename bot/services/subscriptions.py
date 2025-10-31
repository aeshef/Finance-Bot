from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List
import yaml

from ..config import get_settings


CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "subscriptions.yaml"


@dataclass
class Subscription:
    name: str
    amount: float
    currency: str
    period: str  # monthly|yearly
    next_charge: date


def load_subscriptions() -> List[Subscription]:
    try:
        raw = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    items: List[Subscription] = []
    for r in raw or []:
        try:
            items.append(
                Subscription(
                    name=str(r.get("name")),
                    amount=float(r.get("amount")),
                    currency=str(r.get("currency", "RUB")),
                    period=str(r.get("period", "monthly")),
                    next_charge=datetime.fromisoformat(str(r.get("next_charge"))).date(),
                )
            )
        except Exception:
            continue
    return items


def format_subscription_line(s: Subscription) -> str:
    return f"{s.name}: {s.amount:.2f} {s.currency} — {s.next_charge.isoformat()} ({s.period})"


def is_due_within(s: Subscription, days: int) -> bool:
    today = datetime.now().date()
    return today <= s.next_charge <= (today + timedelta(days=days))
