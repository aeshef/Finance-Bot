from decimal import Decimal

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..models import User, Account


router = Router()


class DebtState(StatesGroup):
    mode = State()  # receivable|payable|settle_recv|settle_pay
    name = State()
    amount = State()
    select_acc = State()
    wizard_message_id = State()


def kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="debt:cancel"), InlineKeyboardButton(text="🏠 Меню", callback_data="action:menu")]])


async def _set_msg(state: FSMContext, message: types.Message) -> None:
    await state.update_data(wizard_message_id=message.message_id)


async def _edit(message: types.Message, state: FSMContext, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    data = await state.get_data()
    mid = data.get("wizard_message_id")
    if mid:
        try:
            await message.bot.edit_message_text(chat_id=message.chat.id, message_id=mid, text=text, reply_markup=kb)
            return
        except Exception:
            pass
    m = await message.answer(text, reply_markup=kb)
    await _set_msg(state, m)


def _menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Мне должны", callback_data="debt:recv")],
        [InlineKeyboardButton(text="➖ Я должен", callback_data="debt:pay")],
        [InlineKeyboardButton(text="✅ Погасили мне", callback_data="debt:settle_recv")],
        [InlineKeyboardButton(text="✅ Погасил я", callback_data="debt:settle_pay")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="action:menu"), InlineKeyboardButton(text="❌ Отмена", callback_data="debt:cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "action:debts")
async def debts_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback.message, state, "Долги: выберите действие", _menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("debt:"))
async def debts_route(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = callback.data.split(":")[1]
    message = callback.message
    if data in ("recv", "pay", "settle_recv", "settle_pay"):
        await state.update_data(mode=data)
        # Offer existing counterparties
        async with AsyncSessionLocal() as session:
            tg_id = callback.from_user.id
            user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
            kind_type = "receivable" if data in ("recv", "settle_recv") else "liability_payable"
            accs = (
                await session.execute(
                    select(Account).where(Account.user_id == user.id, Account.type == kind_type)
                )
            ).scalars().all()
        rows = [[InlineKeyboardButton(text=a.name.split(":",1)[1] if ":" in a.name else a.name, callback_data=f"debt:cp:{a.name}")]
                for a in accs]
        rows.append([InlineKeyboardButton(text="➕ Новый контрагент", callback_data="debt:new")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="action:debts"), InlineKeyboardButton(text="🏠 Меню", callback_data="action:menu")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await state.set_state(DebtState.name)
        await _edit(message, state, "Выберите контрагента или создайте нового:", kb)
        await callback.answer()
        return
    await callback.answer()


@router.message(DebtState.name)
async def debt_set_name(message: types.Message, state: FSMContext) -> None:
    name = message.text.strip()
    await state.update_data(counterparty=name)
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(DebtState.amount)
    await _edit(message, state, "Введите сумму:", kb_cancel())


@router.callback_query(DebtState.name, F.data.startswith("debt:cp:"))
async def debt_choose_existing(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    cp = callback.data.split(":", 2)[-1]
    cp_short = cp.split(":", 1)[-1] if ":" in cp else cp
    await state.update_data(counterparty=cp_short)
    await state.set_state(DebtState.amount)
    await _edit(message, state, f"{cp_short}\nВведите сумму:", kb_cancel())
    await callback.answer()


@router.callback_query(DebtState.name, F.data == "debt:new")
async def debt_new_cp(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    await _edit(message, state, "Введите имя контрагента:", kb_cancel())
    await callback.answer()


async def _upsert_debt_account(session, user_id: int, mode: str, name: str, currency: str) -> Account:
    acc_type = "receivable" if mode in ("recv", "settle_recv") else "liability_payable"
    acc_name = f"{acc_type}:{name}"
    acc = (
        await session.execute(select(Account).where(Account.user_id == user_id, Account.name == acc_name))
    ).scalar_one_or_none()
    if acc is None:
        acc = Account(
            user_id=user_id,
            name=acc_name,
            type=acc_type,
            currency=currency,
            is_external_balance=True,
            external_balance=Decimal("0"),
        )
        session.add(acc)
        await session.flush()
    return acc


@router.message(DebtState.amount)
async def debt_set_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = Decimal(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except Exception:
        await _edit(message, state, "Некорректная сумма. Повторите:", kb_cancel())
        return

    data = await state.get_data()
    mode = data.get("mode")

    async with AsyncSessionLocal() as session:
        tg_id = message.from_user.id
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
        # default currency: base of first internal account or RUB
        first_acc = (
            await session.execute(
                select(Account)
                .where(Account.user_id == user.id, Account.is_external_balance == False)
                .order_by(Account.id.asc())
                .limit(1)
            )
        ).scalars().first()
        currency = first_acc.currency if first_acc else "RUB"

        acc = await _upsert_debt_account(session, user.id, mode, data.get("counterparty"), currency)
        cur = Decimal(acc.external_balance or 0)
        if mode == "recv":
            acc.external_balance = cur + amount
            msg = f"Записал: мне должны {acc.name.split(':',1)[1]} +{amount} {currency}"
        elif mode == "pay":
            acc.external_balance = cur + amount
            msg = f"Записал: я должен {acc.name.split(':',1)[1]} +{amount} {currency}"
        elif mode == "settle_recv":
            acc.external_balance = max(Decimal("0"), cur - amount)
            msg = f"Погашено: мне должны от {acc.name.split(':',1)[1]} -{amount} {currency}"
        else:  # settle_pay
            acc.external_balance = max(Decimal("0"), cur - amount)
            msg = f"Погашено: мой долг {acc.name.split(':',1)[1]} -{amount} {currency}"
        await session.commit()

    await state.clear()
    await message.answer(msg)


@router.callback_query(F.data == "debt:cancel")
async def debt_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()
