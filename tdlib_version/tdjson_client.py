from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class TDJsonClient:
    """
    Minimal ctypes wrapper around libtdjson to interact with TDLib without third-party
    dependencies. Only the APIs required by this project are exposed.
    """

    def __init__(self, lib_path: Optional[str] = None, log_verbosity: int = 1) -> None:
        self._lib = self._load_library(lib_path)
        self._configure_ctypes()
        self._client = self._lib.td_json_client_create()
        self.execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": log_verbosity})

    @staticmethod
    def _load_library(lib_path: Optional[str]) -> ctypes.CDLL:
        if lib_path:
            candidate = Path(lib_path).expanduser()
            if not candidate.exists():
                raise FileNotFoundError(f"libtdjson not found at {candidate}")
            return ctypes.CDLL(str(candidate))

        env_path = os.environ.get("TDJSON_PATH") or os.environ.get("TDLIB_PATH")
        if env_path:
            candidate = Path(env_path).expanduser()
            if candidate.exists():
                return ctypes.CDLL(str(candidate))

        # Try common default names before allowing ctypes to raise.
        for candidate in ("libtdjson.dylib", "libtdjson.so", "tdjson.dll"):
            try:
                return ctypes.CDLL(candidate)
            except OSError:
                continue
        raise FileNotFoundError(
            "Unable to locate libtdjson. Set tdlib_path in config.toml or export TDLIB_PATH."
        )

    def _configure_ctypes(self) -> None:
        self._lib.td_json_client_create.restype = ctypes.c_void_p
        self._lib.td_json_client_send.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._lib.td_json_client_receive.argtypes = [ctypes.c_void_p, ctypes.c_double]
        self._lib.td_json_client_receive.restype = ctypes.c_char_p
        self._lib.td_json_client_execute.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._lib.td_json_client_execute.restype = ctypes.c_char_p
        self._lib.td_json_client_destroy.argtypes = [ctypes.c_void_p]

    def send(self, query: Dict[str, Any]) -> None:
        payload = json.dumps(query).encode("utf-8")
        self._lib.td_json_client_send(self._client, ctypes.c_char_p(payload))

    def receive(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        result = self._lib.td_json_client_receive(self._client, ctypes.c_double(timeout))
        if result:
            return json.loads(result.decode("utf-8"))
        return None

    def execute(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = json.dumps(query).encode("utf-8")
        result = self._lib.td_json_client_execute(self._client, ctypes.c_char_p(payload))
        if result:
            return json.loads(result.decode("utf-8"))
        return None

    def close(self) -> None:
        if self._client:
            self._lib.td_json_client_destroy(self._client)
            self._client = None

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        try:
            self.close()
        except Exception:
            pass
