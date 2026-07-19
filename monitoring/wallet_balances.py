from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from monitoring.periods import (
    DAY_ROLLOVER_HOUR,
    format_day_label,
    format_week_label,
    mining_day,
    mining_week_start,
    previous_mining_day,
    previous_mining_week_start,
)

XUNI_CONTRACT = "0x999999cf1046e68e36e1aa2e0e07105eddd00002"
XBLK_CONTRACT = "0x999999cf1046e68e36e1aa2e0e07105eddd00001"
WEI_PER_TOKEN = 10**18
DEFAULT_RPC_TIMEOUT_S = 12.0


@dataclass(frozen=True)
class TokenBalances:
    xnm: float
    xuni: float
    xblk: float


@dataclass(frozen=True)
class TokenChange:
    delta: float | None
    pct: float | None


@dataclass(frozen=True)
class BalanceChangeView:
    current: TokenBalances | None
    previous_day: TokenBalances | None
    previous_week: TokenBalances | None
    previous_day_label: str
    previous_week_label: str
    xnm_day: TokenChange
    xuni_day: TokenChange
    xblk_day: TokenChange
    xnm_week: TokenChange
    xuni_week: TokenChange
    xblk_week: TokenChange
    updated_at: float | None
    status: str


def _rpc_call(rpc_url: str, method: str, params: list, timeout_s: float) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
    if "error" in body:
        raise RuntimeError(str(body["error"]))
    result = body.get("result")
    if result is None:
        raise RuntimeError(f"rpc returned no result for {method}")
    return result


def _erc20_balance(rpc_url: str, contract: str, wallet: str, timeout_s: float) -> float:
    data = "0x70a08231" + wallet[2:].lower().rjust(64, "0")
    raw = _rpc_call(rpc_url, "eth_call", [{"to": contract, "data": data}, "latest"], timeout_s)
    return int(raw, 16) / WEI_PER_TOKEN


def _fetch_token(
    label: str,
    fetch: Callable[[], float],
    *,
    fallback: float | None,
) -> tuple[float | None, str | None]:
    try:
        return fetch(), None
    except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
        if fallback is not None:
            return fallback, label
        return None, f"{label}: {exc}"


def fetch_wallet_balances(
    wallet: str,
    *,
    rpc_url: str = "https://xenblocks.io:5556",
    timeout_s: float = DEFAULT_RPC_TIMEOUT_S,
    fallback: TokenBalances | None = None,
) -> tuple[TokenBalances, list[str]]:
    """Fetch balances per token; use fallback values for tokens that time out."""
    fb = fallback
    cached_tokens: list[str] = []

    xnm, err = _fetch_token(
        "XNM",
        lambda: int(_rpc_call(rpc_url, "eth_getBalance", [wallet, "latest"], timeout_s), 16)
        / WEI_PER_TOKEN,
        fallback=fb.xnm if fb else None,
    )
    if err == "XNM":
        cached_tokens.append("XNM")
    elif err:
        raise RuntimeError(err)

    xuni, err = _fetch_token(
        "XUNI",
        lambda: _erc20_balance(rpc_url, XUNI_CONTRACT, wallet, timeout_s),
        fallback=fb.xuni if fb else None,
    )
    if err == "XUNI":
        cached_tokens.append("XUNI")
    elif err:
        raise RuntimeError(err)

    xblk, err = _fetch_token(
        "XBLK",
        lambda: _erc20_balance(rpc_url, XBLK_CONTRACT, wallet, timeout_s),
        fallback=fb.xblk if fb else None,
    )
    if err == "XBLK":
        cached_tokens.append("XBLK")
    elif err:
        raise RuntimeError(err)

    if xnm is None or xuni is None or xblk is None:
        missing = [
            label
            for label, value in (("XNM", xnm), ("XUNI", xuni), ("XBLK", xblk))
            if value is None
        ]
        raise RuntimeError(f"balance fetch failed for {', '.join(missing)}")

    return TokenBalances(xnm=xnm, xuni=xuni, xblk=xblk), cached_tokens


def _balances_to_dict(balances: TokenBalances) -> dict[str, float]:
    return {"xnm": balances.xnm, "xuni": balances.xuni, "xblk": balances.xblk}


def _balances_from_dict(raw: dict) -> TokenBalances:
    return TokenBalances(
        xnm=float(raw.get("xnm", 0.0)),
        xuni=float(raw.get("xuni", 0.0)),
        xblk=float(raw.get("xblk", 0.0)),
    )


def _pct_change(delta: float | None, baseline: float | None) -> float | None:
    if delta is None or baseline is None or baseline == 0:
        return None
    return (delta / baseline) * 100.0


def _token_change(current: float, baseline: float | None) -> TokenChange:
    if baseline is None:
        return TokenChange(delta=None, pct=None)
    delta = current - baseline
    return TokenChange(delta=delta, pct=_pct_change(delta, baseline))


