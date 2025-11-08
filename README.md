## Telegram Bot Control Panel

This repository now includes a FastAPI backend and a PySide6 desktop dashboard so you can manage every session without relying on the Telegram command group.

### Project Layout

| File | Purpose |
| --- | --- |
| `tdatSessionVersion.py` | Core Telethon bot logic. Now writes status snapshots and consumes control commands from `logs/control`. |
| `control_service.py` | FastAPI service that exposes `/sessions` and `/sessions/{id}/commands` endpoints. It simply reads `logs/status/*.json` and appends commands into `logs/control/*.json`. |
| `gui_dashboard.py` | PySide6 desktop UI that calls the FastAPI endpoints, lists all sessions, and queues commands with buttons/inputs. |

### Running the stack

1. **Start the bots** (as before, with your preferred launcher). Each bot writes a status JSON file every ~5 seconds and monitors `logs/control/<session>.json` for queued commands.
2. **Start the API service** (requires FastAPI & Uvicorn):
   ```bash
   uvicorn control_service:app --reload
   ```
   The service listens on `http://127.0.0.1:8000` by default.
3. **Launch the GUI** (requires PySide6 & requests):
   ```bash
   python gui_dashboard.py
   ```
   Use the `TCBOT_API` environment variable if the API runs on another host/port.

### Supported commands

Buttons in the GUI (and the API) enqueue the following control actions:

- `start` / `stop`
- `populate_groups`
- `set_message`
- `set_photo`
- `set_time`
- `set_limit` (optional per-group override)
- `set_interval`
- `join` (invite link)
- `refresh_status`
- `start` / `stop` (per session or “Start All” / “Stop All” from the GUI, which call the bulk endpoint)

The bots process these commands in the background and log every action to `logs/<session>_control.log`. Because automatic disabling is removed, the dashboard will always show active group counts and the real “last successful post” timestamp.

### One-shot launcher

If you prefer a single command that boots the bots, API, and GUI together, use `start_all.py`:

```bash
python start_all.py
```

The launcher:
- runs `tdatSessionVersion.py` and the FastAPI service (`uvicorn control_service:app`) in the background,
- waits a moment for the API to come up,
- opens the PySide6 dashboard in the foreground,
- shuts everything down cleanly when you close the GUI.

Override the API binding (e.g., to expose it on the network) with:
```bash
export TCBOT_API_HOST=0.0.0.0
export TCBOT_API_PORT=9000
python start_all.py
```
The GUI automatically points to `http://TCBOT_API_HOST:TCBOT_API_PORT`.
