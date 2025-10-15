import asyncio
import logging
import os
from typing import List, Tuple

from telethon import errors
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import Channel, InputPeerChannel

from opentele.td import TDesktop
from opentele.api import UseCurrentSession

INVITES_FILE = "invites.txt"
VALID_OUTPUT = "valid_invites.txt"
INVALID_OUTPUT = "invalid_invites.txt"


def load_invites(path: str) -> List[str]:
    with open(path, "r") as stream:
        return [line.strip() for line in stream if line.strip()]


def iter_account_folders(base_path: str) -> List[str]:
    return sorted(
        entry
        for entry in os.listdir(base_path)
        if entry.isdigit() and os.path.isdir(os.path.join(base_path, entry, "tdata"))
    )


async def create_client(base_path: str):
    for folder in iter_account_folders(base_path):
        tdata_folder = os.path.join(base_path, folder, "tdata")
        try:
            tdesk = TDesktop(tdata_folder)
            if not tdesk.isLoaded():
                logging.warning("Skipping %s: tdata not loaded", folder)
                continue

            session_name = f"validator_{folder}"
            client = await tdesk.ToTelethon(session=session_name, flag=UseCurrentSession)
            await client.connect()
            if await client.is_user_authorized():
                logging.info("Using account %s for validation", folder)
                return client
            logging.warning("Skipping %s: session not authorized", folder)
        except Exception as exc:  # pragma: no cover - defensive
            logging.error("Failed to prepare account %s: %s", folder, exc)
    raise RuntimeError("No valid account found to validate links.")


async def check_join_invite(client, invite_hash: str) -> Tuple[bool, str]:
    try:
        result = await client(CheckChatInviteRequest(invite_hash))
        title = getattr(result, "title", "Unknown")
        return True, f"Valid (invite to {title})"
    except errors.InviteHashExpiredError:
        return False, "Invite expired"
    except errors.InviteHashInvalidError:
        return False, "Invite invalid"
    except errors.UserAlreadyParticipantError:
        return True, "Already a participant"
    except errors.FloodWaitError as exc:
        return False, f"Flood wait {exc.seconds}s"
    except Exception as exc:  # pragma: no cover - unexpected
        return False, f"Error: {exc}"


async def check_public_link(client, username: str) -> Tuple[bool, str]:
    try:
        entity = await client.get_entity(username)
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(InputPeerChannel(entity.id, entity.access_hash)))
            title = full.chats[0].title if full.chats else entity.title
            return True, f"Valid (channel {title})"
        return True, "Valid entity"
    except errors.UsernameNotOccupiedError:
        return False, "Username not occupied"
    except errors.UsernameInvalidError:
        return False, "Username invalid"
    except errors.FloodWaitError as exc:
        return False, f"Flood wait {exc.seconds}s"
    except Exception as exc:  # pragma: no cover - unexpected
        return False, f"Error: {exc}"


async def validate_link(client, link: str) -> Tuple[bool, str]:
    lowered = link.lower()
    if lowered.startswith("http://"):
        link = "https://" + link[len("http://") :]
        lowered = link.lower()

    if "joinchat" in lowered or "/+" in lowered:
        invite_hash = link.split("/")[-1].replace("+", "")
        return await check_join_invite(client, invite_hash)

    if lowered.startswith("https://t.me/"):
        username = link.split("/")[-1]
        return await check_public_link(client, username)

    return False, "Unsupported link format"


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    if not os.path.exists(INVITES_FILE):
        raise FileNotFoundError(f"Could not find {INVITES_FILE}")

    invites = load_invites(INVITES_FILE)
    if not invites:
        logging.info("No invite links found in %s", INVITES_FILE)
        return

    client = await create_client(".")
    valid = []
    invalid = []

    try:
        for link in invites:
            ok, reason = await validate_link(client, link)
            (valid if ok else invalid).append((link, reason))
            logging.info("%s -> %s", link, reason)
    finally:
        await client.disconnect()

    with open(VALID_OUTPUT, "w") as v_out:
        for link, reason in valid:
            v_out.write(f"{link} # {reason}\n")

    with open(INVALID_OUTPUT, "w") as iv_out:
        for link, reason in invalid:
            iv_out.write(f"{link} # {reason}\n")

    logging.info("Validation complete. %d valid, %d invalid.", len(valid), len(invalid))
    logging.info("Results written to %s and %s", VALID_OUTPUT, INVALID_OUTPUT)


if __name__ == "__main__":
    asyncio.run(main())
