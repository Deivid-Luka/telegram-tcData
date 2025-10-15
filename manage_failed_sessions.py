import argparse
import os
import re
import shutil
from typing import List, Set


def parse_failed_ids(log_path: str) -> List[str]:
    """Read failed session IDs from the log file."""
    if not os.path.exists(log_path):
        return []

    pattern = re.compile(r"Bot session ID (\d+)\s+failed")
    failed_ids: List[str] = []
    with open(log_path, "r") as log_file:
        for line in log_file:
            match = pattern.search(line)
            if match:
                failed_ids.append(match.group(1))
    return failed_ids


def get_account_directories(base_path: str) -> Set[str]:
    """Return numeric folder names in base_path."""
    return {
        entry
        for entry in os.listdir(base_path)
        if entry.isdigit() and os.path.isdir(os.path.join(base_path, entry))
    }


def delete_directories(directories: List[str], base_path: str, force: bool = False) -> None:
    """Delete directories safely; requires --delete flag to trigger."""
    if not directories:
        return

    for directory in directories:
        path = os.path.join(base_path, directory)
        if os.path.isdir(path):
            print(f"{'Deleting' if force else 'Would delete'} {path}")
            if force:
                shutil.rmtree(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List or delete Telegram tdata folders that failed to load."
    )
    parser.add_argument(
        "--log",
        default="failed_bots.log",
        help="Path to the failed bots log file (default: failed_bots.log).",
    )
    parser.add_argument(
        "--base",
        default=".",
        help="Base directory containing numeric session folders (default: current directory).",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the matching directories instead of only listing them.",
    )

    args = parser.parse_args()

    failed_ids = parse_failed_ids(args.log)
    if not failed_ids:
        print(f"No failed session IDs found in {args.log}.")
        return

    unique_failed_ids = sorted(set(failed_ids))
    account_dirs = get_account_directories(args.base)

    print("Failed session IDs:")
    for session_id in unique_failed_ids:
        exists = session_id in account_dirs
        marker = "[exists]" if exists else "[missing]"
        print(f"  {session_id} {marker}")

    existing_failed = [session_id for session_id in unique_failed_ids if session_id in account_dirs]
    missing_failed = [session_id for session_id in unique_failed_ids if session_id not in account_dirs]

    print(f"\nSummary:")
    print(f"  Total failures logged: {len(failed_ids)}")
    print(f"  Unique failed IDs: {len(unique_failed_ids)}")
    print(f"  Existing directories to review: {len(existing_failed)}")
    print(f"  Already removed: {len(missing_failed)}")

    if args.delete:
        confirm = input("Type 'yes' to confirm deletion of the listed directories: ").strip().lower()
        if confirm == "yes":
            delete_directories(existing_failed, args.base, force=True)
            print("Deletion complete.")
        else:
            print("Deletion aborted.")
    else:
        print("\nRun again with --delete to remove the directories listed as [exists].")


if __name__ == "__main__":
    main()
