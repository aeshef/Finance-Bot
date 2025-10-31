from __future__ import annotations

from pathlib import Path
from typing import List
import yaml


ROOT = Path(__file__).resolve().parents[2]
EXPENSE_FILE = ROOT / "config" / "categories_mvp.yaml"
INCOME_FILE = ROOT / "config" / "income_categories.yaml"


def load_categories(kind: str = "expense") -> List[str]:
    file_path = INCOME_FILE if kind == "income" else EXPENSE_FILE
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        pass
    # Fallbacks
    if kind == "income":
        return ["Зарплата", "Подарок", "Прочее"]
    return [
        "Еда/Продукты",
        "Еда/Вне дома",
        "Транспорт/Такси",
        "Прочее",
    ]
