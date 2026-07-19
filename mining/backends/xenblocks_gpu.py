"""GPU backend: supervises xenblocks.exe and reads stats from its output."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

from config.settings import Settings
from core.models import BlockHit
from mining.base import MineBatchResult, MinerBackend

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
HS_RE = re.compile(r"([\d,]+)\s+H/s")


class XenblocksGpuBackend(MinerBackend):
    def __init__(self, settings: Settings, strategy_name: str = "gpu") -> None:
        self.settings = settings
        self.strategy_name = strategy_name
        self._proc: subprocess.Popen | None = None
        self._lanes = 1
        self._log_path = settings.log_path.parent / "xenblocks_gpu.log"
        self._last_hs = 0
        self._log_handle = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if not self.settings.xenblocks_exe or not self.settings.xenblocks_exe.exists():
            raise FileNotFoundError(f"xenblocks.exe not found: {self.settings.xenblocks_exe}")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.is_running:
            return
        self._log_handle = self._log_path.open("w", encoding="utf-8", errors="replace")
        self._proc = subprocess.Popen(
            [str(self.settings.xenblocks_exe), "--debug"],
            cwd=str(self.settings.xenblocks_exe.parent),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )

    def stop(self) -> None:
        if self._log_handle:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        if self._proc and self._proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                    capture_output=True,
                    timeout=15,
                )
            else:
                self._proc.terminate()
        self._proc = None

    def set_lanes(self, lanes: int) -> None:
        self._lanes = max(1, lanes)

    def _read_hs(self) -> int:
        if not self._log_path.exists():
            return 0
        raw = self._log_path.read_text(encoding="utf-8", errors="replace")
        text = ANSI_RE.sub("", raw)
        vals = [int(x.replace(",", "")) for x in HS_RE.findall(text)]
        return vals[-1] if vals else 0

    def mine_batch(self, batch_size: int) -> MineBatchResult:
        if self._proc and self._proc.poll() is not None:
            return MineBatchResult(hashes_done=0, hit=None)
        if not self.is_running:
            time.sleep(1.0)
            return MineBatchResult(hashes_done=0, hit=None)
        time.sleep(1.0)
        hs = self._read_hs()
        done = max(0, hs - self._last_hs) if hs >= self._last_hs else hs
        self._last_hs = hs
        return MineBatchResult(hashes_done=done, hit=None)

    @property
    def active_lanes(self) -> int:
        return self._lanes