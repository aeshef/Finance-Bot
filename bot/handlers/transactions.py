from decimal import Decimal
from typing import Optional

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func

from ..db import AsyncSessionLocal
from ..models import User, Account, Transaction
from ..services.categories import load_categories
from ..services.crypto_prices import fetch_prices_rub


router = Router()


class AddTxnState(StatesGroup):
    type = State()
    amount = State()
    category = State()
    account = State()
    wizard_message_id = State()


def inline_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="wizard:cancel")]]
    )


async def _set_wizard_message(state: FSMContext, message: types.Message) -> None:
    await state.update_data(wizard_message_id=message.message_id)


async def _edit_wizard(message: types.Message, state: FSMContext, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    data = await state.get_data()
    msg_id = data.get("wizard_message_id")
    if msg_id:
        try:
            await message.bot.edit_message_text(chat_id=message.chat.id, message_id=msg_id, text=text, reply_markup=kb)
            return
        except Exception:
            pass
    m = await message.answer(text, reply_markup=kb)
    await _set_wizard_message(state, m)


def _categories_keyboard(kind: str) -> InlineKeyboardMarkup:
    cats = load_categories(kind)
    rows = []
    row: list[InlineKeyboardButton] = []
    for c in cats:
        row.append(InlineKeyboardButton(text=c, callback_data=f"wizard:cat:{c}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="📝 Ввести текстом", callback_data="wizard:cat_text")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="action:menu"), InlineKeyboardButton(text="❌ Отмена", callback_data="wizard:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏠 Меню", callback_data="action:menu"),
            ],
        ]
    )


@router.callback_query(F.data == "action:add_expense")
async def add_expense_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    await state.set_state(AddTxnState.type)
    await state.update_data(type="expense")
    await state.set_state(AddTxnState.amount)
    await _edit_wizard(message, state, "Введите сумму расхода (например: 500.00):", inline_cancel_kb())
    await callback.answer()


@router.callback_query(F.data == "action:add_income")
async def add_income_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    await state.set_state(AddTxnState.type)
    await state.update_data(type="income")
    await state.set_state(AddTxnState.amount)
    await _edit_wizard(message, state, "Введите сумму дохода (например: 1500.00):", inline_cancel_kb())
    await callback.answer()


@router.message(AddTxnState.amount)
async def add_amount(message: types.Message, state: FSMContext) -> None:
    try:
        amount = Decimal(message.text.replace(",", "."))
    except Exception:
        await _edit_wizard(message, state, "Не удалось распознать сумму. Повторите, например: 500.00", inline_cancel_kb())
        return
    await state.update_data(amount=str(amount))
    # delete user message with raw amount to keep chat clean
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(AddTxnState.category)
    data = await state.get_data()
    kind = "income" if data.get("type") == "income" else "expense"
    await _edit_wizard(message, state, "Выберите категорию:", _categories_keyboard(kind))


@router.callback_query(AddTxnState.category, F.data.startswith("wizard:cat:"))
async def choose_category_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    cat = callback.data.split(":", 2)[-1]
    await state.update_data(category=cat)

    # offer accounts to choose
    async with AsyncSessionLocal() as session:
        tg_id = callback.from_user.id
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
        accounts = (
            await session.execute(select(Account).where(Account.user_id == user.id))
        ).scalars().all()
        accounts = [a for a in accounts if not a.is_external_balance]
        if not accounts:
            acc = Account(user_id=user.id, name="Кошелек", type="wallet", currency=user.base_currency)
            session.add(acc)
            await session.commit()
            accounts = [acc]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=a.name, callback_data=f"wizard:acc:{a.id}")]
            for a in accounts
        ]
        + [[InlineKeyboardButton(text="❌ Отмена", callback_data="wizard:cancel")]]
    )
    await state.set_state(AddTxnState.account)
    await _edit_wizard(message, state, "Выберите счет:", kb)
    await callback.answer()


@router.callback_query(AddTxnState.category, F.data == "wizard:cat_text")
async def choose_category_text_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    await _edit_wizard(message, state, "Введите категорию текстом:", inline_cancel_kb())
    await callback.answer()


@router.message(AddTxnState.category)
async def add_category(message: types.Message, state: FSMContext) -> None:
    await state.update_data(category=message.text.strip())
    # delete user message with raw category text
    try:
        await message.delete()
    except Exception:
        pass

    # offer accounts to choose
    async with AsyncSessionLocal() as session:
        tg_id = message.from_user.id
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
        accounts = (
            await session.execute(select(Account).where(Account.user_id == user.id))
        ).scalars().all()
        accounts = [a for a in accounts if not a.is_external_balance]
        if not accounts:
            acc = Account(user_id=user.id, name="Кошелек", type="wallet", currency=user.base_currency)
            session.add(acc)
            await session.commit()
            accounts = [acc]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=a.name, callback_data=f"wizard:acc:{a.id}")]
            for a in accounts
        ]
        + [[InlineKeyboardButton(text="❌ Отмена", callback_data="wizard:cancel")]]
    )
    await state.set_state(AddTxnState.account)
    await _edit_wizard(message, state, "Выберите счет:", kb)


