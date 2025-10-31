from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from ..services.tinkoff_integration import sync_tinkoff_account
from ..db import AsyncSessionLocal
from sqlalchemy import select
from ..models import User, Account, Transaction
from decimal import Decimal

router = Router()


def invest_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Синк портфеля", callback_data="invest:sync")],
            [InlineKeyboardButton(text="➕ Пополнить брокер", callback_data="invest:topup")],
            [InlineKeyboardButton(text="📋 Подробности", callback_data="invest:details")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="action:menu")],
        ]
    )


@router.callback_query(F.data == "action:invest")
async def invest_menu(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text("Инвестиции:", reply_markup=invest_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "invest:sync")
async def invest_sync(callback: types.CallbackQuery) -> None:
    tg_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
        if user is None:
            await callback.message.answer("Сначала нажмите /start")
            return
        text = await sync_tinkoff_account(session, user)
    await callback.message.edit_text(text, reply_markup=invest_menu_kb())
    await callback.answer()


class TopUpState(StatesGroup):
    from_acc = State()
    amount = State()
    msg_id = State()


def _kb_cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="action:invest"), InlineKeyboardButton(text="🏠 Меню", callback_data="action:menu")]]
    )


@router.callback_query(F.data == "invest:topup")
async def topup_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    async with AsyncSessionLocal() as session:
        tg_id = callback.from_user.id
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
        cards = (
            await session.execute(
                select(Account).where(
                    Account.user_id == user.id,
                    Account.is_external_balance == False,
                    Account.type.in_(["card", "wallet"]),
                )
            )
        ).scalars().all()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=a.name, callback_data=f"topup:from:{a.id}")] for a in cards] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="action:invest")]])
    m = await message.edit_text("Выберите карту для списания:", reply_markup=kb)
    await state.update_data(msg_id=m.message_id)
    await state.set_state(TopUpState.from_acc)
    await callback.answer()


@router.callback_query(TopUpState.from_acc, F.data.startswith("topup:from:"))
async def topup_from(callback: types.CallbackQuery, state: FSMContext) -> None:
    from_id = int(callback.data.split(":")[-1])
    await state.update_data(from_id=from_id)
    await state.set_state(TopUpState.amount)
    await callback.message.edit_text("Введите сумму пополнения:", reply_markup=_kb_cancel_menu())
    await callback.answer()


@router.message(TopUpState.amount)
async def topup_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amt = Decimal(message.text.replace(",", "."))
        if amt <= 0:
            raise ValueError
    except Exception:
        await message.answer("Некорректная сумма. Повторите: ", reply_markup=_kb_cancel_menu())
        return
    data = await state.get_data()
    from_id = int(data.get("from_id"))
    # delete user amount message to keep chat clean
    try:
        await message.delete()
    except Exception:
        pass
    # Record ONLY expense from card; portfolio подтянется по API отдельно
    async with AsyncSessionLocal() as session:
        from_acc = (await session.execute(select(Account).where(Account.id == from_id))).scalar_one()
        session.add(Transaction(
            user_id=from_acc.user_id,
            account_id=from_acc.id,
            type="expense",
            amount=amt,
            currency=from_acc.currency,
            category="Пополнение брокера",
        ))
        await session.commit()
    await state.clear()
    await message.answer("Пополнение брокера записано ✅", reply_markup=invest_menu_kb())


## Removed old broker_cash flow; now top-up only records expense from card


def _positions_menu_kb(accs: list[tuple[str,str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=name, callback_data=f"invest:acc:{acc_id}")] for name, acc_id in accs]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="action:invest"), InlineKeyboardButton(text="🏠 Меню", callback_data="action:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "invest:details")
async def invest_details(callback: types.CallbackQuery) -> None:
    try:
        from tinkoff.invest import Client
    except Exception:
        await callback.message.edit_text("SDK не установлен. Установите: pip install tinkoff-investments", reply_markup=invest_menu_kb())
        await callback.answer()
        return
    from ..config import get_settings
    token = get_settings().TINKOFF_API_TOKEN
    accs_info: list[tuple[str,str]] = []
    try:
        with Client(token) as client:
            accs = client.users.get_accounts().accounts
            from ..services.tinkoff_integration import _map_account_name  # reuse naming
            for a in accs:
                name = _map_account_name(a)
                accs_info.append((name, a.id))
    except Exception as e:
        await callback.message.edit_text(f"Ошибка SDK: {e}", reply_markup=invest_menu_kb())
        await callback.answer()
        return
    await callback.message.edit_text("Выберите счёт:", reply_markup=_positions_menu_kb(accs_info))
    await callback.answer()


@router.callback_query(F.data.startswith("invest:acc:"))
async def invest_show_positions(callback: types.CallbackQuery) -> None:
    acc_id = callback.data.split(":")[-1]
    try:
        from tinkoff.invest import Client
    except Exception:
        await callback.message.edit_text("SDK не установлен. Установите: pip install tinkoff-investments", reply_markup=invest_menu_kb())
        await callback.answer()
        return
    from ..config import get_settings
    token = get_settings().TINKOFF_API_TOKEN
    lines = ["<pre>"]
    try:
        with Client(token) as client:
            p = client.operations.get_portfolio(account_id=acc_id)
            total = p.total_amount_portfolio
            tot_val = (total.units or 0) + (total.nano or 0)/1_000_000_000
            lines.append(f"Итого: {tot_val} RUB\n")
            for pos in p.positions:
                name = pos.instrument_type or "instrument"
                qty = (pos.quantity.units or 0) + (pos.quantity.nano or 0)/1_000_000_000
                valq = pos.current_price
                val = (valq.units or 0) + (valq.nano or 0)/1_000_000_000
                lines.append(f"{name:<16} {qty:>10.6f} @ {val:>10.2f}")
    except Exception as e:
        lines = [f"Ошибка SDK: {e}"]
    text = "\n".join(lines + ["</pre>"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="invest:details")], [InlineKeyboardButton(text="🏠 Меню", callback_data="action:menu")]])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()
