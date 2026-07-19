from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from block_queue.policy import in_xuni_window

_BAR = "█"
_EMPTY = "░"
_HOURLY_RETENTION_H = 48
_OUTAGE_RETENTION_D = 14
_MAX_OUTAGES = 200
_DISPLAY_HOURS = 6


@dataclass(frozen=True)
class HourlyUptimeRow:
    label: str
    uptime_pct: float | None
    offline_s: float
    outage_count: int


@dataclass(frozen=True)
class ServerUptimeView:
    hours: list[HourlyUptimeRow]
    avg_outage_s: float | None
    outage_count: int
    xuni_outage_count: int
    current_ok: bool
    current_outage_s: float | None
    offline_s_24h: float


def _hour_key(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H")


def _outage_overlaps_xuni(start_ts: float, end_ts: float) -> bool:
    start = datetime.fromtimestamp(start_ts).replace(second=0, microsecond=0)
    end = datetime.fromtimestamp(end_ts)
    cursor = start
    while cursor <= end:
        if in_xuni_window(cursor):
            return True
        cursor += timedelta(minutes=1)
    return False


def _fmt_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class ServerUptimeTracker:
    """Track hourly server uptime from /difficulty probes; persist across sessions."""

    def __init__(self, history_path: Path) -> None:
        self.history_path = history_path
        self._lock = threading.Lock()
        self._hourly: dict[str, dict[str, float]] = {}
        self._outages: list[dict] = []
        self._last_ok: bool | None = None
        self._last_probe_at: float | None = None
        self._outage_start: float | None = None
        self._dirty = False
        self._load()

    def _empty_bucket(self) -> dict[str, float]:
        return {"online_s": 0.0, "offline_s": 0.0}

    def _load(self) -> None:
        if not self.history_path.is_file():
            return
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        hourly = raw.get("hourly", {})
        outages = raw.get("outages", [])
        if isinstance(hourly, dict):
            self._hourly = {
                key: {
                    "online_s": float(value.get("online_s", 0.0)),
                    "offline_s": float(value.get("offline_s", 0.0)),
                }
                for key, value in hourly.items()
                if isinstance(value, dict)
            }
        if isinstance(outages, list):
            self._outages = [item for item in outages if isinstance(item, dict)]

        if "last_ok" in raw and raw["last_ok"] is not None:
            self._last_ok = bool(raw["last_ok"])
        if "last_probe_at" in raw and raw["last_probe_at"] is not None:
            try:
                self._last_probe_at = float(raw["last_probe_at"])
            except (TypeError, ValueError):
                self._last_probe_at = None
        if "outage_start" in raw and raw["outage_start"] is not None:
            try:
                self._outage_start = float(raw["outage_start"])
            except (TypeError, ValueError):
                self._outage_start = None

    def _prune(self) -> None:
        cutoff = datetime.now() - timedelta(hours=_HOURLY_RETENTION_H)
        cutoff_key = cutoff.strftime("%Y-%m-%dT%H")
        self._hourly = {
            key: value for key, value in self._hourly.items() if key >= cutoff_key
        }
        outage_cutoff = time.time() - (_OUTAGE_RETENTION_D * 86400)
        self._outages = [
            item
            for item in self._outages
            if float(item.get("end_ts", 0.0)) >= outage_cutoff
        ][-_MAX_OUTAGES:]

    def _save(self) -> None:
        self._prune()
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "hourly": self._hourly,
            "outages": self._outages,
            "last_ok": self._last_ok,
            "last_probe_at": self._last_probe_at,
            "outage_start": self._outage_start,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.history_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._dirty = False

    def _maybe_save(self) -> None:
        if self._dirty:
            self._save()

    def _add_duration(self, start_ts: float, end_ts: float, online: bool) -> None:
        if end_ts <= start_ts:
            return
        cursor = start_ts
        field = "online_s" if online else "offline_s"
        while cursor < end_ts:
            hour_start = datetime.fromtimestamp(cursor).replace(
                minute=0, second=0, microsecond=0
            )
            next_hour = hour_start + timedelta(hours=1)
            chunk_end = min(end_ts, next_hour.timestamp())
            key = hour_start.strftime("%Y-%m-%dT%H")
            bucket = self._hourly.setdefault(key, self._empty_bucket())
            bucket[field] += chunk_end - cursor
            cursor = chunk_end
        self._dirty = True

    def _start_outage(self, ts: float) -> None:
        self._outage_start = ts
        self._dirty = True

    def _close_outage(self, ts: float) -> None:
        if self._outage_start is None:
            return
        start_ts = self._outage_start
        duration_s = max(0.0, ts - start_ts)
        if duration_s > 0:
            self._outages.append(
                {
                    "start_ts": start_ts,
                    "end_ts": ts,
                    "duration_s": duration_s,
                    "during_xuni_window": _outage_overlaps_xuni(start_ts, ts),
                }
            )
            self._dirty = True
        self._outage_start = None
        self._dirty = True

    def record_probe(self, ok: bool, *, ts: float | None = None) -> None:
        """Record a server health probe (typically every /difficulty check)."""
        now = ts if ts is not None else time.time()
        with self._lock:
            if self._last_probe_at is not None and self._last_ok is not None:
                self._add_duration(self._last_probe_at, now, self._last_ok)

            if self._last_ok is None:
                if not ok:
                    self._start_outage(now)
            elif self._last_ok and not ok:
                self._start_outage(now)
            elif not self._last_ok and ok:
                self._close_outage(now)

            self._last_ok = ok
            self._last_probe_at = now
            self._dirty = True
            self._maybe_save()

    def _projected_hourly(
        self,
        hourly: dict[str, dict[str, float]],
        *,
        now: float,
        last_ok: bool | None,
        last_probe_at: float | None,
    ) -> dict[str, dict[str, float]]:
        """Copy buckets and attribute open interval since last probe for live view."""
        projected = {
            key: {
                "online_s": value["online_s"],
                "offline_s": value["offline_s"],
            }
            for key, value in hourly.items()
        }
        if last_probe_at is None or last_ok is None or now <= last_probe_at:
            return projected

        cursor = last_probe_at
        field = "online_s" if last_ok else "offline_s"
        while cursor < now:
            hour_start = datetime.fromtimestamp(cursor).replace(
                minute=0, second=0, microsecond=0
            )
            next_hour = hour_start + timedelta(hours=1)
            chunk_end = min(now, next_hour.timestamp())
            key = hour_start.strftime("%Y-%m-%dT%H")
            bucket = projected.setdefault(key, self._empty_bucket())
            bucket[field] += chunk_end - cursor
            cursor = chunk_end
        return projected

    def view(self, *, now: float | None = None) -> ServerUptimeView:
        now = now if now is not None else time.time()
        with self._lock:
            current_ok = bool(self._last_ok) if self._last_ok is not None else False
            outage_start = self._outage_start
            last_ok = self._last_ok
            last_probe_at = self._last_probe_at
            hourly = self._projected_hourly(
                self._hourly,
                now=now,
                last_ok=last_ok,
                last_probe_at=last_probe_at,
            )
            outages = list(self._outages)

        current_outage_s = None
        if not current_ok and outage_start is not None:
            current_outage_s = max(0.0, now - outage_start)
        elif not current_ok and last_probe_at is not None and last_ok is False:
            current_outage_s = max(0.0, now - last_probe_at)

        rows: list[HourlyUptimeRow] = []
        offline_24h = 0.0
        # Full 24h offline total for summary, but only render recent hours.
        window_start_24h = datetime.fromtimestamp(now) - timedelta(hours=23)
        window_start_24h = window_start_24h.replace(
            minute=0, second=0, microsecond=0
        )
        for offset in range(24):
            hour_dt = window_start_24h + timedelta(hours=offset)
            key = hour_dt.strftime("%Y-%m-%dT%H")
            bucket = hourly.get(key, self._empty_bucket())
            offline_24h += bucket["offline_s"]

        # Newest hour first (current running hour at top), last N hours only.
        current_hour = datetime.fromtimestamp(now).replace(
            minute=0, second=0, microsecond=0
        )
        for offset in range(_DISPLAY_HOURS):
            hour_dt = current_hour - timedelta(hours=offset)
            key = hour_dt.strftime("%Y-%m-%dT%H")
            bucket = hourly.get(key, self._empty_bucket())
            online_s = bucket["online_s"]
            offline_s = bucket["offline_s"]
            total_s = online_s + offline_s

            hour_outages = sum(
                1
                for item in outages
                if _hour_key(float(item.get("start_ts", 0.0))) == key
            )

            uptime_pct = None
            if total_s > 0:
                uptime_pct = (online_s / total_s) * 100.0

            if hour_dt.date() == date.today():
                label = hour_dt.strftime("%H:00")
            else:
                label = hour_dt.strftime("%b %d %H:00")
            if offset == 0:
                label = f"{label}*"

            rows.append(
                HourlyUptimeRow(
                    label=label,
                    uptime_pct=uptime_pct,
                    offline_s=offline_s,
                    outage_count=hour_outages,
                )
            )

        completed = [float(item.get("duration_s", 0.0)) for item in outages if item]
        avg_outage_s = (sum(completed) / len(completed)) if completed else None
        xuni_outage_count = sum(
            1 for item in outages if bool(item.get("during_xuni_window"))
        )

        return ServerUptimeView(
            hours=rows,
            avg_outage_s=avg_outage_s,
            outage_count=len(outages),
            xuni_outage_count=xuni_outage_count,
            current_ok=current_ok,
            current_outage_s=current_outage_s,
            offline_s_24h=offline_24h,
        )


def format_duration(seconds: float) -> str:
    return _fmt_duration(seconds)