@router.callback_query(AddTxnState.account, F.data.startswith("wizard:acc:"))
async def add_account_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    acc_id_str = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        tg_id = callback.from_user.id
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
        account = (
            await session.execute(select(Account).where(Account.user_id == user.id, Account.id == int(acc_id_str)))
        ).scalar_one_or_none()
        if account is None or account.is_external_balance:
            await callback.answer("Нельзя выбрать этот счет", show_alert=True)
            return

        data = await state.get_data()
        txn = Transaction(
            user_id=user.id,
            account_id=account.id,
            type=data["type"],
            amount=Decimal(data["amount"]),
            currency=account.currency,
            category=data.get("category"),
        )
        session.add(txn)
        await session.commit()

    await state.clear()
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=message.message_id,
        text="Записал транзакцию ✅",
        reply_markup=_main_menu_inline(),
    )
    await callback.answer()


@router.callback_query(F.data == "wizard:cancel")
async def wizard_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(F.data == "action:balance")
async def show_balance_cb(callback: types.CallbackQuery) -> None:
    message = callback.message
    tg_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
        if user is None:
            await message.answer("Сначала нажмите /start")
            return
        accounts = (await session.execute(select(Account).where(Account.user_id == user.id))).scalars().all()

        # compute balances per account
        async def acc_balance(acc: Account) -> Decimal:
            if acc.is_external_balance and acc.external_balance is not None:
                return Decimal(acc.external_balance)
            inc = (
                await session.execute(
                    select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                        Transaction.account_id == acc.id, Transaction.type == "income"
                    )
                )
            ).scalar_one()
            exp = (
                await session.execute(
                    select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                        Transaction.account_id == acc.id, Transaction.type == "expense"
                    )
                )
            ).scalar_one()
            return Decimal(inc) - Decimal(exp)

        groups = {
            "cards": [],
            "invest": [],
            "crypto": [],
            "debts": [],
            "cash": [],
        }
        # Pre-fetch crypto prices
        crypto_symbols: set[str] = set()
        for a in accounts:
            if a.type == "crypto":
                crypto_symbols.add(a.currency.upper())
        prices_rub = {}
        if crypto_symbols:
            try:
                prices_rub = await fetch_prices_rub(sorted(list(crypto_symbols)))
            except Exception:
                prices_rub = {}

        for acc in accounts:
            bal = await acc_balance(acc)
            entry = (acc, bal)
            if acc.type in ("card",) or (acc.type == "wallet" and "нал" not in acc.name.lower()):
                if bal != 0:
                    groups["cards"].append(entry)
            elif acc.type == "wallet":
                if bal != 0:
                    groups["cash"].append(entry)
            elif acc.type.startswith("broker"):
                if bal != 0:
                    groups["invest"].append(entry)
            elif acc.type == "crypto":
                # show crypto even if zero
                groups["crypto"].append(entry)
            elif acc.type in ("receivable", "liability_payable"):
                if bal != 0:
                    groups["debts"].append(entry)
            else:
                if bal != 0:
                    groups["cards"].append(entry)

        def fmt_line(acc: Account, amount: Decimal) -> str:
            label_raw = acc.name
            if acc.type == "receivable":
                label_raw = f"{acc.name.split(':',1)[-1]} (мне должны)"
            elif acc.type == "liability_payable":
                label_raw = f"{acc.name.split(':',1)[-1]} (я должен)"
            label = label_raw[:24]
            if acc.type == "crypto":
                sym = acc.currency.upper()
                amt_crypto = f"{amount:.8f} {sym}"
                rub_val = None
                if sym in prices_rub:
                    rub_val = float(amount) * float(prices_rub[sym])
                rub_str = f" (~{rub_val:.2f} RUB)" if rub_val is not None else ""
                return f"{label:<24} {amt_crypto}{rub_str}"
            amt = f"{amount:.2f}" if acc.currency in ("RUB", "RUR", "USD", "EUR") else f"{amount:.6f}"
            return f"{label:<24} {amt:>14} {acc.currency}"

        sections: list[str] = []
        def add_section(title: str, items: list[tuple[Account, Decimal]]):
            if not items:
                return
            sections.append(title)
            for acc, bal in items:
                sections.append(fmt_line(acc, bal))
            sections.append("")

        sections.append("📊 Баланс")
        sections.append("<pre>")
        add_section("💳 Карты", groups["cards"])
        add_section("💵 Наличные", groups["cash"])
        add_section("📈 Инвестиции", groups["invest"])
        add_section("🪙 Крипто", groups["crypto"])
        add_section("🏦 Долги", groups["debts"])
        if sections and sections[-1] == "":
            sections.pop()
        sections.append("</pre>")

        await message.edit_text("\n".join(sections), reply_markup=_main_menu_inline())
    await callback.answer()

