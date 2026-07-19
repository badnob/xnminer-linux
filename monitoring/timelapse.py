from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from core.models import GpuSnapshot, MiningStats

_SPARK = "▁▂▃▄▅▆▇█"
_HOUR_S = 3600.0


@dataclass
class TimelapseSample:
    elapsed_s: int
    hps: float
    vram_mib: int
    temp_c: int
    pending: int
    accepted: int
    network_ok: bool
    wall_ts: float = 0.0


@dataclass
class TimelapseEvent:
    elapsed_s: int
    clock: str
    label: str


class SessionTimelapse:
    """Rolling session timeline: elapsed time, hourly H/s sparkline, milestones."""

    def __init__(
        self,
        log_path: Path,
        *,
        sample_interval_s: float = 30.0,
        max_samples: int | None = None,
        max_events: int = 10,
        window_s: float = _HOUR_S,
    ) -> None:
        self.log_path = log_path
        self.sample_interval_s = max(1.0, float(sample_interval_s))
        self.window_s = max(self.sample_interval_s, float(window_s))
        # Keep slightly more than one window so resampling has full coverage.
        if max_samples is None:
            max_samples = int(self.window_s / self.sample_interval_s) + 8
        self._started = time.time()
        self._last_sample_at = 0.0
        self._last_network_ok: bool | None = None
        self._online_s = 0.0
        self._offline_s = 0.0
        self._last_state_at = self._started
        self._samples: deque[TimelapseSample] = deque(maxlen=max(16, max_samples))
        self._events: deque[TimelapseEvent] = deque(maxlen=max_events)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._append_log(
            {
                "type": "session_start",
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        )

    def elapsed_s(self) -> int:
        return max(0, int(time.time() - self._started))

    def format_elapsed(self) -> str:
        total = self.elapsed_s()
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _append_log(self, record: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def _track_network(self, network_ok: bool) -> None:
        now = time.time()
        elapsed = now - self._last_state_at
        if self._last_network_ok is not None:
            if self._last_network_ok:
                self._online_s += elapsed
            else:
                self._offline_s += elapsed
            if self._last_network_ok != network_ok:
                label = "NET online" if network_ok else "NET offline"
                self.record_event(label)
        self._last_network_ok = network_ok
        self._last_state_at = now

    def record_event(self, label: str) -> None:
        event = TimelapseEvent(
            elapsed_s=self.elapsed_s(),
            clock=datetime.now().strftime("%H:%M:%S"),
            label=label,
        )
        self._events.appendleft(event)
        self._append_log({"type": "event", **asdict(event)})

    def maybe_sample(
        self,
        stats: MiningStats,
        gpu: GpuSnapshot | None,
        *,
        pending: int,
        network_ok: bool,
    ) -> None:
        self._track_network(network_ok)
        now = time.time()
        if now - self._last_sample_at < self.sample_interval_s:
            return
        self._last_sample_at = now
        sample = TimelapseSample(
            elapsed_s=self.elapsed_s(),
            hps=stats.hps_ema,
            vram_mib=gpu.used_mib if gpu else 0,
            temp_c=gpu.temperature_c if gpu else 0,
            pending=pending,
            accepted=stats.accepted_total,
            network_ok=network_ok,
            wall_ts=now,
        )
        self._samples.append(sample)
        self._append_log({"type": "sample", **asdict(sample)})

    def _sample_wall_ts(self, sample: TimelapseSample) -> float:
        if sample.wall_ts > 0:
            return sample.wall_ts
        return self._started + float(sample.elapsed_s)

    def _window_samples(self, *, now: float | None = None) -> list[TimelapseSample]:
        now = now if now is not None else time.time()
        cutoff = now - self.window_s
        return [
            sample
            for sample in self._samples
            if self._sample_wall_ts(sample) >= cutoff and sample.hps > 0
        ]

    def _bucket_values(
        self,
        samples: list[TimelapseSample],
        *,
        width: int,
        now: float,
    ) -> list[float | None]:
        """Map last-hour samples onto fixed-width timeline (left=oldest, right=now)."""
        if width <= 0:
            return []
        buckets: list[list[float]] = [[] for _ in range(width)]
        window_start = now - self.window_s
        for sample in samples:
            ts = self._sample_wall_ts(sample)
            if ts < window_start:
                continue
            # Rightmost column is the most recent slice of the hour.
            rel = (ts - window_start) / self.window_s
            idx = min(width - 1, max(0, int(rel * width)))
            buckets[idx].append(sample.hps)

        values: list[float | None] = [None] * width
        last: float | None = None
        for i, bucket in enumerate(buckets):
            if bucket:
                last = sum(bucket) / len(bucket)
                values[i] = last
            elif last is not None:
                # Carry forward so early empty buckets after first sample don't gap.
                values[i] = last
        return values

    def sparkline(self, width: int = 48, *, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        samples = self._window_samples(now=now)
        if not samples:
            return " " * width

        values = self._bucket_values(samples, width=width, now=now)
        present = [v for v in values if v is not None]
        if not present:
            return " " * width

        lo = min(present)
        hi = max(present)
        out: list[str] = []
        if hi <= lo:
            for value in values:
                out.append(_SPARK[-1] if value is not None else " ")
            return "".join(out)

        span = hi - lo
        for value in values:
            if value is None:
                out.append(" ")
                continue
            idx = int((value - lo) / span * (len(_SPARK) - 1))
            out.append(_SPARK[idx])
        return "".join(out)

    def average_hps(self, *, now: float | None = None) -> float:
        samples = self._window_samples(now=now)
        if not samples:
            return 0.0
        return sum(s.hps for s in samples) / len(samples)

    def format_uptime_split(self, network_ok: bool) -> str:
        self._track_network(network_ok)
        online = int(self._online_s)
        offline = int(self._offline_s)
        return f"online {self._fmt_duration(online)}  offline {self._fmt_duration(offline)}"

    def _fmt_duration(self, seconds: int) -> str:
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}h{minutes:02d}m"
        if minutes:
            return f"{minutes}m{secs:02d}s"
        return f"{secs}s"

    def event_line(self, max_items: int = 4) -> str:
        if not self._events:
            return "No milestones yet"
        parts = [
            f"{ev.clock} {ev.label}"
            for ev in list(self._events)[:max_items]
        ]
        return " · ".join(parts)

    def finalize(self) -> None:
        if self._last_network_ok is not None:
            now = time.time()
            elapsed = now - self._last_state_at
            if self._last_network_ok:
                self._online_s += elapsed
            else:
                self._offline_s += elapsed
            self._last_state_at = now
        self._append_log(
            {
                "type": "session_end",
                "at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_s": self.elapsed_s(),
                "online_s": int(self._online_s),
                "offline_s": int(self._offline_s),
                "accepted": self._samples[-1].accepted if self._samples else 0,
            }
        )
