from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time as time_of_day
from pathlib import Path
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

from .config import MessagingSettings, JoinSettings, TDLibParameters
from .tdjson_client import TDJsonClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AccountContext:
    session_id: str
    phone_number: str
    database_dir: Path
    files_dir: Path
    invites: List[str] = field(default_factory=list)


class TDLibAccount:
    """
    Manages a single TDLib client instance, handling authentication, messaging, and
    invite joins for one Telegram account.
    """

    def __init__(
        self,
        tdlib: TDLibParameters,
        messaging: MessagingSettings,
        joining: JoinSettings,
        context: AccountContext,
        allow_interactive_login: bool = True,
    ) -> None:
        self.config = tdlib
        self.messaging = messaging
        self.joining = joining
        self.ctx = context
        self.allow_interactive_login = allow_interactive_login

        self.client = TDJsonClient(tdlib.tdlib_path, tdlib.log_verbosity)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._update_task: Optional[asyncio.Task] = None
        self._join_task: Optional[asyncio.Task] = None
        self._send_task: Optional[asyncio.Task] = None
        self._authorized = asyncio.Event()
        self._stopped = asyncio.Event()
        self._last_sent_time = {group_id: 0.0 for group_id in messaging.groups_to_write}
        self._join_index = 0
        self._timezone = ZoneInfo(messaging.timezone)
        self._start_time = _parse_time(messaging.start_time)
        self._end_time = _parse_time(messaging.end_time)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        LOGGER.info("Starting TDLib account %s (%s)", self.ctx.session_id, self.ctx.phone_number)
        self.ctx.database_dir.mkdir(parents=True, exist_ok=True)
        self.ctx.files_dir.mkdir(parents=True, exist_ok=True)

        self._update_task = asyncio.create_task(self._update_loop(), name=f"tdlib-update-{self.ctx.session_id}")
        await self.send_request({"@type": "getAuthorizationState"}, wait=False)
        await self._authorized.wait()

        await self._ensure_required_groups()

        self._send_task = asyncio.create_task(self._send_loop(), name=f"tdlib-send-{self.ctx.session_id}")
        if self.joining.enabled and self.ctx.invites:
            self._join_task = asyncio.create_task(self._join_loop(), name=f"tdlib-join-{self.ctx.session_id}")

    async def stop(self) -> None:
        LOGGER.info("Stopping TDLib account %s", self.ctx.session_id)
        self._stopped.set()
        for task in (self._join_task, self._send_task, self._update_task):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self._join_task, self._send_task, self._update_task) if task), return_exceptions=True)
        self.client.close()

    async def _update_loop(self) -> None:
        try:
            while not self._stopped.is_set():
                update = await asyncio.to_thread(self.client.receive, 1.0)
                if not update:
                    continue
                extra = update.get("@extra")
                if extra and extra in self._pending:
                    future = self._pending.pop(extra)
                    if update.get("@type") == "error":
                        future.set_exception(RuntimeError(update.get("message")))
                    else:
                        future.set_result(update)
                    continue

                handler_name = f"_handle_{update.get('@type')}"
                handler = getattr(self, handler_name, None)
                if handler:
                    await handler(update)
                elif update.get("@type") == "error":
                    LOGGER.error("[%s] TDLib error without extra: %s", self.ctx.session_id, update)
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            pass

    async def _handle_updateAuthorizationState(self, update: Dict[str, Any]) -> None:
        await self._handle_authorization_state(update["authorization_state"])

    async def _handle_authorization_state(self, state: Dict[str, Any]) -> None:
        state_type = state["@type"]
        LOGGER.debug("[%s] authorization state: %s", self.ctx.session_id, state_type)

        if state_type == "authorizationStateWaitTdlibParameters":
            parameters = {
                "database_directory": str(self.ctx.database_dir),
                "files_directory": str(self.ctx.files_dir),
                "use_message_database": True,
                "use_secret_chats": False,
                "api_id": self.config.api_id,
                "api_hash": self.config.api_hash,
                "system_language_code": self.config.system_language_code,
                "device_model": self.config.device_model,
                "application_version": self.config.application_version,
                "enable_storage_optimizer": True,
                "ignore_file_names": False,
            }
            await self.send_request({"@type": "setTdlibParameters", "parameters": parameters}, wait=False)

        elif state_type == "authorizationStateWaitEncryptionKey":
            await self.send_request(
                {"@type": "checkDatabaseEncryptionKey", "encryption_key": self.config.database_encryption_key},
                wait=False,
            )

        elif state_type == "authorizationStateWaitPhoneNumber":
            await self._set_phone_number()

        elif state_type == "authorizationStateWaitCode":
            await self._submit_code()

        elif state_type == "authorizationStateWaitPassword":
            await self._submit_password()

        elif state_type == "authorizationStateReady":
            self._authorized.set()

        elif state_type == "authorizationStateClosed":
            LOGGER.warning("Authorization closed for %s", self.ctx.session_id)
            self._stopped.set()

    async def _set_phone_number(self) -> None:
        if not self.allow_interactive_login:
            raise RuntimeError(
                f"Interactive login disabled but authorization requires phone for {self.ctx.session_id}"
            )
        await self.send_request(
            {
                "@type": "setAuthenticationPhoneNumber",
                "phone_number": self.ctx.phone_number,
                "settings": {"allow_flash_call": False, "is_current_phone_number": False},
            },
            wait=False,
        )

    async def _submit_code(self) -> None:
        if not self.allow_interactive_login:
            raise RuntimeError(f"Cannot complete login for {self.ctx.session_id} without interactive code input.")
        code = await asyncio.to_thread(
            input, f"Enter the login code for {self.ctx.phone_number} (session {self.ctx.session_id}): "
        )
        await self.send_request({"@type": "checkAuthenticationCode", "code": code.strip()}, wait=False)

    async def _submit_password(self) -> None:
        if not self.allow_interactive_login:
            raise RuntimeError(f"Cannot submit password for {self.ctx.session_id} because prompts are disabled.")
        password = await asyncio.to_thread(
            input, f"Enter the 2FA password for {self.ctx.phone_number} (session {self.ctx.session_id}): "
        )
        await self.send_request({"@type": "checkAuthenticationPassword", "password": password.strip()}, wait=False)

    async def _ensure_required_groups(self) -> None:
        await self._authorized.wait()
        for link in filter(None, (self.messaging.command_group_id, self.messaging.message_group, self.messaging.forward_to_group)):
            await self._join_by_id(link)

    async def _join_loop(self) -> None:
        try:
            while not self._stopped.is_set():
                batch = self._next_invite_batch()
                for invite in batch:
                    await self._join_invite(invite)
                    await asyncio.sleep(self.joining.join_attempt_interval)
                await asyncio.sleep(self.joining.join_cycle_interval)
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            pass

    async def _join_invite(self, invite_link: str) -> None:
        try:
            if "joinchat" in invite_link or "+" in invite_link:
                LOGGER.info("[%s] Joining private invite %s", self.ctx.session_id, invite_link)
                await self.send_request({"@type": "joinChatByInviteLink", "invite_link": invite_link}, wait=True)
            else:
                username = invite_link.rsplit("/", 1)[-1].lstrip("@")
                LOGGER.info("[%s] Joining public chat %s", self.ctx.session_id, username)
                result = await self.send_request({"@type": "searchPublicChat", "username": username}, wait=True)
                chat_id = result.get("id")
                await self.send_request({"@type": "joinChat", "chat_id": chat_id}, wait=True)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("[%s] Failed to join %s: %s", self.ctx.session_id, invite_link, exc)

    async def _join_by_id(self, chat_id: int) -> None:
        try:
            await self.send_request({"@type": "joinChat", "chat_id": int(chat_id)}, wait=True)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.debug("[%s] joinChat %s skipped: %s", self.ctx.session_id, chat_id, exc)

    async def _send_loop(self) -> None:
        try:
            while not self._stopped.is_set():
                if not self._within_sending_window():
                    await asyncio.sleep(30)
                    continue
                now = time.time()
                for group_id in self.messaging.groups_to_write:
                    last_sent = self._last_sent_time.get(group_id, 0.0)
                    if now - last_sent < self.messaging.default_group_limit:
                        continue
                    await self._send_payload(group_id)
                    self._last_sent_time[group_id] = time.time()
                    await asyncio.sleep(self.messaging.send_interval)
                await asyncio.sleep(5)
        except asyncio.CancelledError:  # pragma: no cover
            pass

    async def _send_payload(self, chat_id: int) -> None:
        text = self.messaging.text_template
        if self.messaging.media_path:
            content = {
                "@type": "inputMessagePhoto",
                "photo": {"@type": "inputFileLocal", "path": self.messaging.media_path},
                "caption": {"@type": "formattedText", "text": text},
            }
        else:
            content = {"@type": "inputMessageText", "text": {"@type": "formattedText", "text": text}}
        try:
            await self.send_request({"@type": "sendMessage", "chat_id": chat_id, "input_message_content": content}, wait=True)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("[%s] sendMessage to %s failed: %s", self.ctx.session_id, chat_id, exc)

    def _within_sending_window(self) -> bool:
        now = datetime.now(self._timezone).time()
        if self._start_time <= self._end_time:
            return self._start_time <= now <= self._end_time
        # Overnight case (e.g. start 22:00 end 06:00)
        return now >= self._start_time or now <= self._end_time

    def _next_invite_batch(self) -> List[str]:
        batch = []
        for _ in range(min(self.joining.join_batch_size, len(self.ctx.invites))):
            invite = self.ctx.invites[self._join_index % len(self.ctx.invites)]
            batch.append(invite)
            self._join_index += 1
        return batch

    async def send_request(self, query: Dict[str, Any], wait: bool = True, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        if not wait:
            self.client.send(query)
            return None

        if not self._loop:
            raise RuntimeError("TDLibAccount event loop is not initialized yet.")
        token = str(uuid.uuid4())
        future = self._loop.create_future()
        self._pending[token] = future
        query["@extra"] = token
        self.client.send(query)
        return await asyncio.wait_for(future, timeout)


def _parse_time(value: str) -> time_of_day:
    hour, minute = value.split(":")
    return time_of_day(int(hour), int(minute))
