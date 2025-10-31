import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties

from sqlalchemy.ext.asyncio import AsyncEngine

from .config import get_settings
from .db import Base, _engine
from .handlers.start import router as start_router
from .handlers.transactions import router as transactions_router
from .handlers.integrations import router as integrations_router
from .handlers.transfers import router as transfers_router
from .handlers.debts import router as debts_router
from .handlers.investments import router as investments_router
from .scheduler import start_scheduler


async def on_startup(bot: Bot, engine: AsyncEngine) -> None:
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Set default commands
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="sync_tinkoff", description="Синхронизировать Тинькофф"),
        ]
    )
    # Start reminders scheduler
    start_scheduler(bot)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def main() -> None:
    setup_logging()
    settings = get_settings()

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    # Routers
    dp.include_router(start_router)
    dp.include_router(transactions_router)
    dp.include_router(transfers_router)
    dp.include_router(debts_router)
    dp.include_router(investments_router)
    dp.include_router(integrations_router)

    await on_startup(bot, _engine)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
