from __future__ import annotations

import os
import sys
from pathlib import Path


class InstanceLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

    def acquire(self) -> bool:
        if self.lock_path.exists():
            try:
                old_pid = int(self.lock_path.read_text(encoding="utf-8").strip())
                if _pid_alive(old_pid):
                    return False
            except (ValueError, OSError):
                pass
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass
        self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def release(self) -> None:
        try:
            if self.lock_path.exists():
                current = self.lock_path.read_text(encoding="utf-8").strip()
                if current == str(os.getpid()):
                    self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True