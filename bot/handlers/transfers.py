from decimal import Decimal
from uuid import uuid4

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..models import User, Account, Transaction


router = Router()


class TransferState(StatesGroup):
    from_acc = State()
    to_acc = State()
    amount = State()
    fee = State()
    confirm = State()
    wizard_message_id = State()


def kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="tr:cancel")]])


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


def _accounts_kb(accounts: list[Account], prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=a.name, callback_data=f"{prefix}:{a.id}")] for a in accounts]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="tr:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "action:transfer")
async def start_transfer(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    await state.clear()
    await state.set_state(TransferState.from_acc)

    async with AsyncSessionLocal() as session:
        tg_id = callback.from_user.id
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
        accounts = (
            await session.execute(select(Account).where(Account.user_id == user.id))
        ).scalars().all()
        accounts = [a for a in accounts if not a.is_external_balance]
    await _edit(message, state, "Выберите счет ИЗ:", _accounts_kb(accounts, "tr:from"))
    await callback.answer()


@router.callback_query(TransferState.from_acc, F.data.startswith("tr:from:"))
async def set_from(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    from_id = int(callback.data.split(":")[-1])
    await state.update_data(from_id=from_id)

    async with AsyncSessionLocal() as session:
        tg_id = callback.from_user.id
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
        accounts = (
            await session.execute(select(Account).where(Account.user_id == user.id))
        ).scalars().all()
        accounts = [a for a in accounts if not a.is_external_balance and a.id != from_id]
    await state.set_state(TransferState.to_acc)
    await _edit(message, state, "Выберите счет В:", _accounts_kb(accounts, "tr:to"))
    await callback.answer()


@router.callback_query(TransferState.to_acc, F.data.startswith("tr:to:"))
async def set_to(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    to_id = int(callback.data.split(":")[-1])
    await state.update_data(to_id=to_id)
    await state.set_state(TransferState.amount)
    await _edit(message, state, "Введите сумму перевода:", kb_cancel())
    await callback.answer()


@router.message(TransferState.amount)
async def set_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = Decimal(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except Exception:
        await _edit(message, state, "Некорректная сумма. Повторите:", kb_cancel())
        return
    await state.update_data(amount=str(amount))
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(TransferState.fee)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="0 комиссия", callback_data="tr:fee:0")],
        [InlineKeyboardButton(text="Ввести комиссию", callback_data="tr:fee:custom")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="tr:cancel")],
    ])
    await _edit(message, state, "Комиссия?", kb)


@router.callback_query(TransferState.fee, F.data == "tr:fee:0")
async def fee_zero(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    await state.update_data(fee="0")
    await _show_confirm(message, state)
    await callback.answer()


@router.callback_query(TransferState.fee, F.data == "tr:fee:custom")
async def fee_custom(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    await _edit(message, state, "Введите комиссию:", kb_cancel())
    await callback.answer()


@router.message(TransferState.fee)
async def fee_amount(message: types.Message, state: FSMContext) -> None:
    try:
        fee = Decimal(message.text.replace(",", "."))
        if fee < 0:
            raise ValueError
    except Exception:
        await _edit(message, state, "Некорректная комиссия. Повторите:", kb_cancel())
        return
    await state.update_data(fee=str(fee))
    try:
        await message.delete()
    except Exception:
        pass
    await _show_confirm(message, state)


async def _show_confirm(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        from_acc = (await session.execute(select(Account).where(Account.id == int(data["from_id"])))).scalar_one()
        to_acc = (await session.execute(select(Account).where(Account.id == int(data["to_id"])))).scalar_one()
    amount = Decimal(data["amount"]).quantize(Decimal("0.01"))
    fee = Decimal(data.get("fee", "0")).quantize(Decimal("0.01"))
    text = (
        "Подтвердите перевод:\n\n"
        f"Из: {from_acc.name} ({from_acc.currency})\n"
        f"В:  {to_acc.name} ({to_acc.currency})\n"
        f"Сумма: {amount} {from_acc.currency}\n"
        f"Комиссия: {fee} {from_acc.currency}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✔️ Подтвердить", callback_data="tr:confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="tr:cancel")],
    ])
    await state.set_state(TransferState.confirm)
    await _edit(message, state, text, kb)


@router.callback_query(TransferState.confirm, F.data == "tr:confirm")
async def do_transfer(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    data = await state.get_data()
    from_id = int(data["from_id"]) 
    to_id = int(data["to_id"]) 
    amount = Decimal(data["amount"]) 
    fee = Decimal(data.get("fee", "0"))

    async with AsyncSessionLocal() as session:
        from_acc = (await session.execute(select(Account).where(Account.id == from_id))).scalar_one()
        to_acc = (await session.execute(select(Account).where(Account.id == to_id))).scalar_one()
        if from_acc.currency != to_acc.currency:
            await callback.answer("Пока без конвертации валют", show_alert=True)
            return
        # expense from source (amount + fee)
        session.add(Transaction(
            user_id=from_acc.user_id,
            account_id=from_acc.id,
            type="expense",
            amount=amount + fee,
            currency=from_acc.currency,
            category="Переводы",
            description=f"Перевод -> {to_acc.name}",
        ))
        # income to destination (amount)
        session.add(Transaction(
            user_id=to_acc.user_id,
            account_id=to_acc.id,
            type="income",
            amount=amount,
            currency=to_acc.currency,
            category="Переводы",
            description=f"Перевод <- {from_acc.name}",
        ))
        await session.commit()

    await state.clear()
    await message.edit_text("Перевод выполнен ✅")
    await callback.answer()


@router.callback_query(F.data == "tr:cancel")
async def cancel_transfer(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()
