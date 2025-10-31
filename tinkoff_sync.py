#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import urllib.request
import httpx
from typing import Any, Dict, Optional, List
from datetime import datetime, timezone
import time
from pathlib import Path


VAULT = Path("/Users/aeshef/Documents/Obsidian Vault")
ENV_PATH = VAULT / "800_Автоматизация" / ".env"
CACHE_NOTE = VAULT / "700_База_Данных" / "Финансы" / "Мета" / "Портфель_Кэш.md"
LOG_FILE = VAULT / "700_База_Данных" / "Финансы" / "Мета" / "Портфель_Лог.txt"


def dbg(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass
    print(f"[tinkoff_sync] {msg}", file=sys.stderr, flush=True)


def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def fetch_tinkoff_summary(token: str) -> dict:
    # Use user's integration TinkoffClient API
    total_rub = 0.0
    day_change_rub = 0.0  # optional, keep 0 for now
    by_sector = {}
    dbginfo: Dict[str, Any] = {
        "imported_client": False,
        "accounts_v2": [],
        "main_account": None,
        "accounts_rest": [],
        "equities": {},
        "positions_counts": {},
        "sectors": [],
        "errors": [],
    }

    try:
        sys.path.append(str(VAULT))
        from tg_alerting.integrations.tinkoff import TinkoffClient
        dbg("Imported TinkoffClient from tg_alerting.integrations.tinkoff")
        dbginfo["imported_client"] = True
    except Exception as e:
        dbg(f"Import TinkoffClient failed: {e}")
        dbginfo["errors"].append(f"import_error: {e}")
        TinkoffClient = None

    if TinkoffClient is not None:
        try:
            dbg("Initializing TinkoffClient (with SDK in venv if available)...")
            # Prefer running under the same venv where SDK installed
            env_bin = VAULT / "800_Автоматизация" / "env" / "bin"
            os.environ.setdefault("PATH", f"{env_bin}:{os.environ.get('PATH','')}")
            api = TinkoffClient(token)
            accounts = api.get_accounts_v2() or []
            dbg(f"Accounts v2: {accounts}")
            dbginfo["accounts_v2"] = accounts
            if not accounts:
                main = api.get_main_account_id()
                dbg(f"Main account fallback: {main}")
                accounts = [main] if main else []
                dbginfo["main_account"] = main

            all_positions = []
            for acc_id in accounts:
                dbg(f"Fetching equity for account: {acc_id}")
                try:
                    eq = api.get_total_equity_rub(acc_id)
                    dbg(f"Equity {acc_id}: {eq}")
                    if eq is not None:
                        total_rub += float(eq)
                        dbginfo["equities"][acc_id] = float(eq)
                    pos = api.get_positions_detailed(acc_id) or []
                    dbg(f"Positions {acc_id}: {len(pos)} items")
                    all_positions.extend(pos)
                    dbginfo["positions_counts"][acc_id] = len(pos)
                except Exception as e:
                    dbg(f"Error account {acc_id}: {e}")
                    dbginfo["errors"].append(f"account_error {acc_id}: {e}")
                    continue
            if all_positions:
                by_sector = api.aggregate_by_sector(all_positions)
                dbg(f"Sectors: {list(by_sector.keys())}")
                dbginfo["sectors"] = list(by_sector.keys())
            if total_rub == 0 and not accounts:
                dbg("No accounts fetched; check token or API availability")
                dbginfo["errors"].append("no_accounts")
        except Exception as e:
            dbg(f"Fatal error in client flow: {e}")
            dbginfo["errors"].append(f"fatal_client: {e}")

    # REST v2 fallback if still empty
    if total_rub == 0:
        try:
            dbg("Trying REST v2 (HTTP/2) UsersService/GetAccounts...")
            base = "https://invest-public-api.tinkoff.ru/rest"
            url_acc = base + "/tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts"
            # prefer HTTP/2; fallback to HTTP/1.1 if not available
            try:
                client_ctx = httpx.Client(http2=True, timeout=25.0, follow_redirects=True)
            except Exception as e:
                dbg(f"HTTP/2 unavailable ({e}); falling back to HTTP/1.1")
                client_ctx = httpx.Client(timeout=25.0, follow_redirects=True)
            # fetch accounts
            with client_ctx as client:
                r = client.post(url_acc, json={}, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "x-app-name": "finance-bot/0.1",
                    "User-Agent": "finance-bot/0.1",
                })
                if r.status_code != 200:
                    dbg(f"REST v2 accounts http {r.status_code}: {r.text[:200]}")
                    raise RuntimeError(f"REST v2 accounts http {r.status_code}")
                acc_data = r.json()
            accounts = [
                str(a.get("id") or a.get("brokerAccountId"))
                for a in (acc_data.get("accounts") or acc_data.get("payload", {}).get("accounts") or [])
                if (a.get("id") or a.get("brokerAccountId"))
            ]
            dbg(f"REST accounts: {accounts}")
            dbginfo["accounts_rest"] = accounts
            for acc_id in accounts:
                url_port = base + "/tinkoff.public.invest.api.contract.v1.PortfolioService/GetPortfolio"
                payload = {"accountId": acc_id, "currency": "RUB"}
                # use separate client context to avoid closed-client issues
                try:
                    c2 = httpx.Client(http2=True, timeout=25.0, follow_redirects=True)
                except Exception:
                    c2 = httpx.Client(timeout=25.0, follow_redirects=True)
                with c2 as client2:
                    r2 = client2.post(url_port, json=payload, headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "x-app-name": "finance-bot/0.1",
                        "User-Agent": "finance-bot/0.1",
                    })
                if r2.status_code != 200:
                    dbg(f"REST v2 portfolio {acc_id} http {r2.status_code}: {r2.text[:200]}")
                    continue
                port = r2.json()
                mv = port.get("totalAmountPortfolio") or port.get("payload", {}).get("totalAmountPortfolio")
                if isinstance(mv, dict) and (mv.get("currency", "").lower() in ("rub", "rur")):
                    units = float(mv.get("units") or 0)
                    nano = float(mv.get("nano") or 0)
                    val = units + nano / 1_000_000_000
                    dbg(f"REST equity {acc_id}: {val}")
                    total_rub += val
                    dbginfo["equities"][acc_id] = val
        except Exception as e:
            dbg(f"REST fallback error: {e}")
            dbginfo["errors"].append(f"rest_error: {e}")

    # Legacy OpenAPI fallback
    if total_rub == 0:
        try:
            dbg("Trying legacy OpenAPI /user/accounts and /portfolio ...")
            base = "https://api-invest.tinkoff.ru/openapi"
            # accounts
            try:
                client2_ctx = httpx.Client(http2=True, timeout=20.0, follow_redirects=True)
            except Exception as e:
                dbg(f"HTTP/2 unavailable for legacy ({e}); falling back to HTTP/1.1")
                client2_ctx = httpx.Client(timeout=20.0, follow_redirects=True)
            with client2_ctx as client2:
                r = client2.get(base + "/user/accounts", headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "x-app-name": "finance-bot/0.1",
                    "User-Agent": "finance-bot/0.1",
                })
                if r.status_code != 200:
                    dbg(f"Legacy accounts http {r.status_code}: {r.text[:200]}")
                    raise RuntimeError(f"legacy http {r.status_code}")
                accs_data = r.json()
            accounts = [str(a.get("brokerAccountId")) for a in accs_data.get("payload", {}).get("accounts", []) if a.get("brokerAccountId")]
            dbg(f"Legacy accounts: {accounts}")
            dbginfo["accounts_rest"] = dbginfo.get("accounts_rest", []) or accounts
            # portfolio per account
            for acc_id in accounts:
                url = base + "/portfolio?brokerAccountId=" + acc_id
                r2 = client2.get(url, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "x-app-name": "finance-bot/0.1",
                    "User-Agent": "finance-bot/0.1",
                })
                if r2.status_code != 200:
                    dbg(f"Legacy portfolio {acc_id} http {r2.status_code}: {r2.text[:200]}")
                    continue
                port = r2.json()
                payload = port.get("payload", {})
                tot = payload.get("totalAmountPortfolio") or payload.get("totalAmountCurrencies")
                val = None
                if isinstance(tot, dict):
                    val = float(tot.get("value") or 0.0)
                dbg(f"Legacy equity {acc_id}: {val}")
                if val:
                    total_rub += float(val)
                    dbginfo["equities"][acc_id] = float(val)
        except Exception as e:
            dbg(f"Legacy fallback error: {e}")
            dbginfo["errors"].append(f"legacy_error: {e}")

    sector_rows = sorted(by_sector.items(), key=lambda x: -x[1]) if by_sector else []
    sector_md = None
    if sector_rows:
        sector_table = ["| Сектор | Стоимость ₽ |", "|---|---:|"]
        for name, val in sector_rows:
            sector_table.append(f"| {name} | {int(round(val)):,} |".replace(",", " "))
        sector_md = "\n".join(sector_table)

    return {
        "total_rub": int(round(total_rub)),
        "day_change_rub": int(round(day_change_rub)),
        "sector_table": sector_md,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "_debug": dbginfo,
    }


