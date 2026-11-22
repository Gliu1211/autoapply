"""Start AutoApply: python backend/run.py  (then open http://127.0.0.1:8742)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app.config import HOST, PORT  # noqa: E402

def already_running() -> bool:
    """Windows lets two servers share a port, and two autopilots on one database fight over the browser."""
    import socket
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((HOST, PORT)) == 0


if __name__ == "__main__":
    if already_running():
        print(f"AutoApply is already running at http://{HOST}:{PORT} - open that, or stop the other copy first (Ctrl+C in its window).")
        sys.exit(0)
    # No reload/workers: on Windows those switch to a selector event loop that can't launch the browser.
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="info")
