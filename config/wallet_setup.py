from __future__ import annotations

import re
import shutil
import sys
import uuid
from pathlib import Path

from config.settings import DEFAULT_INI, ROOT

_ETH_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_PLACEHOLDERS = {
    "",
    "0x",
    "your_wallet_address",
    "your_wallet_here",
    "changeme",
    "replace_me",
}
# Shared default display names that would clash on Woodyminer / XenBlockScan.
_NAME_PLACEHOLDERS = {
    "",
    "miner",
    "miner1",
    "miner2",
    "worker",
    "worker1",
    "default",
    "changeme",
    "replace_me",
    "yourname",
    "your_name",
    "your-name",
    "xenblockscan",
    "xnminer",
}
EXAMPLE_INI = ROOT / "miner.ini.example"


def generate_random_miner_name() -> str:
    """Unique display name per install (Woodyminer / XenBlockScan worker label)."""
    return f"xnminer-{uuid.uuid4().hex[:8]}"


def is_placeholder_miner_name(name: str) -> bool:
    return name.strip().lower() in _NAME_PLACEHOLDERS


def is_valid_eth_address(address: str) -> bool:
    return bool(_ETH_RE.match(address.strip()))


def normalize_eth_address(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if not value.lower().startswith("0x"):
        value = f"0x{value}"
    return value


def needs_wallet_setup(address: str) -> bool:
    normalized = normalize_eth_address(address)
    if not normalized:
        return True
    if normalized.lower() in _PLACEHOLDERS:
        return True
    return not is_valid_eth_address(normalized)


def _set_ini_value(path: Path, section: str, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_section = False
    found = False
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped[1:-1].strip().lower() == section.lower()
            out.append(line)
            continue
        if in_section and re.match(rf"^{re.escape(key)}\s*=", stripped, re.IGNORECASE):
            out.append(f"{key} = {value}")
            found = True
            continue
        out.append(line)

    if not found:
        if not any(l.strip().lower() == f"[{section.lower()}]" for l in lines):
            if out and out[-1].strip():
                out.append("")
            out.append(f"[{section}]")
        out.append(f"{key} = {value}")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def ensure_miner_ini_exists(ini_path: Path | None = None) -> Path:
    """Create miner.ini from the example template if it does not exist yet."""
    path = ini_path or DEFAULT_INI
    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    if EXAMPLE_INI.is_file():
        shutil.copy2(EXAMPLE_INI, path)
        return path

    # Minimal fallback if example was not shipped.
    path.write_text(
        "\n".join(
            [
                "[account]",
                "address =",
                "worker =",
                "",
                "[server]",
                "base_url = http://xenblocks.io",
                "connection_timeout_s = 20",
                "network_poll_interval_s = 15",
                "network_poll_timeout_s = 3",
                "network_down_poll_interval_s = 30",
                "",
                "[mining]",
                "backend = cuda",
                "strategy = random",
                "memory_cost = 1100",
                "time_cost = 1",
                "parallelism = 1",
                "hash_len = 64",
                "",
                "[efficiency]",
                "target_vram_pct = 69.09",
                "desktop_headroom_pct = 25.12",
                "emergency_vram_pct = 92.78",
                "min_headroom_pct = 3.68",
                "runtime_overhead_pct = 6.28",
                "min_headroom_floor_mib = 512",
                "runtime_overhead_floor_mib = 256",
                "target_vram_mib = 0",
                "headroom_mib = 0",
                "emergency_vram_mib = 0",
                "min_headroom_mib = 0",
                "max_gpu_temp_c = 75",
                "warn_gpu_temp_c = 72",
                "gpu_cooldown_s = 60",
                "gpu_power_boost_enabled = true",
                "gpu_power_target_pct = 100",
                "gpu_windows_performance_mode = false",
                "cpu_lanes = 2",
                "lane_ramp_step = 1",
                "sample_interval_s = 5",
                "",
                "[queue]",
                "db_path = data/blocks.db",
                "jsonl_path = data/queue.jsonl",
                "rejected_jsonl_path = data/rejected.jsonl",
                "submit_cpu_fraction = 0.30",
                "",
                "[monitoring]",
                "log_path = data/session.log",
                "timelapse_path = data/session_timelapse.jsonl",
                "stats_interval_s = 4",
                "timelapse_sample_s = 30",
                "temp_watch_path = data/temp_watch.log",
                "dashboard_enabled = true",
                "woodyminer_enabled = true",
                "woodyminer_upload_url = https://woodyminer.com/api/stat/upload",
                "woodyminer_upload_period_s = 60",
                "woodyminer_custom_name =",
                "",
                "[gpu]",
                "xenblocks_exe =",
                "xenblocks_db =",
                "enabled = false",
                "",
                "[cuda]",
                "dll_path = native/build/bin/libxen_cuda.so",
                "batch_size = 0",
                "max_batch_size = 0",
                "max_lanes = 4",
                "lane_reserve = 1",
                "runtime_overhead_mib = 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def save_wallet_to_ini(
    ini_path: Path,
    address: str,
    worker: str | None = None,
) -> None:
    ini_path.parent.mkdir(parents=True, exist_ok=True)
    if not ini_path.exists():
        ensure_miner_ini_exists(ini_path)

    _set_ini_value(ini_path, "account", "address", address)
    if worker:
        _set_ini_value(ini_path, "account", "worker", worker)


def _read_ini_value(ini_path: Path, section: str, key: str) -> str:
    if not ini_path.is_file():
        return ""
    in_section = False
    for line in ini_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped[1:-1].strip().lower() == section.lower()
            continue
        if in_section and re.match(rf"^{re.escape(key)}\s*=", stripped, re.IGNORECASE):
            return stripped.split("=", 1)[1].strip()
    return ""


def _ensure_worker_id(ini_path: Path) -> str:
    """Ensure [account] worker + Woodyminer custom name are unique per install.

    Empty or shared placeholders (e.g. miner1) are replaced with a random
    ``xnminer-xxxxxxxx`` name so fleet / leaderboard data does not clash.
    Intentional custom names are left alone.
    """
    worker = _read_ini_value(ini_path, "account", "worker")
    custom = _read_ini_value(ini_path, "monitoring", "woodyminer_custom_name")

    need_worker = is_placeholder_miner_name(worker)
    need_custom = is_placeholder_miner_name(custom)

    if not need_worker and not need_custom:
        # Custom left blank on purpose → settings falls back to worker.
        if not custom.strip() and worker.strip():
            return worker.strip()
        return (custom or worker).strip()

    if not need_worker:
        name = worker.strip()
    elif not need_custom and custom.strip():
        name = custom.strip()
    else:
        name = generate_random_miner_name()

    if need_worker:
        _set_ini_value(ini_path, "account", "worker", name)
    # Always persist a concrete custom name when it was empty/placeholder so
    # Woodyminer and XenBlockScan never share a generic default label.
    if need_custom:
        _set_ini_value(ini_path, "monitoring", "woodyminer_custom_name", name)
    return name


_WORKER_NAME_RE = re.compile(r"^[\w .\-@#+]{1,64}$", re.UNICODE)


def is_valid_worker_name(name: str) -> bool:
    """True if name is a usable custom worker label (not empty / placeholder)."""
    value = name.strip()
    if not value or is_placeholder_miner_name(value):
        return False
    if len(value) > 64:
        return False
    return bool(_WORKER_NAME_RE.match(value))


def prompt_for_wallet() -> str:
    print()
    print("=" * 40)
    print("  First-time setup - Xenblocks Miner")
    print("=" * 40)
    print()
    print("Enter your Ethereum / EVM wallet address (0x + 40 hex characters).")
    print("It is saved to miner.ini and used as your mining account.")
    print()

    while True:
        try:
            raw = input("Wallet address: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit("Wallet setup cancelled.")

        address = normalize_eth_address(raw)
        if is_valid_eth_address(address):
            return address

        print("Invalid address. Example: 0x1234567890abcdef1234567890abcdef12345678")
        print()


def prompt_for_worker_name() -> str | None:
    """Ask for a worker / miner display name.

    Returns a custom name, or ``None`` when the user leaves it empty so a
    unique ``xnminer-xxxxxxxx`` name is generated.
    """
    print()
    print("Worker / miner name (shown on Woodyminer / XenBlockScan).")
    print("Leave empty to auto-generate a unique name (e.g. xnminer-a1b2c3d4).")
    print()

    while True:
        try:
            raw = input("Worker name [auto]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit("Worker name setup cancelled.")

        if not raw:
            return None

        if is_placeholder_miner_name(raw):
            print(
                f'  "{raw}" is too generic and would clash with other miners. '
                "Pick something unique, or leave empty for auto."
            )
            print()
            continue

        if len(raw) > 64:
            print("  Name is too long (max 64 characters).")
            print()
            continue

        if not _WORKER_NAME_RE.match(raw):
            print(
                "  Use letters, numbers, spaces, and . _ - @ # + only "
                "(max 64 characters)."
            )
            print()
            continue

        return raw


def apply_worker_name(ini_path: Path, worker: str | None) -> str:
    """Persist a custom worker name, or generate a unique one if ``worker`` is None."""
    if worker and is_valid_worker_name(worker):
        name = worker.strip()
        _set_ini_value(ini_path, "account", "worker", name)
        _set_ini_value(ini_path, "monitoring", "woodyminer_custom_name", name)
        return name
    return _ensure_worker_id(ini_path)


def ensure_wallet_configured(
    ini_path: Path | None = None,
    *,
    interactive: bool = True,
) -> Path:
    """Ensure miner.ini exists and has a valid wallet address (+ worker name)."""
    path = ensure_miner_ini_exists(ini_path or DEFAULT_INI)

    import configparser

    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    address = cp.get("account", "address", fallback="").strip()
    worker_existing = cp.get("account", "worker", fallback="").strip()
    can_prompt = interactive and sys.stdin.isatty()

    if not needs_wallet_setup(address):
        # Wallet already set — only prompt for worker if still empty/placeholder.
        if can_prompt and is_placeholder_miner_name(worker_existing):
            chosen = prompt_for_worker_name()
            miner_name = apply_worker_name(path, chosen)
            print()
            if chosen:
                print(f"Worker name saved: {miner_name}")
            else:
                print(f"Worker name auto-generated: {miner_name}")
            print()
        else:
            _ensure_worker_id(path)
        return path

    if not can_prompt:
        print(
            "ERROR: Wallet not configured. Set [account] address in miner.ini "
            "or run the miner interactively once to enter it.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    address = prompt_for_wallet()
    chosen = prompt_for_worker_name()
    save_wallet_to_ini(path, address, worker=chosen if chosen else None)
    miner_name = apply_worker_name(path, chosen)
    print()
    print(f"Wallet saved to {path}")
    if chosen:
        print(f"Worker name: {miner_name}")
    else:
        print(
            f"Worker name: {miner_name}  "
            "(auto-generated; edit miner.ini to change)"
        )
    print("You can edit miner.ini later to change wallet, worker name, or backend.")
    print()
    return path
