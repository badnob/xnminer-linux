from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

from networking.difficulty import fetch_difficulty


def check_port80(base_url: str, timeout_s: float = 5.0) -> bool:
    host = urlparse(base_url).hostname or "xenblocks.io"
    try:
        with socket.create_connection((host, 80), timeout=timeout_s):
            return True
    except OSError:
        return False


def wait_for_server(difficulty_url: str, timeout_s: int = 180, poll_s: int = 3) -> int | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = fetch_difficulty(difficulty_url)
        if status.difficulty is not None:
            return status.difficulty
        time.sleep(poll_s)
    return None