class WalletBalanceTracker:
    """Fetch on-chain balances at launch and daily at 1am; deltas from local snapshots."""

    def __init__(
        self,
        wallet: str,
        history_path: Path,
        *,
        rpc_url: str = "https://xenblocks.io:5556",
        refresh_interval_s: float = 300.0,
    ) -> None:
        self.wallet = wallet
        self.history_path = history_path
        self.rpc_url = rpc_url
        self.refresh_interval_s = refresh_interval_s
        self._lock = threading.Lock()
        self._refreshing = False
        self._current: TokenBalances | None = None
        self._updated_at: float | None = None
        self._last_attempt_at: float | None = None
        self._status = "waiting"
        self._daily_refresh_date: str | None = None
        self._history: dict[str, dict] = {}
        self._load_history()
        self._seed_from_history()

    def _load_history(self) -> None:
        if not self.history_path.is_file():
            return
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            snapshots = raw.get("snapshots", {})
            if isinstance(snapshots, dict):
                self._history = snapshots
        except (OSError, json.JSONDecodeError):
            self._history = {}

    def _latest_snapshot(self) -> TokenBalances | None:
        if not self._history:
            return None
        latest_day = max(self._history)
        return self._snapshot_for_day(latest_day)

    def _seed_from_history(self) -> None:
        today_key = mining_day().isoformat()
        snap = self._snapshot_for_day(today_key) or self._latest_snapshot()
        if snap is None:
            return
        self._current = snap
        self._status = "cached"

    def _save_history(self) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "wallet": self.wallet,
            "snapshots": self._history,
        }
        self.history_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _record_snapshot(
        self,
        balances: TokenBalances,
        *,
        now: datetime | None = None,
    ) -> None:
        day = mining_day(now).isoformat()
        self._history[day] = {
            **_balances_to_dict(balances),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._save_history()

    def _snapshot_for_day(self, day: str | date) -> TokenBalances | None:
        key = day.isoformat() if isinstance(day, date) else day
        raw = self._history.get(key)
        if not isinstance(raw, dict):
            return None
        return _balances_from_dict(raw)

    def refresh_on_launch(self) -> None:
        """Fetch XNM/XUNI/XBLK from RPC when the miner starts."""
        self.maybe_refresh(force=True)
        now = datetime.now()
        if now.hour >= DAY_ROLLOVER_HOUR:
            self._daily_refresh_date = mining_day(now).isoformat()

    def maybe_daily_refresh(self) -> None:
        """While mining continuously, refresh balances once per mining day after 1:00."""
        now = datetime.now()
        if now.hour < DAY_ROLLOVER_HOUR:
            return
        day_key = mining_day(now).isoformat()
        if self._daily_refresh_date == day_key:
            return
        self._daily_refresh_date = day_key
        self.maybe_refresh(force=True)

    def maybe_refresh(self, *, force: bool = False) -> None:
        now = time.time()
        with self._lock:
            stale_due = (
                self._updated_at is None
                and (
                    self._last_attempt_at is None
                    or now - self._last_attempt_at >= self.refresh_interval_s
                )
            )
            fresh_due = (
                self._updated_at is not None
                and now - self._updated_at >= self.refresh_interval_s
            )
            due = force or stale_due or fresh_due
            if not due or self._refreshing:
                return
            self._refreshing = True
            self._last_attempt_at = now

        def worker() -> None:
            fallback = None
            with self._lock:
                fallback = self._current
            try:
                balances, cached_tokens = fetch_wallet_balances(
                    self.wallet,
                    rpc_url=self.rpc_url,
                    fallback=fallback,
                )
            except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
                with self._lock:
                    if self._current is None:
                        self._status = "rpc error"
                    else:
                        self._status = "stale"
                    self._refreshing = False
                return

            status = "ok"
            if cached_tokens:
                status = f"partial ({', '.join(cached_tokens)} cached)"

            with self._lock:
                self._current = balances
                self._updated_at = time.time()
                self._status = status
                self._refreshing = False
            if not cached_tokens:
                self._record_snapshot(balances)

        threading.Thread(target=worker, daemon=True).start()

    def view(self, *, now: datetime | None = None) -> BalanceChangeView:
        now = now or datetime.now()
        with self._lock:
            current = self._current
            updated_at = self._updated_at
            status = self._status

        # Day: always previous mining day (yesterday after 1am). Never reuse an
        # older week snapshot — that made "Day vs Jul 13" match the week column.
        prev_day_target = previous_mining_day(now)
        previous_day = self._snapshot_for_day(prev_day_target)
        day_label = format_day_label(prev_day_target)

        # Week: week-to-date vs this mining week's Monday (1am boundary).
        # Example: Fri Jul 17 → baseline Mon Jul 13.
        week_start = mining_week_start(now)
        previous_week = self._snapshot_for_day(week_start)
        # If Monday has no snap yet, try previous week Monday (true prior week).
        if previous_week is None:
            prior_monday = previous_mining_week_start(now)
            previous_week = self._snapshot_for_day(prior_monday)
            week_label = (
                format_week_label(prior_monday)
                if previous_week is not None
                else format_day_label(week_start)
            )
        else:
            week_label = format_day_label(week_start)

        empty = TokenChange(delta=None, pct=None)
        if current is None:
            return BalanceChangeView(
                current=None,
                previous_day=previous_day,
                previous_week=previous_week,
                previous_day_label=day_label,
                previous_week_label=week_label,
                xnm_day=empty,
                xuni_day=empty,
                xblk_day=empty,
                xnm_week=empty,
                xuni_week=empty,
                xblk_week=empty,
                updated_at=updated_at,
                status=status,
            )

        return BalanceChangeView(
            current=current,
            previous_day=previous_day,
            previous_week=previous_week,
            previous_day_label=day_label,
            previous_week_label=week_label,
            xnm_day=_token_change(
                current.xnm,
                previous_day.xnm if previous_day else None,
            ),
            xuni_day=_token_change(
                current.xuni,
                previous_day.xuni if previous_day else None,
            ),
            xblk_day=_token_change(
                current.xblk,
                previous_day.xblk if previous_day else None,
            ),
            xnm_week=_token_change(
                current.xnm,
                previous_week.xnm if previous_week else None,
            ),
            xuni_week=_token_change(
                current.xuni,
                previous_week.xuni if previous_week else None,
            ),
            xblk_week=_token_change(
                current.xblk,
                previous_week.xblk if previous_week else None,
            ),
            updated_at=updated_at,
            status=status,
        )