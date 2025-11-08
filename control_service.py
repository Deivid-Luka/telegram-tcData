import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


STATUS_DIR = Path("logs/status")
CONTROL_DIR = Path("logs/control")

app = FastAPI(title="Telegram Bot Control Panel", version="1.0.0")


class CommandRequest(BaseModel):
    name: str = Field(..., description="Command identifier (e.g., start, stop, set_message).")
    data: Optional[Dict] = Field(default=None, description="Optional payload for the command.")


class BulkCommandRequest(BaseModel):
    session_ids: Optional[List[str]] = Field(default=None, description="Sessions to target, default is all.")
    command: CommandRequest


def _read_json_file(path: Path) -> Optional[Dict]:
    try:
        with path.open("r") as file:
            return json.load(file)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Malformed JSON in {path}: {exc}") from exc


def _write_json_file(path: Path, payload: Dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as file:
        json.dump(payload, file, indent=2)


def list_session_statuses() -> List[Dict]:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for status_file in STATUS_DIR.glob("*.json"):
        data = _read_json_file(status_file)
        if data:
            sessions.append(data)
    return sessions


def get_session_status(session_id: str) -> Dict:
    status_file = STATUS_DIR / f"{session_id}.json"
    data = _read_json_file(status_file)
    if not data:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found.")
    return data


def append_command(session_id: str, command: CommandRequest):
    control_file = CONTROL_DIR / f"{session_id}.json"
    payload = _read_json_file(control_file) or {"commands": []}
    entry = {
        "id": uuid.uuid4().hex,
        "name": command.name,
        "data": command.data or {},
        "created_at": datetime.utcnow().isoformat(),
    }
    payload["commands"].append(entry)
    _write_json_file(control_file, payload)
    return entry


def validate_command(command: CommandRequest):
    allowed = {
        "start",
        "stop",
        "set_message",
        "set_photo",
        "set_time",
        "set_limit",
        "populate_groups",
        "join",
        "set_interval",
        "refresh_status",
    }
    if command.name not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported command '{command.name}'.")
    if command.name == "set_message" and not (command.data and command.data.get("text")):
        raise HTTPException(status_code=400, detail="'set_message' requires text payload.")
    if command.name == "set_limit":
        if not (command.data and command.data.get("limit")):
            raise HTTPException(status_code=400, detail="'set_limit' requires limit payload.")
    if command.name == "join":
        if not (command.data and command.data.get("invite")):
            raise HTTPException(status_code=400, detail="'join' requires invite payload.")


@app.get("/health")
def healthcheck():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/sessions")
def list_sessions():
    sessions = list_session_statuses()
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/sessions/{session_id}")
def session_detail(session_id: str):
    return get_session_status(session_id)


@app.post("/sessions/{session_id}/commands")
def enqueue_command(session_id: str, command: CommandRequest):
    validate_command(command)
    entry = append_command(session_id, command)
    return {"queued": entry}


@app.post("/sessions/commands/bulk")
def enqueue_bulk_command(request: BulkCommandRequest):
    validate_command(request.command)
    if request.session_ids:
        target_sessions = request.session_ids
    else:
        target_sessions = [status["session_id"] for status in list_session_statuses()]
    results = []
    for session_id in target_sessions:
        entry = append_command(session_id, request.command)
        results.append({"session_id": session_id, "entry": entry})
    return {"queued": results, "count": len(results)}
