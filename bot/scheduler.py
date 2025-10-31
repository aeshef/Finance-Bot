from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

from .services.subscriptions import load_subscriptions, format_subscription_line, is_due_within
from .db import AsyncSessionLocal
from .models import User


scheduler: AsyncIOScheduler | None = None


async def send_subscriptions_digest(bot) -> None:
    subs = load_subscriptions()
    due = [s for s in subs if is_due_within(s, 3)]
    if not due:
        return
    text_lines = ["🔔 Ближайшие списания (≤ 3 дня):", ""] + [f"• {format_subscription_line(s)}" for s in due]
    text = "\n".join(text_lines)

    async with AsyncSessionLocal() as session:
        users = (await session.execute(User.__table__.select())).all()
        # fallback: broadcast to all users with chat_id
        for row in users:
            u = row[0]
            if u.chat_id:
                try:
                    await bot.send_message(chat_id=u.chat_id, text=text)
                except Exception:
                    pass


def start_scheduler(bot) -> None:
    global scheduler
    scheduler = AsyncIOScheduler()
    # every day at 10:00 local time
    scheduler.add_job(send_subscriptions_digest, CronTrigger(hour=10, minute=0), args=[bot])
    scheduler.start()
