from __future__ import annotations

from datetime import datetime
from pathlib import Path


class SessionLogger:
    def __init__(self, log_path: Path, echo_console: bool = True) -> None:
        self.log_path = log_path
        self.echo_console = echo_console
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        if self.echo_console:
            print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def info(self, msg: str) -> None:
        self._write("INFO", msg)

    def warn(self, msg: str) -> None:
        self._write("WARN", msg)

    def error(self, msg: str) -> None:
        self._write("ERROR", msg)

    def debug(self, msg: str) -> None:
        self._write("DEBUG", msg)