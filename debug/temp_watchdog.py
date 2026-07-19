"""Independent GPU temperature watchdog — runs alongside the miner."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import load_settings
from monitoring.nvidia import NvidiaMonitor


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def _stop_miner() -> list[int]:
    stopped: list[int] = []
    main_py = str(ROOT / "main.py")
    if sys.platform == "win32":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '*{main_py}*' }} | "
                    "ForEach-Object { $_.ProcessId }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                stopped.append(pid)
    else:
        # pkill -f matches the full command line; only this install's main.py.
        try:
            result = subprocess.run(
                ["pgrep", "-f", main_py],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid == __import__("os").getpid():
                        continue
                    subprocess.run(
                        ["kill", "-TERM", str(pid)],
                        capture_output=True,
                        timeout=10,
                    )
                    stopped.append(pid)
        except (OSError, subprocess.TimeoutExpired):
            pass
    lock = ROOT / "data" / "miner.lock"
    if lock.exists():
        lock.unlink(missing_ok=True)
    return stopped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=2.0, help="Watch duration")
    parser.add_argument("--poll-s", type=float, default=15.0, help="Poll interval")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings(args.config)
    log_path = settings.temp_watch_path
    warn_c = settings.warn_gpu_temp_c
    stop_c = settings.max_gpu_temp_c
    duration_s = max(60.0, args.hours * 3600.0)
    poll_s = max(5.0, args.poll_s)

    monitor = NvidiaMonitor(device_index=0)
    if not monitor.available:
        _log(log_path, "ERROR NVML unavailable — watchdog exiting")
        return 1

    end_at = time.time() + duration_s
    last_ok_log = 0.0
    high_since: float | None = None
    sustained_warn_s = 300.0

    _log(
        log_path,
        f"WATCHDOG START warn={warn_c}C stop={stop_c}C poll={poll_s:.0f}s "
        f"duration={duration_s / 3600:.1f}h",
    )

    try:
        while time.time() < end_at:
            snap = monitor.snapshot()
            now = time.time()
            if snap is None:
                _log(log_path, "WARN NVML snapshot failed")
                time.sleep(poll_s)
                continue

            temp = snap.temperature_c
            detail = (
                f"temp={temp}C util={snap.util_pct}% "
                f"vram={snap.used_mib}/{snap.total_mib}MiB power={snap.power_w:.0f}W"
            )

            if temp >= stop_c:
                _log(log_path, f"CRITICAL {detail} — stopping miner")
                stopped = _stop_miner()
                _log(
                    log_path,
                    f"MINER STOPPED pids={stopped or 'none'} reason=temp>={stop_c}C",
                )
                return 0

            if temp >= warn_c:
                if high_since is None:
                    high_since = now
                hot_for = now - high_since
                _log(log_path, f"HOT {detail} sustained={hot_for:.0f}s")
                if hot_for >= sustained_warn_s:
                    _log(
                        log_path,
                        f"CRITICAL sustained {warn_c}C+ for {hot_for:.0f}s — stopping miner",
                    )
                    stopped = _stop_miner()
                    _log(log_path, f"MINER STOPPED pids={stopped or 'none'} reason=sustained_hot")
                    return 0
            else:
                high_since = None
                if now - last_ok_log >= 300.0:
                    _log(log_path, f"OK {detail}")
                    last_ok_log = now

            time.sleep(poll_s)
    finally:
        monitor.shutdown()

    _log(log_path, "WATCHDOG END normal timeout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())