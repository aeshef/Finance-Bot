from __future__ import annotations

from pathlib import Path
from typing import Iterable
import yaml

from .cashback_models import CashbackRulesFile, CashbackRule


def load_cashback_rules(file_path: Path) -> CashbackRulesFile:
    data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    return CashbackRulesFile.model_validate(data)


def iter_rules(files: Iterable[Path]) -> list[CashbackRule]:
    collected: list[CashbackRule] = []
    for fp in files:
        try:
            cf = load_cashback_rules(fp)
            collected.extend(cf.rules)
        except Exception:
            continue
    # sort by priority
    collected.sort(key=lambda r: r.priority)
    return collected

