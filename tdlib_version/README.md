# TDLib automation pipeline

This folder contains a TDLib-based replacement for the previous Telethon workflow. The entry
point is `runner.py`, which spins up one TDLib client per account, joins invite links, and posts
messages to the configured groups on a schedule.

## Prerequisites

1. **TDLib binaries** – compile TDLib or install a prebuilt package so that `libtdjson` is available.
   Set `tdlib_path` in `config.toml` or export `TDLIB_PATH`/`TDJSON_PATH` if it is not on the default
   loader path.
2. **API credentials** – add your Telegram `api_id`/`api_hash` to `config.toml`.
3. **Python deps** – the standard library is enough; no external packages are required because we
   talk to TDLib through `ctypes`.

## Configuration

1. Copy `config.example.toml` to `config.toml` (already done once) and update:
   - `[tdlib]` block with your API credentials and optional `tdlib_path`.
   - `[paths]` if your `valid_invites.txt`, `exported_accounts.csv`, or numeric `tdata`
     directories live elsewhere.
   - `[messaging]` for the text/photo payload and group ids.
   - `[joining]` to tune batch size / intervals or disable invite joins.
   - `[accounts]` to limit how many sessions are started per run or to disable interactive login.
2. The script discovers accounts in two ways (merged together):
   - rows inside `exported_accounts.csv` (phone numbers can be prefilled there);
   - directories whose name is made of digits (e.g. `56948524261/`). The folder names are treated as
     phone numbers if the CSV does not contain an explicit mapping.
3. TDLib state is written to `tdlib_version/state/<session>/` so it stays isolated from the original
   `tdata` folders. The first run for every account will ask for the login code (and 2FA password if
   enabled); afterwards TDLib reuses the stored database and no input is required.

## Running the workflow

```bash
cd tdlib_version
python runner.py --config config.toml --log-level INFO
```

Optional flags:

- `--limit 10` limits the number of accounts for quick smoke testing.
- `--log-level DEBUG` prints the underlying TDLib state transitions.

The process keeps running until you press `Ctrl+C`. During shutdown it gracefully stops every TDLib
client so the local database remains consistent.

## Notes on invites and group membership

- Invites are loaded from the file specified in `[paths].invites_file`. If the file contains private
  links (with `+` or `joinchat`) TDLib automatically calls `joinChatByInviteLink`. Public usernames
  are resolved via `searchPublicChat`.
- Required groups such as the command/message/forwarding chats defined under `[messaging]` are joined
  automatically after authorization.

## Extensibility

The TDLib layer is built from three small modules:

- `config.py` – TOML parsing into dataclasses.
- `tdjson_client.py` – thin `ctypes` wrapper over `libtdjson`.
- `account.py` – per-account state machine (auth, message scheduling, invite loop).
- `runner.py` – orchestrates discovery of accounts and orchestrates the asyncio tasks.

You can extend `TDLibAccount` to forward messages, add command handling, or export metrics without
touching the lower layers. When moving to a server you only need to copy the `tdlib_version` folder,
fill in `config.toml`, and provide the compiled TDLib binaries.
