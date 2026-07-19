"""Persist CUDA lane cap reductions after GPU temp cooldowns."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class CudaLanePolicyState:
    config_max_lanes: int
    effective_max_lanes: int
    temp_reductions: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def load_lane_policy(path: Path, *, config_max_lanes: int) -> CudaLanePolicyState:
    config_max_lanes = max(1, config_max_lanes)
    if not path.exists():
        return CudaLanePolicyState(
            config_max_lanes=config_max_lanes,
            effective_max_lanes=config_max_lanes,
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        effective = int(raw.get("effective_max_lanes", config_max_lanes))
        effective = max(1, min(config_max_lanes, effective))
        events = raw.get("events", [])
        if not isinstance(events, list):
            events = []
        return CudaLanePolicyState(
            config_max_lanes=config_max_lanes,
            effective_max_lanes=effective,
            temp_reductions=int(raw.get("temp_reductions", 0)),
            events=events[-50:],
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return CudaLanePolicyState(
            config_max_lanes=config_max_lanes,
            effective_max_lanes=config_max_lanes,
        )


def save_lane_policy(path: Path, state: CudaLanePolicyState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config_max_lanes": state.config_max_lanes,
        "effective_max_lanes": state.effective_max_lanes,
        "temp_reductions": state.temp_reductions,
        "events": state.events[-50:],
        "updated_at": _utc_now(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_lane_event(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{_utc_now()}] {message}\n"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def record_temp_lane_reduction(
    policy_path: Path,
    log_path: Path,
    state: CudaLanePolicyState,
    *,
    temperature_c: int,
    difficulty: int,
    lanes_active: int,
    lanes_before: int,
    lanes_after: int,
    reason: str,
) -> CudaLanePolicyState:
    event = {
        "at": _utc_now(),
        "temperature_c": temperature_c,
        "difficulty": difficulty,
        "lanes_active": lanes_active,
        "lanes_cap_before": lanes_before,
        "lanes_cap_after": lanes_after,
        "reason": reason,
        "note": (
            "Low difficulty adds parallel lanes for harvest; extra lanes increased "
            "heat. Lane cap reduced for next restart."
        ),
    }
    state.effective_max_lanes = lanes_after
    state.temp_reductions += 1
    state.events.append(event)
    save_lane_policy(policy_path, state)
    append_lane_event(
        log_path,
        (
            f"TEMP LANE REDUCE difficulty={difficulty} temp={temperature_c}C "
            f"lanes_active={lanes_active} cap {lanes_before}->{lanes_after} "
            f"| {reason}"
        ),
    )
    return state


def restore_lane_cap_if_cool(
    policy_path: Path,
    log_path: Path,
    state: CudaLanePolicyState,
    *,
    temperature_c: int,
    warn_temp_c: int,
    difficulty: int,
    reference_difficulty: int,
) -> tuple[CudaLanePolicyState, bool]:
    """
    Restore the configured lane ceiling after reference difficulty when the GPU
    has cooled, so the next low-difficulty harvest can push all lanes again.
    """
    if difficulty < reference_difficulty:
        return state, False
    if temperature_c >= warn_temp_c - 5:
        return state, False
    if state.effective_max_lanes >= state.config_max_lanes:
        return state, False

    before = state.effective_max_lanes
    state.effective_max_lanes = state.config_max_lanes
    event = {
        "at": _utc_now(),
        "temperature_c": temperature_c,
        "difficulty": difficulty,
        "lanes_cap_before": before,
        "lanes_cap_after": state.config_max_lanes,
        "reason": "reference_difficulty_cool_restore",
        "note": (
            "GPU cooled at reference difficulty — lane cap restored for the "
            "next low-difficulty harvest push."
        ),
    }
    state.events.append(event)
    save_lane_policy(policy_path, state)
    append_lane_event(
        log_path,
        (
            f"LANE CAP RESTORE difficulty={difficulty} temp={temperature_c}C "
            f"cap {before}->{state.config_max_lanes} "
            f"| ready for next harvest push"
        ),
    )
    return state, True


def restore_lane_cap_if_cool(
    policy_path: Path,
    log_path: Path,
    state: CudaLanePolicyState,
    *,
    temperature_c: int,
    warn_temp_c: int,
    difficulty: int,
    reference_difficulty: int,
) -> tuple[CudaLanePolicyState, bool]:
    """
    Restore the configured lane ceiling after reference difficulty when the GPU
    has cooled, so the next low-difficulty harvest can push all lanes again.
    """
    if difficulty < reference_difficulty:
        return state, False
    if temperature_c >= warn_temp_c - 5:
        return state, False
    if state.effective_max_lanes >= state.config_max_lanes:
        return state, False

    before = state.effective_max_lanes
    state.effective_max_lanes = state.config_max_lanes
    event = {
        "at": _utc_now(),
        "temperature_c": temperature_c,
        "difficulty": difficulty,
        "lanes_cap_before": before,
        "lanes_cap_after": state.config_max_lanes,
        "reason": "reference_difficulty_cool_restore",
        "note": (
            "GPU cooled at reference difficulty — lane cap restored for the "
            "next low-difficulty harvest push."
        ),
    }
    state.events.append(event)
    save_lane_policy(policy_path, state)
    append_lane_event(
        log_path,
        (
            f"LANE CAP RESTORE difficulty={difficulty} temp={temperature_c}C "
            f"cap {before}->{state.config_max_lanes} "
            f"| ready for next harvest push"
        ),
    )
    return state, True