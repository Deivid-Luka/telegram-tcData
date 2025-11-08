from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from pathlib import Path
from typing import Dict, Iterable, List

from .account import AccountContext, TDLibAccount
from .config import ProjectConfig, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the TDLib-based automation workflow.")
    parser.add_argument("--config", default=None, help="Path to config.toml (defaults to tdlib_version/config.toml).")
    parser.add_argument("--limit", type=int, help="Override the maximum number of accounts to start.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level (default: INFO).")
    return parser.parse_args()


def load_invites(path: Path) -> List[str]:
    if not path.exists():
        logging.warning("Invite file %s not found; join loop disabled.", path)
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def load_accounts_from_csv(csv_path: Path) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not csv_path.exists():
        return mapping
    with open(csv_path, "r", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            session_id = (row.get("session_id") or "").strip()
            if not session_id:
                continue
            phone = (row.get("phone") or "").strip()
            mapping[session_id] = phone or f"+{session_id}"
    return mapping


def discover_account_contexts(cfg: ProjectConfig, invites: Iterable[str], limit_override: int | None = None) -> List[AccountContext]:
    invites_list = list(invites)
    sessions_dir = cfg.paths.sessions_root
    sessions_dir.mkdir(parents=True, exist_ok=True)

    mapping = {}
    if cfg.paths.accounts_csv:
        mapping.update(load_accounts_from_csv(cfg.paths.accounts_csv))

    tdata_root = cfg.paths.tdata_root
    if tdata_root.exists():
        for entry in sorted(tdata_root.iterdir()):
            if entry.is_dir() and entry.name.isdigit():
                mapping.setdefault(entry.name, f"+{entry.name}")

    contexts: List[AccountContext] = []
    for session_id, phone in sorted(mapping.items()):
        account_root = sessions_dir / session_id
        contexts.append(
            AccountContext(
                session_id=session_id,
                phone_number=phone,
                database_dir=account_root / "db",
                files_dir=account_root / "files",
                invites=list(invites_list),
            )
        )

    limit = limit_override if limit_override is not None else cfg.accounts.limit
    if limit:
        contexts = contexts[: int(limit)]
    return contexts


async def run_accounts(cfg: ProjectConfig, contexts: List[AccountContext]) -> None:
    if not contexts:
        logging.warning("No accounts discovered. Check exported_accounts.csv or numeric directories.")
        return

    accounts = [
        TDLibAccount(cfg.tdlib, cfg.messaging, cfg.joining, ctx, allow_interactive_login=cfg.accounts.allow_interactive_login)
        for ctx in contexts
    ]

    try:
        for account in accounts:
            await account.start()
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):  # pragma: no cover - cooperative shutdown
        logging.info("Cancellation received; stopping TDLib accounts.")
    finally:
        await asyncio.gather(*(account.stop() for account in accounts), return_exceptions=True)


async def async_main(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    invites = load_invites(cfg.paths.invites_file)
    contexts = discover_account_contexts(cfg, invites, limit_override=args.limit)
    await run_accounts(cfg, contexts)


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
