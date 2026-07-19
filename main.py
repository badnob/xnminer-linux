#!/usr/bin/env python3
"""Xenblocks Python miner — modular entry point."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings, load_settings
from config.wallet_setup import ensure_wallet_configured
from core.supervisor import Supervisor
from debug.diagnostics import run_diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Xenblocks modular Python miner")
    parser.add_argument("--config", type=Path, default=None, help="Path to miner.ini")
    parser.add_argument("--diagnose", action="store_true", help="Run checks and exit")
    parser.add_argument(
        "--backend",
        choices=["cpu", "cuda", "gpu"],
        help="cpu=from-scratch Python | cuda=native GPU | gpu=legacy binary bridge",
    )
    parser.add_argument("--strategy", help="Override key strategy (cpu backend)")
    parser.add_argument("--lanes", type=int, help="Override CPU lane count")
    parser.add_argument("--max-seconds", type=int, default=None, help="Stop after N seconds")
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Classic scrolling log output instead of live dashboard",
    )
    args = parser.parse_args()

    ensure_wallet_configured(args.config, interactive=not args.diagnose)
    settings = load_settings(args.config)
    overrides: dict = {}
    if args.backend:
        overrides["backend"] = args.backend
    if args.strategy:
        overrides["strategy"] = args.strategy
    if args.lanes:
        overrides["cpu_lanes"] = args.lanes
    if overrides:
        settings = replace(settings, **overrides)

    if args.diagnose:
        import json
        print(json.dumps(run_diagnostics(settings), indent=2))
        return 0

    use_dashboard = settings.dashboard_enabled and not args.no_dashboard
    supervisor = Supervisor(settings, use_dashboard=use_dashboard)
    if not supervisor.startup_checks():
        return 1
    supervisor.run(max_seconds=args.max_seconds, skip_startup_checks=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())