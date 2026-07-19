"""
Report accepts + holdings to XenBlockScan (open index).

Design rules:
- Never block mining or startup.
- All HTTP runs on a single daemon worker thread.
- Queue is drop-oldest / drop-new under pressure (never join the miner).
- Optional history backfill is deferred, rate-limited, and off the hot path.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("xenblockscan")

ENABLED = False
ENDPOINT = "http://127.0.0.1:8787/api/v1/events"
API_KEY = ""
REPORT_REJECTS = False
PLUGIN_ID = "tony-xnminer"
PLUGIN_VERSION = "0.3.0"
HOST_PROJECT = "badnob/xnminer"
# Keep short so a down scan never stalls the reporter worker long
TIMEOUT_S = 1.5

_worker: threading.Thread | None = None
_q: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(maxsize=256)
_started = False
_lock = threading.Lock()
_stats = {"posted": 0, "failed": 0, "dropped": 0}
_backfill_thread: threading.Thread | None = None


def configure(
    *,
    enabled: bool,
    endpoint: str = ENDPOINT,
    api_key: str = "",
    report_rejects: bool = False,
    timeout_s: float = TIMEOUT_S,
) -> None:
    global ENABLED, ENDPOINT, API_KEY, REPORT_REJECTS, TIMEOUT_S
    ENABLED = bool(enabled)
    ENDPOINT = (endpoint or ENDPOINT).rstrip("/")
    API_KEY = api_key or ""
    REPORT_REJECTS = bool(report_rejects)
    TIMEOUT_S = max(0.5, min(float(timeout_s), 3.0))
    if ENABLED:
        _ensure_worker()
        log.info("xenblockscan reporter enabled → %s (non-blocking)", _events_url())
    else:
        log.info("xenblockscan reporter disabled")


def stats() -> dict[str, int]:
    return dict(_stats)


def _ensure_worker() -> None:
    global _worker, _started
    with _lock:
        if _started and _worker is not None and _worker.is_alive():
            return
        _started = True
        _worker = threading.Thread(
            target=_worker_loop,
            name="xenblockscan-reporter",
            daemon=True,
        )
        _worker.start()


def _events_url() -> str:
    ep = ENDPOINT
    if ep.endswith("/holdings"):
        return ep[: -len("/holdings")] + "/events"
    if ep.endswith("/events"):
        return ep
    if ep.rstrip("/").endswith("/api/v1"):
        return ep.rstrip("/") + "/events"
    return ep.rstrip("/") + "/api/v1/events"


def _events_batch_url() -> str:
    base = _events_url()
    if base.endswith("/events"):
        return base + ":batch"
    return base.rstrip("/") + "/events:batch"


def _holdings_url() -> str:
    ep = ENDPOINT
    if ep.endswith("/events"):
        return ep[: -len("/events")] + "/holdings"
    if ep.endswith("/holdings"):
        return ep
    if ep.rstrip("/").endswith("/api/v1"):
        return ep.rstrip("/") + "/holdings"
    return ep.rstrip("/") + "/api/v1/holdings"


def report_accepted(
    *,
    account: str,
    kind: str,
    key: str = "",
    hash_to_verify: str = "",
    worker: str = "",
    difficulty: int | None = None,
    occurred_at: datetime | None = None,
) -> None:
    _report_event(
        account=account,
        kind=kind,
        key=key,
        hash_to_verify=hash_to_verify,
        status="accepted",
        worker=worker,
        difficulty=difficulty,
        occurred_at=occurred_at,
    )


def report_rejected(
    *,
    account: str,
    kind: str,
    key: str = "",
    hash_to_verify: str = "",
    worker: str = "",
    difficulty: int | None = None,
    occurred_at: datetime | None = None,
) -> None:
    if not REPORT_REJECTS:
        return
    _report_event(
        account=account,
        kind=kind,
        key=key,
        hash_to_verify=hash_to_verify,
        status="rejected",
        worker=worker,
        difficulty=difficulty,
        occurred_at=occurred_at,
    )


def _trackers_url() -> str:
    ep = ENDPOINT
    if ep.endswith("/events"):
        base = ep[: -len("/events")]
    elif ep.endswith("/holdings"):
        base = ep[: -len("/holdings")]
    elif ep.rstrip("/").endswith("/api/v1"):
        base = ep.rstrip("/")
    else:
        base = ep.rstrip("/") + "/api/v1"
    return base.rstrip("/") + "/trackers/heartbeat"


def report_holdings(
    *,
    account: str,
    worker: str = "",
    xnm: float | None = None,
    xuni: float | None = None,
    xblk: float | None = None,
    blocks: int | None = None,
    super_blocks: int | None = None,
    rank: int | None = None,
    sol_address: str | None = None,
    hashrate: float | None = None,
    tracker_id: str = "",
) -> None:
    """Push balances + live hashrate (H/s) to the open index. Non-blocking."""
    if not ENABLED:
        return
    payload: dict[str, Any] = {
        "schema_version": 1,
        "source": {
            "plugin": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "host_project": HOST_PROJECT,
        },
        "account": account,
    }
    if worker:
        payload["worker"] = worker
    if tracker_id:
        payload["tracker_id"] = tracker_id
    for k, v in (
        ("xnm", xnm),
        ("xuni", xuni),
        ("xblk", xblk),
        ("blocks", blocks),
        ("super_blocks", super_blocks),
        ("rank", rank),
        ("sol_address", sol_address),
        ("hashrate", hashrate),
    ):
        if v is not None:
            payload[k] = v
    _enqueue(_holdings_url(), payload)


def report_tracker(
    *,
    tracker_id: str,
    account: str,
    worker: str = "",
    hashrate: float | None = None,
    accepted: int | None = None,
    rejected: int | None = None,
    found: int | None = None,
    difficulty: int | None = None,
    network_ok: bool | None = None,
) -> None:
    """Fleet heartbeat for website averages (hashrate / accept / reject). Non-blocking."""
    if not ENABLED:
        return
    tid = (tracker_id or "").strip()
    acct = (account or "").strip()
    if not tid or not acct:
        return
    payload: dict[str, Any] = {
        "schema_version": 1,
        "tracker_id": tid,
        "account": acct,
        "source": {
            "plugin": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "host_project": HOST_PROJECT,
        },
    }
    if worker:
        payload["worker"] = worker
    # Always include counters (0 is valid)
    payload["accepted"] = int(accepted or 0)
    payload["rejected"] = int(rejected or 0)
    payload["found"] = int(found or 0)
    if hashrate is not None and float(hashrate) > 0:
        payload["hashrate"] = float(hashrate)
    if difficulty is not None:
        try:
            payload["difficulty"] = float(difficulty)
        except (TypeError, ValueError):
            pass
    if network_ok is not None:
        payload["network_ok"] = bool(network_ok)
    _enqueue(_trackers_url(), payload)


def _report_event(
    *,
    account: str,
    kind: str,
    key: str,
    hash_to_verify: str,
    status: str,
    worker: str,
    difficulty: int | None,
    occurred_at: datetime | None,
) -> None:
    if not ENABLED:
        return
    when = occurred_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    kind_u = (kind or "").upper() or "XNM"
    if kind_u not in ("XNM", "XUNI", "XBLK"):
        if "XUNI" in kind_u:
            kind_u = "XUNI"
        elif "XBLK" in kind_u or "SUPER" in kind_u:
            kind_u = "XBLK"
        else:
            kind_u = "XNM"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "source": {
            "plugin": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "host_project": HOST_PROJECT,
        },
        "account": account,
        "event": {
            "type": "block_submit",
            "status": status,
            "kind": kind_u,
            "key": key,
            "hash_to_verify": hash_to_verify,
            "occurred_at": when.isoformat(),
        },
    }
    if worker:
        payload["worker"] = worker
    if difficulty is not None:
        payload["meta"] = {"difficulty": difficulty}
    _enqueue(_events_url(), payload)


def _enqueue(url: str, payload: dict[str, Any]) -> None:
    """Non-blocking enqueue only — never wait on the miner thread."""
    if not ENABLED:
        return
    _ensure_worker()
    item = (url, payload)
    try:
        _q.put_nowait(item)
    except queue.Full:
        # Drop one old job so live accepts still have a chance
        try:
            _q.get_nowait()
            _q.task_done()
        except queue.Empty:
            pass
        try:
            _q.put_nowait(item)
        except queue.Full:
            _stats["dropped"] += 1


def _worker_loop() -> None:
    while True:
        item = _q.get()
        if item is None:
            return
        url, payload = item
        try:
            _post(url, payload)
            _stats["posted"] += 1
        except Exception as exc:
            _stats["failed"] += 1
            # Throttle log spam when scan is offline
            if _stats["failed"] <= 3 or _stats["failed"] % 25 == 0:
                log.warning("xenblockscan post failed: %s", exc)
        finally:
            _q.task_done()
            # Tiny pace so we never flood localhost while mining
            time.sleep(0.02)


def _post(url: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"xenblockscan-plugin/{PLUGIN_ID}/{PLUGIN_VERSION}",
    }
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        resp.read()


def _event_payload(
    *,
    account: str,
    kind: str,
    key: str,
    hash_to_verify: str,
    worker: str,
    occurred_at: datetime | None,
) -> dict[str, Any]:
    when = occurred_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    kind_u = (kind or "XNM").upper()
    if kind_u not in ("XNM", "XUNI", "XBLK"):
        kind_u = "XNM"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "source": {
            "plugin": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "host_project": HOST_PROJECT,
        },
        "account": account,
        "event": {
            "type": "block_submit",
            "status": "accepted",
            "kind": kind_u,
            "key": key,
            "hash_to_verify": hash_to_verify,
            "occurred_at": when.isoformat(),
        },
    }
    if worker:
        payload["worker"] = worker
    return payload


def schedule_backfill(
    rows: list[dict[str, Any]],
    *,
    account: str,
    worker: str = "",
    max_rows: int = 200,
    delay_s: float = 45.0,
) -> None:
    """
    Optional history push — always async.
    Defaults to a small recent batch of special finds only, after mining is up.
    """
    global _backfill_thread
    if not ENABLED or not account or not rows:
        return
    with _lock:
        if _backfill_thread is not None and _backfill_thread.is_alive():
            return
        _backfill_thread = threading.Thread(
            target=_backfill_worker,
            kwargs={
                "rows": list(rows),
                "account": account,
                "worker": worker,
                "max_rows": max(0, int(max_rows)),
                "delay_s": max(5.0, float(delay_s)),
            },
            name="xenblockscan-backfill",
            daemon=True,
        )
        _backfill_thread.start()


def _backfill_worker(
    *,
    rows: list[dict[str, Any]],
    account: str,
    worker: str,
    max_rows: int,
    delay_s: float,
) -> None:
    time.sleep(delay_s)
    if not ENABLED:
        return
    # Prefer XUNI/XBLK; keep payload tiny so miner never feels it
    rank = {"XBLK": 0, "XUNI": 1, "XNM": 2}
    ordered = sorted(
        rows,
        key=lambda r: rank.get(
            str(r.get("block_type") or r.get("kind") or "XNM").upper(), 9
        ),
    )[:max_rows]
    n = 0
    for row in ordered:
        if not ENABLED:
            return
        key = str(row.get("key") or "")
        if not key:
            continue
        kind = (row.get("block_type") or row.get("kind") or "XNM").upper()
        occurred = row.get("submitted_at") or row.get("occurred_at")
        when = None
        if occurred:
            try:
                when = datetime.fromisoformat(str(occurred).replace("Z", "+00:00"))
            except ValueError:
                when = None
        payload = _event_payload(
            account=account,
            kind=kind,
            key=key,
            hash_to_verify=str(row.get("hash_str") or row.get("hash_to_verify") or ""),
            worker=worker,
            occurred_at=when,
        )
        _enqueue(_events_url(), payload)
        n += 1
        # Pace enqueue so worker stays quiet
        time.sleep(0.05)
    if n:
        log.info("xenblockscan deferred backfill enqueued %s event(s)", n)


# Keep name for older callers; never blocks
def backfill_accepted(
    rows: list[dict[str, Any]],
    *,
    account: str,
    worker: str = "",
    prioritize_special: bool = True,
    max_rows: int | None = None,
    batch_size: int = 50,
) -> int:
    schedule_backfill(
        rows,
        account=account,
        worker=worker,
        max_rows=max_rows if max_rows is not None else 200,
        delay_s=45.0,
    )
    return 0


def flush_queue(*, timeout_s: float = 0.0) -> None:
    """No-op for startup. Never wait on the miner path."""
    return
