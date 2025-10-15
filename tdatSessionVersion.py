import logging
import os
import time
import multiprocessing

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, SessionPasswordNeededError, UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, Chat, Channel, Message, MessageService
from datetime import datetime
from opentele.td import TDesktop
from opentele.api import UseCurrentSession
import random
import asyncio


class TelegramBot:
    def __init__(self, client, phone_number, session_id, invite_links=None, directory='logs'):
        self.client = client
        self.phone_number = phone_number
        self.session_id = session_id
        self.session_file = f'session_{session_id}'
        self.groups_to_write = [-4524328298]
        self.default_group_limit = 60
        self.your_user_id = '@togoshpk'
        self.command_group_id = -4811247148
        self.forward_to_group = -4842019800
        self.message_group = -4784715732
        self.messages = {"text": "+123456789+123456789", "photo": "testt.png"}
        self.active = False
        self.last_sent_time = {group_id: 0 for group_id in self.groups_to_write}
        self.group_limits = {group_id: self.default_group_limit for group_id in self.groups_to_write}
        self.command_group_invite = "https://t.me/+MbNH2JFIZD8zN2Vk"
        self.messages_group_invite = "https://t.me/+n0JdpJSFkkk0YzZk"
        self.forward_to_group_invite = "https://t.me/+_65l-IAyfC9lYTM8"
        self.start_time = "09:00"
        self.end_time = "23:00"
        self.file_path = os.path.join(directory, f"{session_id}_progress.log")
        self.invite_links = invite_links
        self.last_invite_index = 0
        self.background_tasks = []
        os.makedirs(directory, exist_ok=True)

    async def ensure_command_group_membership(self, invite_link=None, group_type='command'):
        logging.info(f"Bot {self.session_id} attempting to join the {group_type} group...")
        try:
            await self.join_desired_group(invite_link)
            logging.info(f"Bot {self.session_id} has successfully joined the {group_type} group.")
        except UserAlreadyParticipantError:
            logging.info(f"Bot {self.session_id} is already in the {group_type} group.")
        except Exception as e:
            logging.error(f"Failed to join {group_type} group for bot {self.session_id}: {e}")
            raise

    async def start(self):
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

    async def join_desired_group(self, message):
        try:
            if "joinchat" in message or "+" in message:
                hash_part = message.split('/')[-1].replace('+', '')
                await self.client(ImportChatInviteRequest(hash_part))
            else:
                await self.client(JoinChannelRequest(message))
                logging.info(f"Bot {self.session_id} joined the group with invite link: {message}")
        except Exception as e:
            logging.error(f"Error while joining group: {e}")

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
            start_index = last_invite_index
            end_index = start_index + 15

            if end_index <= len(self.invite_links):
                invite_links_to_try = self.invite_links[start_index:end_index]
            else:
                invite_links_to_try = self.invite_links[start_index:] + self.invite_links[
                                                                        :end_index % len(self.invite_links)]

            error_count = 0
            last_joined_link = None

            for i, invite_link in enumerate(invite_links_to_try, start=start_index):
                try:
                    await self.join_desired_group(invite_link)
                    logging.info(f"Bot {self.session_id} joined group: {invite_link}")
                    last_joined_link = invite_link
                except UserAlreadyParticipantError:
                    logging.info(f"Bot {self.session_id} is already in the group: {invite_link}")
                except Exception as e:
                    error_count += 1
                    logging.error(f"Bot {self.session_id} failed to join group {invite_link}: {e}")

                await asyncio.sleep(2)

            last_position = (end_index - 1) % len(self.invite_links)
            await self.save_progress(last_joined_link, last_position, error_count)

            # Update last invite index for the next batch
            last_invite_index = end_index % len(self.invite_links)

            await asyncio.sleep(2 * 60 * 60)

    async def setup_handlers(self):
        @self.client.on(events.NewMessage(incoming=True, chats=None))
        async def handler(event):
            if (event.is_private and event.sender_id != self.your_user_id) and event.chat_id != self.command_group_id:
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
        response = f"Session {self.session_id}: {self.phone_number} - {status}"
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
                if "joinchat" in invite_link or "+" in invite_link:
                    hash_part = invite_link.split('/')[-1].replace('+', '')
                    await self.client(ImportChatInviteRequest(hash_part))
                else:
                    await self.client(JoinChannelRequest(invite_link))
                await event.respond(f"Session {self.session_id} joined the group with invite link: {invite_link}")
            except Exception as e:
                await event.respond(f"Error while joining group: {e}")

    async def join_group_all(self, event, message):
        if len(message) > 1:
            invite_link = message[1]
            try:
                if "joinchat" in invite_link or "+" in invite_link:
                    hash_part = invite_link.split('/')[-1].replace('+', '')
                    await self.client(ImportChatInviteRequest(hash_part))
                else:
                    await self.client(JoinChannelRequest(invite_link))
                await event.respond(f"Session {self.session_id} joined the group with invite link: {invite_link}")
            except Exception as e:
                await event.respond(f"Error while joining group: {e}")

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
            current_time = datetime.now().time()

            if start_time_obj <= current_time <= end_time_obj and self.active:
                total_messages = await self.get_total_message_count(self.message_group)
                if total_messages > 0 and self.active:
                    try:
                        message = await self.get_regular_message(self.message_group, total_messages)
                        if message:
                            if message.photo:
                                caption = message.message or "No caption"

                                message_start_time = time.time()
                                eligible_groups = []
                                cooldown_remaining = []
                                current_timestamp = time.time()
                                excluded_ids = {abs(self.command_group_id), abs(self.forward_to_group),
                                                abs(self.message_group)}

                                for group_id in self.groups_to_write:
                                    if abs(group_id) in excluded_ids:
                                        continue

                                    limit_seconds = self.group_limits.get(group_id, self.default_group_limit)
                                    last_sent = self.last_sent_time.get(group_id, 0)
                                    elapsed = current_timestamp - last_sent

                                    if limit_seconds <= 0 or elapsed >= limit_seconds:
                                        eligible_groups.append(group_id)
                                    else:
                                        cooldown_remaining.append(limit_seconds - elapsed)

                                if not eligible_groups:
                                    sleep_for = max(min(cooldown_remaining, default=5), 1) if cooldown_remaining else 5
                                    await asyncio.sleep(sleep_for)
                                    continue

                                selected_group = random.choice(eligible_groups)

                                await self.client.send_file(selected_group, message.photo, caption=caption)
                                message_end_time = time.time()
                                self.last_sent_time[selected_group] = message_end_time

                                elapsed_time = message_end_time - message_start_time
                                print(f"Message sent to group {selected_group} in {elapsed_time:.2f} seconds")

                                await asyncio.sleep(15)
                            else:
                                print("Fetched message does not contain a photo.")
                        else:
                            print("Could not find a regular message after several attempts.")
                    except FloodWaitError as e:
                        print(f"Flood wait error for group {self.message_group}: {e.seconds} seconds")
                        await asyncio.sleep(e.seconds)
                    except Exception as e:
                        print(f"Error while sending message to group {self.message_group}: {e}")
                        await asyncio.sleep(2)
                    pass
                else:
                    await asyncio.sleep(2)
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

    tdesk = TDesktop(tdata_folder)
    if not tdesk.isLoaded():
        session_id = os.path.basename(account_folder)
        logging.error(f"Failed to load account from {tdata_folder}")
        log_failed_bot(session_id, reason="tdata not loaded")
        return None

    session_name = f"session_{os.path.basename(account_folder)}"
    client = await tdesk.ToTelethon(session=session_name, flag=UseCurrentSession)
    await client.connect()
    if not await client.is_user_authorized():
        logging.error(f"Client {session_name} is not authorized.")
        log_failed_bot(os.path.basename(account_folder), reason="Not authorized")
        return None
    else:
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
    for folder in account_folders:
        session_id = os.path.basename(folder)
        account_folder = os.path.join(base_folder, folder)

        # Create a separate process for each bot
        process = multiprocessing.Process(
            target=run_bot_process_safe, args=(session_id, invite_links, account_folder)
        )
        processes.append(process)

    # Start all processes
    for process in processes:
        process.start()

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
