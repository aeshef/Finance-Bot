from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User, Account
from ..config import get_settings


def _fetch_tinkoff_summary(token: str) -> dict:
    try:
        from tinkoff_sync import fetch_tinkoff_summary  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Cannot import tinkoff_sync: {e}")
    return fetch_tinkoff_summary(token)


async def _upsert_external_account(session: AsyncSession, user_id: int, name: str, balance_rub: Decimal) -> Account:
    acc = (
        await session.execute(select(Account).where(Account.user_id == user_id, Account.name == name))
    ).scalar_one_or_none()
    if acc is None:
        acc = Account(
            user_id=user_id,
            name=name,
            type="broker_portfolio",
            currency="RUB",
            is_external_balance=True,
            external_balance=balance_rub,
        )
        session.add(acc)
    else:
        acc.is_external_balance = True
        acc.external_balance = balance_rub
    return acc


def _map_account_name(a) -> str:
    t = getattr(a, "type", None)
    t_name = None
    try:
        t_name = t.name if t is not None else None
    except Exception:
        t_name = None
    acc_id = getattr(a, "id", "")
    tail = acc_id[-4:] if isinstance(acc_id, str) and len(acc_id) >= 4 else acc_id
    if t_name == "ACCOUNT_TYPE_TINKOFF_IIS" or t_name == "ACCOUNT_TYPE_INVEST_BOX":
        return "Тинькофф ИИС"
    # ensure uniqueness for regular broker by suffixing tail
    return f"Тинькофф Брокер {tail}" if tail else "Тинькофф Брокер"


async def _sync_via_sdk(session: AsyncSession, user: User, token: str) -> str:
    try:
        from tinkoff.invest import Client
    except Exception as e:
        raise RuntimeError(f"SDK not available: {e}")

    lines: list[str] = []
    total = Decimal("0")
    settings = get_settings()
    ignore_ids: set[str] = set()
    if settings.TINKOFF_IGNORE_ACCOUNT_IDS:
        ignore_ids = set(x.strip() for x in settings.TINKOFF_IGNORE_ACCOUNT_IDS.split(",") if x.strip())
    with Client(token) as client:
        accs = client.users.get_accounts().accounts
        for a in accs:
            try:
                if getattr(a, "id", "") in ignore_ids:
                    continue
                p = client.operations.get_portfolio(account_id=a.id)
                q = p.total_amount_portfolio
                value = Decimal(str((q.units or 0))) + (Decimal(str(q.nano or 0)) / Decimal("1000000000"))
                name = _map_account_name(a)
                # Skip zero portfolios to avoid clutter/accidental empty accounts
                if value != 0:
                    await _upsert_external_account(session, user.id, name, value)
                    lines.append(f"{name:<24} {value:>14} RUB")
                    total += value
            except Exception as e:
                lines.append(f"{getattr(a, 'id', 'acc')}: error {e}")
        await session.commit()
    body = "\n".join(lines)
    return f"Синк по SDK\n<pre>\n{body}\n\nИтого: {total} RUB\n</pre>" if lines else "Нет счетов в SDK"


async def sync_tinkoff_account(session: AsyncSession, user: User) -> str:
    settings = get_settings()
    if not settings.TINKOFF_API_TOKEN:
        return "TINKOFF_API_TOKEN не задан в .env"

    # Prefer SDK if installed
    try:
        text = await _sync_via_sdk(session, user, settings.TINKOFF_API_TOKEN)
        return text
    except Exception:
        pass

    data = _fetch_tinkoff_summary(settings.TINKOFF_API_TOKEN)
    total_rub = Decimal(str(data.get("total_rub", 0)))
    acc = (
        await session.execute(
            select(Account).where(Account.user_id == user.id, Account.name == "Тинькофф Брокер")
        )
    ).scalar_one_or_none()
    if acc is None:
        acc = Account(
            user_id=user.id,
            name="Тинькофф Брокер",
            type="broker_portfolio",
            currency="RUB",
            is_external_balance=True,
            external_balance=total_rub,
        )
        session.add(acc)
    else:
        acc.is_external_balance = True
        acc.external_balance = total_rub

    await session.commit()
    return f"Тинькофф синхронизирован: {total_rub} RUB"


def tinkoff_debug_text() -> str:
    settings = get_settings()
    if not settings.TINKOFF_API_TOKEN:
        return "Нет токена TINKOFF_API_TOKEN"
    try:
        data = _fetch_tinkoff_summary(settings.TINKOFF_API_TOKEN)
    except Exception as e:
        return f"Ошибка импорта: {e}"
    dbg = data.get("_debug", {})
    lines = ["<pre>"]
    for k, v in dbg.items():
        val = str(v)
        if len(val) > 300:
            val = val[:300] + "…"
        lines.append(f"{k}: {val}")
    lines.append("</pre>")
    return "\n".join(lines)