def write_cache_note(data: dict) -> None:
    CACHE_NOTE.parent.mkdir(parents=True, exist_ok=True)
    # Put key values into frontmatter so Dataview can read p.total_rub, p.day_change_rub, p.updated_at
    sector_md = data.get("sector_table")
    fm_lines = [
        "---",
        "tags: [investments, portfolio, cache]",
        f"total_rub: {data.get('total_rub', 0)}",
        f"day_change_rub: {data.get('day_change_rub', 0)}",
        f"updated_at: '{data.get('updated_at','')}'",
    ]
    if sector_md:
        # store as literal block in frontmatter for easy dv.page access
        fm_lines.append("sector_table: |-")
        for line in sector_md.splitlines():
            fm_lines.append(f"  {line}")
    fm_lines.append("---\n")
    body = ""
    CACHE_NOTE.write_text("\n".join(fm_lines) + body, encoding="utf-8")


def cleanup_temp_notes(max_age_seconds: int = 600) -> Dict[str, Any]:
    """Delete recent empty 'Untitled*.md' files in 600_Архив/Trash created by template runs."""
    removed = []
    errors = []
    trash_dir = VAULT / "600_Архив" / "Trash"
    now = time.time()
    try:
        if trash_dir.exists():
            for p in trash_dir.iterdir():
                try:
                    if not p.is_file():
                        continue
                    name = p.name
                    if not (name.startswith("Untitled") and name.endswith(".md")):
                        continue
                    stat = p.stat()
                    size_ok = stat.st_size <= 10
                    age_ok = (now - stat.st_mtime) <= max_age_seconds
                    if size_ok and age_ok:
                        p.unlink()
                        removed.append(name)
                except Exception as e:
                    errors.append(f"{p.name}: {e}")
    except Exception as e:
        errors.append(str(e))
    return {"removed": removed, "errors": errors}


def main() -> None:
    env = load_env(ENV_PATH)
    token = env.get("TINKOFF_API_TOKEN", "").strip()
    summary = fetch_tinkoff_summary(token) if token else {
        "total_rub": 0,
        "day_change_rub": 0,
        "sector_table": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_cache_note(summary)
    cleanup = cleanup_temp_notes()
    print(json.dumps({
        "ok": True,
        "debug": {
            "import_client": Path(VAULT / 'tg_alerting' / 'integrations' / 'tinkoff.py').exists(),
            "log_file": str(LOG_FILE),
            **summary.get("_debug", {}),
            "cleanup": cleanup,
        },
        **summary
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()


