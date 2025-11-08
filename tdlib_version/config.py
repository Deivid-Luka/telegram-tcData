from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Iterable, List, Optional

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    import tomli as tomllib  # type: ignore


@dataclasses.dataclass(slots=True)
class TDLibParameters:
    api_id: int
    api_hash: str
    device_model: str = "CodexAgent"
    system_language_code: str = "en"
    application_version: str = "1.0"
    use_test_dc: bool = False
    database_encryption_key: str = ""
    tdlib_path: Optional[str] = None
    log_verbosity: int = 1


@dataclasses.dataclass(slots=True)
class PathSettings:
    invites_file: Path
    accounts_csv: Optional[Path]
    sessions_root: Path
    tdata_root: Path


@dataclasses.dataclass(slots=True)
class MessagingSettings:
    groups_to_write: List[int]
    default_group_limit: int = 180
    send_interval: int = 180
    forward_to_group: Optional[int] = None
    command_group_id: Optional[int] = None
    message_group: Optional[int] = None
    start_time: str = "00:00"
    end_time: str = "23:59"
    timezone: str = "UTC"
    text_template: str = ""
    media_path: Optional[str] = None


@dataclasses.dataclass(slots=True)
class JoinSettings:
    enabled: bool = True
    join_batch_size: int = 2
    join_attempt_interval: int = 180
    join_cycle_interval: int = 45 * 60


@dataclasses.dataclass(slots=True)
class AccountRuntime:
    limit: Optional[int] = None
    allow_interactive_login: bool = True


@dataclasses.dataclass(slots=True)
class ProjectConfig:
    tdlib: TDLibParameters
    paths: PathSettings
    messaging: MessagingSettings
    joining: JoinSettings
    accounts: AccountRuntime


def _resolve_path(base: Path, *parts: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(str(Path(*parts)))))
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def _load_tdlib_settings(raw: dict) -> TDLibParameters:
    return TDLibParameters(
        api_id=int(raw["api_id"]),
        api_hash=str(raw["api_hash"]),
        device_model=str(raw.get("device_model", "CodexAgent")),
        system_language_code=str(raw.get("system_language_code", "en")),
        application_version=str(raw.get("application_version", "1.0")),
        use_test_dc=bool(raw.get("use_test_dc", False)),
        database_encryption_key=str(raw.get("database_encryption_key", "")),
        tdlib_path=raw.get("tdlib_path"),
        log_verbosity=int(raw.get("log_verbosity", 1)),
    )


def _load_path_settings(raw: dict, base: Path) -> PathSettings:
    invites_file = _resolve_path(base, raw.get("invites_file", "valid_invites.txt"))
    accounts_csv_raw = raw.get("accounts_csv")
    accounts_csv = _resolve_path(base, accounts_csv_raw) if accounts_csv_raw else None
    sessions_root = _resolve_path(base, raw.get("sessions_root", "tdlib_version/state"))
    tdata_root = _resolve_path(base, raw.get("tdata_root", "."))
    return PathSettings(
        invites_file=invites_file,
        accounts_csv=accounts_csv,
        sessions_root=sessions_root,
        tdata_root=tdata_root,
    )


def _load_messaging_settings(raw: dict, base: Path) -> MessagingSettings:
    groups = raw.get("groups_to_write", [])
    if not isinstance(groups, Iterable):
        raise ValueError("messaging.groups_to_write must be an iterable of chat ids.")
    groups_to_write = [int(g) for g in groups]
    media_path = raw.get("media_path")
    if media_path:
        media_path = str(_resolve_path(base, media_path))
    def _coerce(value: Optional[int]) -> Optional[int]:
        return None if value is None else int(value)

    return MessagingSettings(
        groups_to_write=groups_to_write,
        default_group_limit=int(raw.get("default_group_limit", 180)),
        send_interval=int(raw.get("send_interval", 180)),
        forward_to_group=_coerce(raw.get("forward_to_group")),
        command_group_id=_coerce(raw.get("command_group_id")),
        message_group=_coerce(raw.get("message_group")),
        start_time=str(raw.get("start_time", "00:00")),
        end_time=str(raw.get("end_time", "23:59")),
        timezone=str(raw.get("timezone", "UTC")),
        text_template=str(raw.get("text_template", "")),
        media_path=media_path,
    )


def _load_join_settings(raw: dict) -> JoinSettings:
    return JoinSettings(
        enabled=bool(raw.get("enabled", True)),
        join_batch_size=int(raw.get("join_batch_size", 2)),
        join_attempt_interval=int(raw.get("join_attempt_interval", 180)),
        join_cycle_interval=int(raw.get("join_cycle_interval", 45 * 60)),
    )


def _load_account_settings(raw: dict) -> AccountRuntime:
    return AccountRuntime(
        limit=raw.get("limit"),
        allow_interactive_login=bool(raw.get("allow_interactive_login", True)),
    )


def load_config(path: Optional[os.PathLike[str] | str] = None) -> ProjectConfig:
    """
    Load the TDLib project configuration from a TOML file.
    """
    base_path = Path(path or Path(__file__).with_name("config.toml"))
    if base_path.is_dir():
        base_path = base_path / "config.toml"

    with open(base_path, "rb") as config_file:
        data = tomllib.load(config_file)

    root_dir = base_path.parent
    tdlib = _load_tdlib_settings(data["tdlib"])
    paths = _load_path_settings(data["paths"], root_dir)
    messaging = _load_messaging_settings(data["messaging"], root_dir)
    joining = _load_join_settings(data.get("joining", {}))
    accounts = _load_account_settings(data.get("accounts", {}))
    return ProjectConfig(tdlib=tdlib, paths=paths, messaging=messaging, joining=joining, accounts=accounts)


__all__ = [
    "TDLibParameters",
    "MessagingSettings",
    "JoinSettings",
    "PathSettings",
    "AccountRuntime",
    "ProjectConfig",
    "load_config",
]
