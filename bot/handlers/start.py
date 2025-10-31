from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..models import User, Account


router = Router()


def main_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➖ Добавить расход", callback_data="action:add_expense"),
                InlineKeyboardButton(text="➕ Добавить доход", callback_data="action:add_income"),
            ],
            [
                InlineKeyboardButton(text="↔️ Перевод", callback_data="action:transfer"),
                InlineKeyboardButton(text="💼 Долги", callback_data="action:debts"),
            ],
            [
                InlineKeyboardButton(text="📈 Инвестиции", callback_data="action:invest"),
            ],
            [
                InlineKeyboardButton(text="📊 Баланс", callback_data="action:balance"),
            ],
        ]
    )


@router.callback_query(F.data == "action:menu")
async def back_to_menu(callback: types.CallbackQuery) -> None:
    try:
        await callback.message.edit_text(
            "Привет! 👋 Я помогу вести ваши финансы. Выберите действие:",
            reply_markup=main_menu_inline(),
        )
    except Exception:
        pass
    await callback.answer()


@router.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    tg_id = message.from_user.id
    chat_id = message.chat.id

    async with AsyncSessionLocal() as session:
        # get or create user
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=tg_id, chat_id=chat_id)
            session.add(user)
            await session.flush()
        else:
            user.chat_id = chat_id

        # ensure at least one default account exists (check existence only)
        exists_row = (
            await session.execute(
                select(Account.id).where(Account.user_id == user.id).limit(1)
            )
        ).first()
        if exists_row is None:
            default_acc = Account(user_id=user.id, name="Кошелек", type="wallet", currency=user.base_currency)
            session.add(default_acc)

        await session.commit()

    await message.answer(
        "Привет! 👋 Я помогу вести ваши финансы. Выберите действие:",
        reply_markup=main_menu_inline(),
    )

