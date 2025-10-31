from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from .cashback_models import CashbackRule


@dataclass
class TxnContext:
    amount: float
    currency: str
    occurred_on: date
    category: Optional[str] = None
    merchant: Optional[str] = None
    mcc: Optional[int] = None


@dataclass
class CashbackEstimate:
    account: str
    rule_id: Optional[str]
    rule_title: Optional[str]
    estimated_amount: float
    reason: str


def _match_rule(rule: CashbackRule, ctx: TxnContext, account_name: str) -> bool:
    if account_name not in rule.applies_to.accounts:
        return False
    if not (rule.validity.start <= ctx.occurred_on <= rule.validity.end):
        return False
    cond = rule.conditions
    if cond.categories and (ctx.category is None or ctx.category not in cond.categories):
        return False
    if cond.merchants and (ctx.merchant is None or not any(m.lower() in ctx.merchant.lower() for m in cond.merchants)):
        return False
    if cond.mcc and (ctx.mcc is None or ctx.mcc not in cond.mcc):
        return False
    return True


def _calc_estimate(rule: CashbackRule, ctx: TxnContext) -> float:
    if rule.reward.kind == "percent":
        cash = ctx.amount * (rule.reward.value / 100.0)
    else:
        cash = rule.reward.value
    if rule.reward.cap is not None:
        cash = min(cash, rule.reward.cap.amount)
    return round(cash, 2)


def suggest_best_account(ctx: TxnContext, rules: Iterable[CashbackRule], candidate_accounts: Iterable[str]) -> Optional[CashbackEstimate]:
    best: Optional[CashbackEstimate] = None
    for acc in candidate_accounts:
        for rule in rules:
            if not _match_rule(rule, ctx, acc):
                continue
            est = _calc_estimate(rule, ctx)
            reason = f"{rule.reward.kind} {rule.reward.value} with cap {rule.reward.cap.amount if rule.reward.cap else '∞'}"
            cur = CashbackEstimate(account=acc, rule_id=rule.id, rule_title=rule.title, estimated_amount=est, reason=reason)
            if best is None or cur.estimated_amount > best.estimated_amount:
                best = cur
    return best

