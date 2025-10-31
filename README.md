## Personal Finance Telegram Bot

Minimal scaffold to track multi-account balances, manual expenses/incomes, with Tinkoff integration hook.

### Setup

1. Create an `.env` file in the project root with:

```
TELEGRAM_BOT_TOKEN=<from BotFather>
TIMEZONE=Europe/Moscow
BASE_CURRENCY=RUB
TINKOFF_API_TOKEN=<optional>
DATABASE_URL=sqlite+aiosqlite:///./finance.db
```

2. Install dependencies:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

3. Run the bot:

```bash
python -m bot.main
```

### Features (initial)
- /start sets up your profile and shows quick actions
- Quick add expense/income via guided prompts
- SQLite database with async SQLAlchemy
- Placeholder services: currency, cashback, Tinkoff sync

### Notes
- Database at `finance.db` unless `DATABASE_URL` is overridden
- Multi-currency supported at data level; conversions require rates sync (service stub)

