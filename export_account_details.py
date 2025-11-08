import asyncio
import csv
import logging
import os
import sqlite3
from typing import Dict, Optional

try:
    from opentele.api import UseCurrentSession
    from opentele.td import TDesktop
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    TDesktop = None
    UseCurrentSession = None

OUTPUT_FILE = "exported_accounts.csv"
BASE_DIR = "."
OFFLINE_ONLY = os.environ.get("TDATA_EXPORT_OFFLINE", "1") == "1"


async def fetch_account_info(account_path: str) -> Optional[Dict[str, str]]:
    """
    Load a single Telegram tdata folder and return its account details.
    Falls back to offline data if networking is not allowed.
    """
    session_id = os.path.basename(account_path)

    # Offline-only mode simply returns the session id as the phone number
    if OFFLINE_ONLY or not TDesktop or not UseCurrentSession:
        return {"session_id": session_id, "phone": f"+{session_id}", "username": "", "first_name": "", "last_name": ""}

    tdata_folder = os.path.join(account_path, "tdata")
    if not os.path.isdir(tdata_folder):
        logging.warning("Skipping %s; missing tdata folder.", account_path)
        return None

    session_name = f"session_{session_id}"

    tdesk = TDesktop(tdata_folder)
    if not tdesk.isLoaded():
        logging.error("Unable to load tdata for %s.", session_id)
        return None

    client = await tdesk.ToTelethon(session=session_name, flag=UseCurrentSession)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logging.error("Client %s is not authorized.", session_name)
            return None

        me = await client.get_me()
        phone = getattr(me, "phone", None)
        username = getattr(me, "username", None)

        formatted_phone = f"+{phone}" if phone else ""
        formatted_username = f"@{username}" if username else ""

        return {
            "session_id": session_id,
            "phone": formatted_phone,
            "username": formatted_username,
            "first_name": getattr(me, "first_name", "") or "",
            "last_name": getattr(me, "last_name", "") or "",
        }
    except sqlite3.OperationalError as exc:
        logging.error("Database error for %s: %s", session_name, exc)
        return None
    except Exception as exc:  # pylint: disable=broad-except
        logging.error("Failed to fetch info for %s: %s", session_name, exc)
        return None
    finally:
        await client.disconnect()


async def export_accounts(base_dir: str, output_path: str) -> None:
    """
    Iterate through all numeric folders in base_dir and export account details.
    """
    account_dirs = sorted(
        entry
        for entry in os.listdir(base_dir)
        if entry.isdigit() and os.path.isdir(os.path.join(base_dir, entry))
    )

    if not account_dirs:
        logging.info("No account folders found in %s.", os.path.abspath(base_dir))
        return

    rows = []
    for folder in account_dirs:
        info = await fetch_account_info(os.path.join(base_dir, folder))
        if info:
            rows.append(info)

    if not rows:
        logging.info("No account information could be retrieved.")
        return

    fieldnames = ["session_id", "phone", "username", "first_name", "last_name"]
    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    logging.info("Exported %d accounts to %s.", len(rows), os.path.abspath(output_path))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(export_accounts(BASE_DIR, OUTPUT_FILE))


if __name__ == "__main__":
    main()
