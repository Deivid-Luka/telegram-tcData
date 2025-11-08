import logging
import os
import time
import multiprocessing
import sqlite3

from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    SessionPasswordNeededError,
    UserAlreadyParticipantError,
    ChannelsTooMuchError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    RPCError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, Chat, Channel, Message, MessageService
from datetime import datetime, timedelta, timezone
from opentele.td import TDesktop
from opentele.api import UseCurrentSession
import random
import asyncio
from zoneinfo import ZoneInfo

try:
    import fcntl
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None


class TelegramBot:
    def __init__(self, client, phone_number, session_id, invite_links=None, directory='logs'):
        self.client = client
        self.phone_number = phone_number
        self.session_id = session_id
        self.session_file = f'session_{session_id}'
        self.groups_to_write = [-4524328298]
        self.default_group_limit = 180
        self.your_user_id = '@togoshpk'
        self.command_group_id = -4811247148
        self.forward_to_group = -4784715732
        self.message_group = -4842019800
        self.messages = {"text": "+123456789+123456789", "photo": "testt.png"}
        self.active = False
        self.last_sent_time = {group_id: 0 for group_id in self.groups_to_write}
        self.group_limits = {group_id: self.default_group_limit for group_id in self.groups_to_write}
        self.last_successful_send = 0
        self.command_group_invite = "https://t.me/+MbNH2JFIZD8zN2Vk"
        self.messages_group_invite = "https://t.me/+n0JdpJSFkkk0YzZk"
        self.forward_to_group_invite = "https://t.me/+_65l-IAyfC9lYTM8"
        self.start_time = "10:00"
        self.end_time = "22:00"
        self.timezone = ZoneInfo("Atlantic/Reykjavik")
        self.file_path = os.path.join(directory, f"{session_id}_progress.log")
        self.invite_links = invite_links
        self.last_invite_index = 0
        self.join_batch_size = 2
        self.join_attempt_interval = 180
        self.join_cycle_interval = 45 * 60
        self.join_block_until = datetime.now(timezone.utc)
        self.join_failures = 0
        self.join_failure_threshold = 3
        self.join_disabled = False
        self.join_disabled_reason = None
        self.group_failures = {}
        self.disabled_groups = set()
        self.failure_threshold = 3
        self.failure_cooldown = 90 * 60
        self.send_interval = 180
        self.background_tasks = []
        self.lock_dir = os.path.join(directory, "locks")
        os.makedirs(directory, exist_ok=True)
        os.makedirs(self.lock_dir, exist_ok=True)
        self.lock_path = os.path.join(self.lock_dir, f"{self.session_id}.lock")
        self.lock_handle = None

    async def ensure_command_group_membership(self, invite_link=None, group_type='command'):
        logging.info(f"Bot {self.session_id} attempting to join the {group_type} group...")
        try:
            await self.join_desired_group(invite_link)
            logging.info(f"Bot {self.session_id} has successfully joined the {group_type} group.")
        except UserAlreadyParticipantError:
            logging.info(f"Bot {self.session_id} is already in the {group_type} group.")
        except Exception as e:
            self._handle_join_penalty(e)
            logging.error(f"Failed to join {group_type} group for bot {self.session_id}: {e}")
            raise

    async def start(self):
        self._acquire_session_lock()
        try:
            await self.client.is_user_authorized()

            logging.info(f"Successfully authenticated {self.phone_number}")

            await self.client.get_dialogs()  # This marks all previous updates as read

            # # Join required groups if not already a member
            await self.ensure_command_group_membership(self.command_group_invite)
            await self.ensure_command_group_membership(self.messages_group_invite, group_type='message')
            await self.ensure_command_group_membership(self.forward_to_group_invite, group_type='forward')

            await self.setup_handlers()

            self.background_tasks = [
                asyncio.create_task(self.send_message_loop(), name=f"send-loop-{self.session_id}")
            ]

            if self.invite_links:
                self.background_tasks.append(
                    asyncio.create_task(self.join_groups_periodically(), name=f"join-loop-{self.session_id}")
                )

            try:
                await asyncio.gather(*self.background_tasks)
            finally:
                for task in self.background_tasks:
                    task.cancel()
                await asyncio.gather(*self.background_tasks, return_exceptions=True)
        except SessionPasswordNeededError:
            logging.error(
                f"Two-step verification is enabled for {self.phone_number}. Please disable it or handle it in the code.")
            raise
        except Exception as e:
            logging.error(f"An issue occurred with phone number {self.phone_number}: {e}")
            raise
        finally:
            self._release_session_lock()

    async def join_desired_group(self, message):
        if "joinchat" in message or "+" in message:
            hash_part = message.split('/')[-1].replace('+', '')
            await self.client(ImportChatInviteRequest(hash_part))
        else:
            await self.client(JoinChannelRequest(message))

    @staticmethod
    def _join_error_message(error):
        if isinstance(error, UserAlreadyParticipantError):
            return "Already a participant"
        if isinstance(error, ChannelsTooMuchError):
            return "Reached Telegram limit for channels/supergroups"
        if isinstance(error, FloodWaitError):
            return f"Flood wait {int(error.seconds)} seconds"
        if isinstance(error, InviteHashExpiredError):
            return "Invite expired"
        if isinstance(error, InviteHashInvalidError):
            return "Invite invalid or revoked"
        if isinstance(error, sqlite3.OperationalError):
            return "Local session database is locked"
        return str(error)

    def _acquire_session_lock(self):
        if self.lock_handle:
            return
        logging.info(f"Attempting to acquire session lock for {self.session_id}")
        try:
            if fcntl:
                handle = open(self.lock_path, "w")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close()
                    raise RuntimeError(
                        f"Session {self.session_id} is already active elsewhere. "
                        "Stop the other instance before starting this one."
                    )
                handle.write(str(os.getpid()))
                handle.flush()
                self.lock_handle = handle
            else:  # pragma: no cover - non-POSIX fallback
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                self.lock_handle = os.fdopen(fd, "w")
        except FileExistsError:  # pragma: no cover - fallback path
            raise RuntimeError(
                f"Session {self.session_id} appears to be active elsewhere (lock file present). "
                "Remove stale lock if you're sure no other instance is running."
            )
        logging.info(f"Session lock acquired for {self.session_id}")

    def _release_session_lock(self):
        if not self.lock_handle:
            return
        logging.info(f"Releasing session lock for {self.session_id}")
        try:
            if fcntl:
                try:
                    fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            self.lock_handle.close()
        finally:
            self.lock_handle = None
        try:
            os.unlink(self.lock_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logging.debug(f"Unable to remove lock file for session {self.session_id}: {exc}")

    def _handle_join_penalty(self, error):
        reason = self._join_error_message(error)
        if isinstance(error, ChannelsTooMuchError):
            self.join_disabled = True
            self.join_disabled_reason = reason
        elif isinstance(error, FloodWaitError):
            wait_seconds = int(error.seconds) + 10
            self.join_block_until = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
        elif isinstance(error, sqlite3.OperationalError):
            self.join_block_until = datetime.now(timezone.utc) + timedelta(minutes=5)

    @staticmethod
    def _format_last_sent(timestamp):
        if timestamp <= 0:
            return "never"
        diff = datetime.now(timezone.utc) - datetime.fromtimestamp(timestamp, tz=timezone.utc)
        seconds = diff.total_seconds()
        if seconds < 1:
            return "just now"

        intervals = [
            (86400, "d"),
            (3600, "h"),
            (60, "m"),
            (1, "s"),
        ]
        parts = []
        for unit_seconds, label in intervals:
            if seconds >= unit_seconds:
                value = int(seconds // unit_seconds)
                seconds -= value * unit_seconds
                if value:
                    parts.append(f"{value}{label}")
            if len(parts) == 2:
                break
        return " ".join(parts) if parts else "<1s"

    def _disable_group(self, group_id, reason):
        if group_id in self.disabled_groups:
            return
        self.disabled_groups.add(group_id)
        self.group_limits[group_id] = float('inf')
        self.last_sent_time[group_id] = time.time()
        logging.warning(
            f"Session {self.session_id} disabled group {group_id} due to repeated failures: {reason}"
        )

    def _mark_group_failure(self, group_id, reason, disable=False):
        if group_id is None:
            return
        count = self.group_failures.get(group_id, 0) + 1
        self.group_failures[group_id] = count
        cooldown = min(self.failure_cooldown, max(60, count * 300))
        current_limit = self.group_limits.get(group_id, self.default_group_limit)
        self.group_limits[group_id] = max(current_limit, cooldown)
        self.last_sent_time[group_id] = time.time()
        logging.warning(
            f"Session {self.session_id} encountered error sending to {group_id} "
            f"({count}/{self.failure_threshold}): {reason}"
        )
        if disable or count >= self.failure_threshold:
            logging.warning(
                f"Session {self.session_id} reached failure threshold for {group_id}, "
                f"but automatic disabling is skipped to keep posting active. Reason: {reason}"
            )

    def load_last_position(self):
        if not os.path.exists(self.file_path):
            with open(self.file_path, "w") as file:
                file.write("No previous session data found.\n")
            return 0

        with open(self.file_path, "r") as file:
            lines = file.readlines()
            for line in lines:
                if line.startswith("Last joined link position:"):
                    try:
                        return int(line.split(":")[1].strip())
                    except ValueError:
                        break  # Default to 0 if parsing fails

    async def save_progress(self, last_link, last_position, total_errors):
        with open(self.file_path, "a") as file:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            file.write(f"Timestamp: {timestamp}\n")
            file.write(f"Bot {self.session_id} processed this batch:\n")
            file.write(f"Last joined link: {last_link}\n")
            file.write(f"Last joined link position: {last_position}\n")
            file.write(f"Total errors in this run: {total_errors}\n\n")

    async def join_groups_periodically(self):
        last_invite_index = self.load_last_position()

        while True:
            if not self.invite_links:
                await asyncio.sleep(self.join_cycle_interval)
                continue

            if self.join_disabled:
                if self.join_disabled_reason:
                    logging.warning(
                        f"Join loop for session {self.session_id} is disabled: {self.join_disabled_reason}"
                    )
                    self.join_disabled_reason = None
                await asyncio.sleep(self.join_cycle_interval)
                continue

            now = datetime.now(timezone.utc)
            if now < self.join_block_until:
                wait_seconds = max((self.join_block_until - now).total_seconds(), 5)
                logging.debug(
                    f"Session {self.session_id} waiting {wait_seconds:.0f}s before next join attempt."
                )
                await asyncio.sleep(wait_seconds)
                continue

            batch = []
            invite_len = len(self.invite_links)
            for _ in range(min(self.join_batch_size, invite_len)):
                position = last_invite_index % invite_len
                batch.append((self.invite_links[position], position))
                last_invite_index = (last_invite_index + 1) % invite_len

            error_count = 0
            last_joined_link = None

            for attempt_index, (invite_link, position) in enumerate(batch):
                now = datetime.now(timezone.utc)
                if now < self.join_block_until or self.join_disabled:
                    break

                if attempt_index > 0:
                    await asyncio.sleep(self.join_attempt_interval)

                try:
                    await self.join_desired_group(invite_link)
                    logging.info(f"Bot {self.session_id} joined group: {invite_link}")
                    last_joined_link = invite_link
                    self.join_failures = 0
                except UserAlreadyParticipantError:
                    logging.debug(f"Session {self.session_id} already in group: {invite_link}")
                except ChannelsTooMuchError as e:
                    logging.warning(
                        f"Session {self.session_id} reached the maximum joined channels. Disabling further joins."
                    )
                    self._handle_join_penalty(e)
                    break
                except FloodWaitError as e:
                    logging.warning(
                        f"Session {self.session_id} hit flood wait ({int(e.seconds)}s) on join."
                    )
                    self._handle_join_penalty(e)
                    break
                except (InviteHashExpiredError, InviteHashInvalidError):
                    logging.info(
                        f"Session {self.session_id} failed to join (invalid invite): {invite_link}"
                    )
                    error_count += 1
                except sqlite3.OperationalError as e:
                    logging.error(
                        f"Session {self.session_id} encountered database lock while joining: {e}"
                    )
                    self._handle_join_penalty(e)
                    break
                except Exception as e:
                    error_count += 1
                    self.join_failures += 1
                    logging.error(
                        f"Session {self.session_id} failed to join group {invite_link}: {e}"
                    )
                    self._handle_join_penalty(e)
                    if self.join_failures >= self.join_failure_threshold:
                        cooldown_minutes = 30
                        logging.warning(
                            f"Session {self.session_id} backing off joins for {cooldown_minutes} minutes "
                            f"after repeated failures."
                        )
                        self.join_block_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
                        self.join_failures = 0
                        break

            if self.invite_links:
                last_position = (last_invite_index - 1) % len(self.invite_links)
                await self.save_progress(last_joined_link, last_position, error_count)

            await asyncio.sleep(self.join_cycle_interval)

    async def setup_handlers(self):
        allowed_group_ids = {
            self.command_group_id,
            self.forward_to_group,
            self.message_group,
        }

        @self.client.on(events.NewMessage(incoming=True))
        async def handler(event):
            chat_id = event.chat_id if event.chat_id is not None else event.sender_id

            if not event.is_private and chat_id not in allowed_group_ids:
                return

            if event.is_private and event.sender_id != self.your_user_id and chat_id != self.command_group_id:
                try:
                    await event.forward_to(self.forward_to_group)
                except Exception as e:
                    print(f"Error with session {self.session_id} (Phone: {self.phone_number}): {e}")

        @self.client.on(events.NewMessage(from_users=self.your_user_id))
        @self.client.on(events.NewMessage(chats=self.command_group_id))
        async def command_handler(event):
            if event.is_private or event.chat_id == self.command_group_id:
                await self.process_command(event)

    async def process_command(self, event):
        if event.message.file:
            await self.handle_file_command(event)
        else:
            await self.handle_text_command(event)

    async def handle_file_command(self, event):
        command = event.message.message.split(maxsplit=1)[0]
        if command == '/set' and event.message.file:
            bot_message = event.message.message.split(maxsplit=2)[2]
            self.messages['text'] = bot_message
            self.messages['photo'] = await event.message.download_media()
            await event.respond(f"Message and photo for session {self.session_id} set to: {bot_message}")

    async def handle_text_command(self, event):
        message = event.message.message.split()
        command = message[0]

        command_handlers = {
            '/message': self.set_message,
            '/photo': self.set_photo,
            '/status': self.get_status,
            '/groups': self.get_group_overview,
            '/start': self.start_session,
            '/stop': self.stop_session,
            '/startAll': self.start_all,
            '/stopAll': self.stop_all,
            '/limits': self.get_limits,
            '/set-time': self.set_time_range,
            '/set-limit': self.set_group_limit,
            '/join': self.join_group,
            '/joinAll': self.join_group_all,
            '/list-groups': self.list_groups,
            '/populate-groups': self.populate_groups,
            '/populate-groups-all': self.populate_groups_all
        }

        handler = command_handlers.get(command)
        if handler:
            await handler(event, message)

    async def set_message(self, event, message):
        if len(message) > 2 and int(message[1]) == self.session_id:
            bot_message = " ".join(message[2:])
            self.messages['text'] = bot_message
            await event.respond(f"Message for session {self.session_id} set to: {bot_message}")

    async def set_photo(self, event, message):
        if len(message) > 2 and int(message[1]) == self.session_id:
            photo_path = message[2]
            self.messages['photo'] = photo_path
            await event.respond(f"Photo for session {self.session_id} set to: {photo_path}")

    async def get_status(self, event, message):
        status = 'ON' if self.active else 'OFF'
        human_last_sent = self._format_last_sent(self.last_successful_send)
        total_groups = len(self.groups_to_write)
        active_groups = sum(1 for group_id in self.groups_to_write if group_id not in self.disabled_groups)
        disabled_groups = total_groups - active_groups
        response = (
            f"Session {self.session_id}: {self.phone_number} - {status}\n"
            f"Last post: {human_last_sent}\n"
            f"Groups: {active_groups}/{total_groups} active (disabled: {disabled_groups})"
        )
        await event.respond(response)

    async def start_session(self, event, message):
        if len(message) > 1 and int(message[1]) == self.session_id:
            self.active = True
            await event.respond(f"Session {self.session_id} started.")

    async def stop_session(self, event, message):
        if len(message) > 1 and int(message[1]) == self.session_id:
            self.active = False
            await event.respond(f"Session {self.session_id} stopped.")

    async def start_all(self, event, message):
        self.active = True
        await event.respond("All accounts started.")

    async def stop_all(self, event, message):
        self.active = False
        await event.respond("All accounts stopped.")

    async def get_limits(self, event, message):
        response = "\n".join(
            [f"Group {group_id}: {self.group_limits[group_id]} seconds" for group_id in self.groups_to_write])
        await event.respond(response)

    async def get_group_overview(self, event, message):
        total_groups = len(self.groups_to_write)
        active_groups = [group_id for group_id in self.groups_to_write if group_id not in self.disabled_groups]
        disabled_groups = [group_id for group_id in self.groups_to_write if group_id in self.disabled_groups]
        lines = [
            f"Session {self.session_id} group summary:",
            f"Total: {total_groups}",
            f"Active: {len(active_groups)}",
            f"Disabled: {len(disabled_groups)}",
        ]

        if active_groups:
            sample_active = ", ".join(str(group_id) for group_id in active_groups[:20])
            lines.append(f"Active sample: {sample_active}")

        if disabled_groups:
            sample_disabled = ", ".join(str(group_id) for group_id in disabled_groups[:20])
            lines.append(f"Disabled sample: {sample_disabled}")

        await self.send_long_message(event.chat_id, "\n".join(lines))

    async def set_time_range(self, event, message):
        if len(message) > 2:
            self.start_time = message[1]
            self.end_time = message[2]
            await event.respond(f"Time range set to: {self.start_time} - {self.end_time}")

    async def set_group_limit(self, event, message):
        if len(message) > 2:
            group_id = int(message[1])
            new_limit = int(message[2])
            self.group_limits[group_id] = new_limit
            await event.respond(f"Limit for group {group_id} set to {new_limit} seconds.")

    async def join_group(self, event, message):
        if len(message) > 2 and int(message[1]) == self.session_id:
            invite_link = message[2]
            try:
                await self.join_desired_group(invite_link)
                self.join_failures = 0
                await event.respond(f"Session {self.session_id} joined the group with invite link: {invite_link}")
            except UserAlreadyParticipantError:
                await event.respond(f"Session {self.session_id} is already in that group.")
            except Exception as e:
                self._handle_join_penalty(e)
                await event.respond(f"Error while joining group: {self._join_error_message(e)}")

    async def join_group_all(self, event, message):
        if len(message) > 1:
            invite_link = message[1]
            try:
                await self.join_desired_group(invite_link)
                self.join_failures = 0
                await event.respond(f"Session {self.session_id} joined the group with invite link: {invite_link}")
            except UserAlreadyParticipantError:
                await event.respond(f"Session {self.session_id} is already in that group.")
            except Exception as e:
                self._handle_join_penalty(e)
                await event.respond(f"Error while joining group: {self._join_error_message(e)}")

    async def list_groups(self, event, message):
        try:
            result = await self.client(GetDialogsRequest(
                offset_date=None,
                offset_id=0,
                offset_peer=InputPeerEmpty(),
                limit=200,
                hash=0
            ))
            chats = result.chats
            response = "Joined groups:\n"
            for chat in chats:
                if hasattr(chat, 'title'):
                    username = getattr(chat, 'username', 'No username')
                    response += f"{chat.id}: {chat.title} ({'@' + username if username else 'No username'})\n"
            await self.send_long_message(event.chat_id, response)
        except Exception as e:
            await event.respond(f"Error retrieving groups: {e}")

    async def populate_groups(self, event, message):
        if len(message) > 1 and int(message[1]) == self.session_id:
            await self._populate_groups(event)

    async def populate_groups_all(self, event, message):
        await self._populate_groups(event)

    async def _populate_groups(self, event):
        try:
            result = await self.client(GetDialogsRequest(
                offset_date=None,
                offset_id=0,
                offset_peer=InputPeerEmpty(),
                limit=200,
                hash=0
            ))
            chats = result.chats
            new_groups = []
            response = "Joined groups:\n"
            for chat in chats:
                if isinstance(chat, (Chat, Channel)) and abs(chat.id) not in {abs(self.command_group_id),
                                                                              abs(self.forward_to_group),
                                                                              abs(self.message_group)}:
                    new_groups.append(chat.id)
                    self.group_limits.setdefault(chat.id, self.default_group_limit)
                    self.last_sent_time.setdefault(chat.id, 0)
                    self.group_failures.setdefault(chat.id, 0)
                    self.disabled_groups.discard(chat.id)
                    response += f"{chat.id}: {chat.title}\n"
            self.groups_to_write.extend(new_groups)
            await event.respond(f"Groups to write updated with {len(new_groups)} new groups.")
        except Exception as e:
            await event.respond(f"Error retrieving groups: {e}")

    async def get_total_message_count(self, group_id):
        messages = await self.client.get_messages(group_id, limit=1)
        return messages.total if messages else 0

    async def get_regular_message(self, group_id, total_messages):
        for _ in range(10):
            random_position = random.randint(0, total_messages - 1)
            message = await self.get_message_at_position(group_id, random_position)
            if message and isinstance(message, Message) and not isinstance(message, MessageService):
                return message
        return None

    async def get_message_at_position(self, group_id, position):
        messages = await self.client.get_messages(group_id, limit=1, add_offset=position)
        if messages:
            return messages[0]
        return None

    async def send_message_loop(self):
        while True:
            start_time_obj = self.parse_time(self.start_time)
            end_time_obj = self.parse_time(self.end_time)
            current_time = datetime.now(self.timezone).time()

            if start_time_obj <= current_time <= end_time_obj and self.active:
                total_messages = await self.get_total_message_count(self.message_group)
                if total_messages > 0 and self.active:
                    selected_group = None
                    try:
                        message = await self.get_regular_message(self.message_group, total_messages)
                        if message:
                            media = None
                            media_type = None
                            if message.photo:
                                media = message.photo
                                media_type = "photo"
                            elif getattr(message, "video", None):
                                media = message.video
                                media_type = "video"
                            elif getattr(message, "document", None) and getattr(message.document, "mime_type", None):
                                if message.document.mime_type.startswith("video/"):
                                    media = message.document
                                    media_type = "video"

                            current_timestamp = time.time()
                            eligible_groups = []
                            cooldown_remaining = []
                            excluded_ids = {
                                abs(self.command_group_id),
                                abs(self.forward_to_group),
                                abs(self.message_group),
                            }
                            for group_id in self.groups_to_write:
                                if abs(group_id) in excluded_ids:
                                    continue
                                if group_id in self.disabled_groups:
                                    continue
                                self.group_limits.setdefault(group_id, self.default_group_limit)
                                self.last_sent_time.setdefault(group_id, 0)
                                self.group_failures.setdefault(group_id, 0)
                                limit_seconds = self.group_limits.get(group_id, self.default_group_limit)
                                last_sent = self.last_sent_time.get(group_id, 0)
                                elapsed = current_timestamp - last_sent

                                if limit_seconds <= 0 or elapsed >= limit_seconds:
                                    eligible_groups.append(group_id)
                                elif limit_seconds != float('inf'):
                                    cooldown_remaining.append(limit_seconds - elapsed)

                            if not eligible_groups:
                                sleep_for = (
                                    max(min(cooldown_remaining), 5)
                                    if cooldown_remaining
                                    else max(self.send_interval, 5)
                                )
                                await asyncio.sleep(sleep_for)
                                continue

                            selected_group = random.choice(eligible_groups)
                            message_start_time = time.time()
                            try:
                                if media:
                                    caption = message.message or ""
                                    await self.client.send_file(
                                        selected_group,
                                        media,
                                        caption=caption if caption else None,
                                    )
                                elif message.message:
                                    await self.client.send_message(selected_group, message.message)
                                    media_type = "text"
                                else:
                                    print("Fetched message does not contain supported media.")
                                    await asyncio.sleep(10)
                                    continue

                                message_end_time = time.time()
                                self.last_sent_time[selected_group] = message_end_time
                                self.last_successful_send = message_end_time
                                self.group_failures[selected_group] = 0
                                self.disabled_groups.discard(selected_group)

                                elapsed_time = message_end_time - message_start_time
                                print(
                                    f"Message ({media_type or 'unknown'}) sent to group {selected_group} "
                                    f"in {elapsed_time:.2f} seconds"
                                )

                                jitter = random.uniform(-0.25, 0.25)
                                delay = max(5, self.send_interval * (1 + jitter))
                                await asyncio.sleep(delay)
                            except FloodWaitError as e:
                                wait = int(e.seconds) + 10
                                self.group_limits[selected_group] = max(
                                    self.group_limits.get(selected_group, self.default_group_limit), wait
                                )
                                self.last_sent_time[selected_group] = time.time()
                                logging.warning(
                                    f"Session {self.session_id} hit flood wait ({wait}s) sending to {selected_group}"
                                )
                                await asyncio.sleep(e.seconds)
                            except RPCError as e:
                                error_name = e.__class__.__name__
                                disable = error_name in {
                                    "ChatWriteForbiddenError",
                                    "ChatWriteRestrictedError",
                                    "ChatAdminRequiredError",
                                    "UserBannedInChannelError",
                                    "ChannelPrivateError",
                                    "PeerIdInvalidError",
                                    "ChatSendMediaForbiddenError",
                                }
                                reason = f"{error_name}: {getattr(e, 'message', str(e))}"
                                self._mark_group_failure(selected_group, reason, disable=disable)
                                await asyncio.sleep(5)
                            except Exception as e:
                                self._mark_group_failure(selected_group, str(e))
                                await asyncio.sleep(5)
                        else:
                            print("Could not find a regular message after several attempts.")
                            await asyncio.sleep(10)
                    except FloodWaitError as e:
                        print(f"Flood wait error for group {self.message_group}: {e.seconds} seconds")
                        await asyncio.sleep(e.seconds)
                    except Exception as e:
                        print(f"Error while sending message to group {self.message_group}: {e}")
                        if selected_group is not None:
                            self._mark_group_failure(selected_group, str(e))
                        await asyncio.sleep(5)
                    pass
                else:
                    await asyncio.sleep(10)
                    print(f"No messages found in group {self.message_group}.")
            else:
                print("Current time is outside the specified time range for sending messages.")
                await asyncio.sleep(100)

    async def send_long_message(self, chat_id, message, chunk_size=4096):
        for i in range(0, len(message), chunk_size):
            chunk = message[i:i + chunk_size]
            await self.client.send_message(chat_id, chunk)

    @staticmethod
    def parse_time(time_str):
        return datetime.strptime(time_str, "%H:%M").time()


async def run_bot_instance(bot):
    max_retries = 2
    retry_count = 0

    while retry_count < max_retries:
        try:
            logging.warning(
                f"Initializing bot with session_id: {bot.session_id} (attempt {retry_count + 1}/{max_retries})...")
            await bot.start()
            logging.info(f"Bot with session_id: {bot.session_id} started successfully")
            return  # Exit the function if the bot starts successfully
        except RuntimeError as e:
            logging.error(f"Startup aborted for bot {bot.session_id}: {e}")
            return
        except Exception as e:
            retry_count += 1
            logging.error(f"Error in bot {bot.session_id} on attempt {retry_count}: {e}")
            if retry_count >= max_retries:
                log_failed_bot(bot.session_id)
                break  # Stop retrying after max retries


async def setup_client(account_folder):
    tdata_folder = os.path.join(account_folder, 'tdata')
    if not os.path.exists(tdata_folder):
        raise FileNotFoundError(f"tdata folder not found in {account_folder}")

    session_id = os.path.basename(account_folder)
    tdesk = TDesktop(tdata_folder)
    if not tdesk.isLoaded():
        logging.error(f"Failed to load account from {tdata_folder}")
        log_failed_bot(session_id, reason="tdata not loaded")
        return None

    session_name = f"session_{session_id}"
    client = await tdesk.ToTelethon(session=session_name, flag=UseCurrentSession)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logging.error(f"Client {session_name} is not authorized.")
            log_failed_bot(session_id, reason="Not authorized")
            return None
    except sqlite3.OperationalError as e:
        logging.error(f"Client {session_name} failed to open session database: {e}")
        log_failed_bot(session_id, reason="Session database locked")
        return None
    except Exception as e:
        logging.error(f"Client {session_name} failed to connect: {e}")
        log_failed_bot(session_id, reason=str(e))
        return None

    logging.info(f"Client {session_name} is authorized.")
    return client


def run_bot_process_safe(session_id, invite_links, account_folder):
    """
    A wrapper to safely run the bot in a separate process.
    """
    try:
        asyncio.run(run_bot(session_id, invite_links, account_folder))
    except Exception as e:
        logging.error(f"Error in bot process for session_id {session_id}: {e}")


async def run_bot(session_id, invite_links, account_folder):
    """
    Runs a single bot instance asynchronously.
    """
    client = await setup_client(account_folder)
    if not client:
        logging.error(f"Failed to set up client for session_id {session_id}")
        return

    me = await client.get_me()
    bot = TelegramBot(
        session_id=session_id,
        invite_links=invite_links,
        client=client,
        phone_number=me.phone
    )

    logging.info(f"Starting bot {session_id}...")
    await run_bot_instance(bot)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Load invite links
    try:
        with open('invites.txt', 'r') as f:
            invite_links = [line.strip() for line in f if line.strip()]
        logging.info(f"Loaded {len(invite_links)} invite links.")
    except Exception as e:
        logging.error(f"Failed to read invite links from 'invites.txt': {e}")
        return

    base_folder = '.'
    account_folders = [folder for folder in os.listdir(base_folder) if folder.isdigit()]

    processes = []
    for idx, folder in enumerate(account_folders):
        session_id = os.path.basename(folder)
        account_folder = os.path.join(base_folder, folder)

        # Create a separate process for each bot
        process = multiprocessing.Process(
            target=run_bot_process_safe, args=(session_id, invite_links, account_folder)
        )
        process.start()
        processes.append(process)

        if idx < len(account_folders) - 1:
            time.sleep(1)

    # Wait for all processes to complete
    for process in processes:
        process.join()


def log_failed_bot(session_id, reason="Unknown"):
    """
    Logs a failed bot to a file.
    """
    log_file = 'failed_bots.log'
    with open(log_file, 'a') as file:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file.write(f"{timestamp} - Bot session ID {session_id} failed: {reason}\n")
    logging.warning(f"Logged failure for bot session ID {session_id} due to: {reason}")


if __name__ == "__main__":
    main()
