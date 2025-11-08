import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
API_HOST = os.environ.get("TCBOT_API_HOST", "127.0.0.1")
API_PORT = os.environ.get("TCBOT_API_PORT", "8000")

PROCESS_TABLE = []


def _start_process(args, name):
    print(f"[launcher] Starting {name}: {' '.join(args)}")
    proc = subprocess.Popen(args, cwd=ROOT)
    PROCESS_TABLE.append((name, proc))
    return proc


def _stop_processes():
    for name, proc in PROCESS_TABLE:
        if proc.poll() is not None:
            continue
        print(f"[launcher] Stopping {name} (pid={proc.pid})")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print(f"[launcher] Force killing {name}")
            proc.kill()


atexit.register(_stop_processes)


def main():
    bots_cmd = [PYTHON, "tdatSessionVersion.py"]
    api_cmd = [
        PYTHON,
        "-m",
        "uvicorn",
        "control_service:app",
        "--host",
        API_HOST,
        "--port",
        API_PORT,
    ]

    _start_process(bots_cmd, "bots-runner")
    _start_process(api_cmd, "control-api")

    # Give the API a moment to bind before the GUI tries to talk to it.
    time.sleep(2)

    os.environ.setdefault("TCBOT_API", f"http://{API_HOST}:{API_PORT}")

    try:
        from gui_dashboard import main as gui_main
    except ImportError as exc:  # pragma: no cover - import guard
        print(f"[launcher] Failed to import GUI: {exc}")
        return

    try:
        gui_main()
    except KeyboardInterrupt:
        print("[launcher] GUI interrupted, shutting down...")
    finally:
        _stop_processes()


if __name__ == "__main__":
    main()
