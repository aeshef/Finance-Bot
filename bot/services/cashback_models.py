from __future__ import annotations

from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field


class Validity(BaseModel):
    start: date
    end: date


class Cap(BaseModel):
    period: str = Field(default="monthly")  # monthly | weekly | total
    amount: float
    currency: str = Field(default="RUB")


class Reward(BaseModel):
    kind: str = Field(default="percent")  # percent | fixed
    value: float  # percent e.g. 5.0 or fixed amount in currency
    cap: Optional[Cap] = None


class Conditions(BaseModel):
    categories: List[str] = Field(default_factory=list)  # our internal categories
    mcc: List[int] = Field(default_factory=list)
    merchants: List[str] = Field(default_factory=list)  # substrings / normalized names
    tags: List[str] = Field(default_factory=list)  # arbitrary tags


class AppliesTo(BaseModel):
    accounts: List[str] = Field(default_factory=list)  # account names (cards) in our system


class CashbackRule(BaseModel):
    id: str
    title: str
    validity: Validity
    reward: Reward
    conditions: Conditions
    applies_to: AppliesTo
    priority: int = 100  # lower means higher priority
    stackable: bool = False  # can be combined with other rules
    notes: Optional[str] = None


class CashbackRulesFile(BaseModel):
    month: str  # YYYY-MM
    rules: List[CashbackRule]

