from aiogram import Router, types, F
from aiogram.filters import Command
from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..models import User
from ..services.tinkoff_integration import sync_tinkoff_account, tinkoff_debug_text
from .start import main_menu_inline


router = Router()


@router.message(Command("sync_tinkoff"))
@router.message(F.text == "Синк Тинькофф")
async def sync_tinkoff(message: types.Message) -> None:
    tg_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
        if user is None:
            await message.answer("Сначала нажмите /start")
            return
        text = await sync_tinkoff_account(session, user)
    await message.answer(text, reply_markup=main_menu_inline())


@router.callback_query(F.data == "action:sync_tinkoff")
async def sync_tinkoff_cb(callback: types.CallbackQuery) -> None:
    message = callback.message
    tg_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
        if user is None:
            await message.answer("Сначала нажмите /start")
            return
        text = await sync_tinkoff_account(session, user)
    await message.edit_text(text, reply_markup=main_menu_inline())
    await callback.answer()


@router.callback_query(F.data == "action:tinkoff_debug")
async def tinkoff_debug_cb(callback: types.CallbackQuery) -> None:
    text = tinkoff_debug_text()
    await callback.message.edit_text(text, reply_markup=main_menu_inline())
    await callback.answer()

