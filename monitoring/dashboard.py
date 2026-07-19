from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config.settings import Settings
from core.models import GpuSnapshot, MiningStats
from monitoring.dashboard_stats import (
    accepted_tokens_by_kind,
    dashboard_rows,
    row_total,
)
from monitoring.timelapse import SessionTimelapse
from monitoring.local_stats import LocalMiningStatsTracker, TokenChange
from monitoring.rewards import current_reward_summary, reward_era_label
from monitoring.server_uptime import ServerUptimeTracker, format_duration
from monitoring.wallet_balances import WalletBalanceTracker

_TOKEN_STYLES = {"XUNI": "yellow", "XNM": "green", "XBLK": "red"}
_UP_BAR = "█"
_UP_EMPTY = "░"


@dataclass
class DashboardEvent:
    ts: str
    action: str
    block: str
    detail: str


def _enable_windows_vt() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


class MinerDashboard:
    """Fixed full-screen live console UI (alternate buffer; resize-safe)."""

    def __init__(self, settings: Settings) -> None:
        _enable_windows_vt()
        self.settings = settings
        # soft_wrap avoids long lines pushing the viewport sideways on resize.
        self.console = Console(force_terminal=True, soft_wrap=True)
        self._live: Live | None = None
        self._events: deque[DashboardEvent] = deque(maxlen=6)
        self._status = "Starting..."
        self._network_ok = False
        self._difficulty: int | None = None
        self._cuda_batch = 0
        self._cuda_lanes = 1
        self._gpu: GpuSnapshot | None = None
        self._stats = MiningStats()
        self._pending_by_type: dict[str, int] = {"XUNI": 0, "XNM": 0, "XBLK": 0}
        self._resubmission_by_type: dict[str, int] = {"XUNI": 0, "XNM": 0, "XBLK": 0}
        self._timelapse: SessionTimelapse | None = None
        self._local_stats: LocalMiningStatsTracker | None = None
        self._wallet_balances: WalletBalanceTracker | None = None
        self._server_uptime: ServerUptimeTracker | None = None
        self._last_refresh_at = 0.0
        self._min_refresh_s = 0.5
        self._last_size: tuple[int, int] | None = None

    def start(self) -> None:
        # screen=True uses the terminal alternate buffer so resize/move does not
        # pile old dashboard frames into scrollback (the "scroll to find it" mess).
        self._live = Live(
            self.render(),
            console=self.console,
            refresh_per_second=2,
            transient=True,
            screen=True,
            redirect_stderr=False,
            redirect_stdout=False,
            vertical_overflow="crop",
        )
        self._last_size = self._console_size()
        self._live.start()

    def stop(self) -> None:
        if self._live:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None
        self._last_size = None

    def _console_size(self) -> tuple[int, int]:
        size = self.console.size
        return int(size.width), int(size.height)

    def set_status(self, msg: str) -> None:
        self._status = msg
        self._refresh(force=True)

    def set_network(self, ok: bool, difficulty: int | None) -> None:
        if self._network_ok == ok and self._difficulty == difficulty:
            return
        self._network_ok = ok
        self._difficulty = difficulty
        self._refresh()

    def set_cuda_batch(self, batch: int, lanes: int = 1) -> None:
        lanes = max(1, lanes)
        if self._cuda_batch == batch and self._cuda_lanes == lanes:
            return
        self._cuda_batch = batch
        self._cuda_lanes = lanes

    def set_timelapse(self, timelapse: SessionTimelapse) -> None:
        self._timelapse = timelapse

    def set_local_stats(self, tracker: LocalMiningStatsTracker | None) -> None:
        self._local_stats = tracker

    def set_wallet_balances(self, tracker: WalletBalanceTracker | None) -> None:
        self._wallet_balances = tracker

    def set_server_uptime(self, tracker: ServerUptimeTracker | None) -> None:
        self._server_uptime = tracker

    def update(
        self,
        stats: MiningStats,
        gpu: GpuSnapshot | None,
        pending_by_type: dict[str, int] | None = None,
        resubmission_by_type: dict[str, int] | None = None,
    ) -> None:
        self._stats = stats
        self._gpu = gpu
        if pending_by_type is not None:
            self._pending_by_type = pending_by_type
        if resubmission_by_type is not None:
            self._resubmission_by_type = resubmission_by_type
        self._refresh()

    def event(self, action: str, block: str, detail: str = "") -> None:
        self._events.appendleft(
            DashboardEvent(
                ts=datetime.now().strftime("%H:%M:%S"),
                action=action,
                block=block,
                detail=detail,
            )
        )
        self._refresh(force=True)

    def _refresh(self, *, force: bool = False) -> None:
        if not self._live:
            return
        size = self._console_size()
        if self._last_size != size:
            # Window was resized/moved — force a full redraw against the new size.
            self._last_size = size
            force = True
        now = time.time()
        if not force and now - self._last_refresh_at < self._min_refresh_s:
            return
        self._last_refresh_at = now
        # refresh=True on size changes so the alternate buffer repaints immediately.
        self._live.update(self.render(), refresh=force)

    def _styled_count(self, value: int | float, style: str) -> Text:
        if isinstance(value, float) and abs(value - round(value)) >= 0.001:
            return Text(f"{value:,.2f}", style=style)
        return Text(f"{int(round(value)):,}", style=style)

    def _styled_token_amount(self, value: float, style: str) -> Text:
        """Format a token amount (may be fractional after halving, e.g. 2.5)."""
        if abs(value - round(value)) < 0.001:
            return Text(f"{int(round(value)):,}", style=style)
        if abs(value * 2 - round(value * 2)) < 0.001:
            return Text(f"{value:,.1f}", style=style)
        return Text(f"{value:,.2f}", style=style)

    def _format_delta(self, value: float | None) -> Text:
        if value is None:
            return Text("—", style="dim")
        if abs(value - round(value)) < 0.001:
            rounded = int(round(value))
            if rounded > 0:
                return Text(f"+{rounded:,}", style="bold green")
            if rounded < 0:
                return Text(f"{rounded:,}", style="bold red")
            return Text("0", style="dim")
        if value > 0:
            return Text(f"+{value:,.2f}", style="bold green")
        if value < 0:
            return Text(f"{value:,.2f}", style="bold red")
        return Text("0.00", style="dim")

    def _format_balance(self, value: float) -> Text:
        if value >= 1000 or abs(value - round(value)) < 0.001:
            return Text(f"{value:,.0f}", style="bold white")
        if value >= 1:
            return Text(f"{value:,.2f}", style="bold white")
        return Text(f"{value:,.4f}", style="bold white")

    def _format_pct(self, value: float | None) -> Text:
        if value is None:
            return Text("—", style="dim")
        if value > 0:
            return Text(f"+{value:.1f}%", style="green")
        if value < 0:
            return Text(f"{value:.1f}%", style="red")
        return Text("0.0%", style="dim")

    def _format_change_cell(self, change: TokenChange) -> Text:
        if change.delta is None and change.pct is None:
            return Text("—", style="dim")
        delta = self._format_delta(change.delta)
        pct = self._format_pct(change.pct)
        return Text.assemble(delta, (" ", "dim"), pct)

    def _render_wallet_stats(self) -> Panel:
        tracker = self._wallet_balances
        if tracker is None:
            return Panel(
                Text("—", style="dim"),
                title="Wallet balances",
                border_style="dim",
                padding=(0, 1),
            )

        view = tracker.view()
        tbl = Table(box=None, padding=(0, 1), expand=True)
        tbl.add_column(justify="right", style="dim", width=5)
        tbl.add_column(justify="right", width=14)
        tbl.add_column(justify="right", width=16)
        tbl.add_column(justify="right", width=16)
        tbl.add_row(
            Text("", style="dim"),
            Text("Balance", style="dim italic"),
            Text(f"Day vs {view.previous_day_label}", style="dim italic"),
            Text(f"Week vs {view.previous_week_label}", style="dim italic"),
        )

        if view.current is None:
            status = view.status
            if status == "waiting":
                status = "fetching..."
            elif status == "rpc error":
                status = "RPC unavailable"
            tbl.add_row(
                Text("", style="dim"),
                Text(status, style="dim italic"),
                Text("", style="dim"),
                Text("", style="dim"),
            )
        else:
            for label, amount, day_change, week_change in (
                ("XNM", view.current.xnm, view.xnm_day, view.xnm_week),
                ("XUNI", view.current.xuni, view.xuni_day, view.xuni_week),
                ("XBLK", view.current.xblk, view.xblk_day, view.xblk_week),
            ):
                tbl.add_row(
                    Text(label, style=_TOKEN_STYLES[label]),
                    self._format_balance(amount),
                    self._format_change_cell(day_change),
                    self._format_change_cell(week_change),
                )

        title = "Wallet balances (tokens)"
        if view.current is not None:
            if view.status == "cached":
                title = "Wallet balances (cached)"
            elif view.status == "stale":
                title = "Wallet balances (refresh failed)"
            elif view.status.startswith("partial ("):
                note = view.status.removeprefix("partial (").removesuffix(")")
                title = f"Wallet balances ({note})"
            elif view.previous_day is None:
                title = "Wallet balances (day vs yesterday pending)"
        return Panel(
            tbl,
            title=title,
            border_style="dim",
            padding=(0, 1),
        )

    def _render_right_panel(self) -> Group:
        return Group(
            self._render_wallet_stats(),
            Text(""),
            self._render_local_stats(),
        )

    def _render_local_stats(self) -> Panel:
        tracker = self._local_stats
        if tracker is None:
            return Panel(
                Text("—", style="dim"),
                title="Local accepts (tokens)",
                border_style="dim",
                padding=(0, 1),
            )

        view = tracker.view()
        tbl = Table(box=None, padding=(0, 1), expand=True)
        tbl.add_column(justify="right", style="dim", width=5)
        tbl.add_column(justify="right", width=10)
        tbl.add_column(justify="right", width=14)
        tbl.add_column(justify="right", width=14)
        tbl.add_row(
            Text("", style="dim"),
            Text("Today", style="dim italic"),
            Text(f"Day vs {view.previous_day_label}", style="dim italic"),
            Text(f"Week vs {view.previous_week_label}", style="dim italic"),
        )
        for label, today_tokens, day_change, week_change, blocks in (
            ("XNM", view.today_xnm, view.xnm_day, view.xnm_week, view.today_blocks_xnm),
            ("XUNI", view.today_xuni, view.xuni_day, view.xuni_week, view.today_blocks_xuni),
            ("XBLK", view.today_xblk, view.xblk_day, view.xblk_week, view.today_blocks_xblk),
        ):
            # Primary: token amount; dim suffix shows raw block count.
            amount = self._styled_token_amount(today_tokens, "bold white")
            if blocks and abs(today_tokens - float(blocks)) > 0.001:
                amount = Text.assemble(
                    amount,
                    (f" ({blocks} blk)", "dim"),
                )
            tbl.add_row(
                Text(label, style=_TOKEN_STYLES[label]),
                amount,
                self._format_change_cell(day_change),
                self._format_change_cell(week_change),
            )

        return Panel(
            tbl,
            title=f"Local accepts · {view.reward_era}",
            border_style="dim",
            padding=(0, 1),
        )

    def _uptime_bar(self, pct: float | None, width: int = 12) -> Text:
        if pct is None:
            return Text(" " * width, style="dim")
        filled = int(round((pct / 100.0) * width))
        filled = max(0, min(width, filled))
        return Text.assemble(
            (_UP_BAR * filled, "bold green"),
            (_UP_EMPTY * (width - filled), "dim red"),
        )

    def _render_server_uptime(self) -> Panel:
        tracker = self._server_uptime
        if tracker is None:
            return Panel(
                Text("—", style="dim"),
                title="Server uptime (last 6h)",
                border_style="dim",
                padding=(0, 1),
            )

        view = tracker.view()
        tbl = Table(box=None, padding=(0, 1), expand=True)
        tbl.add_column("Hour", style="dim", width=14)
        tbl.add_column("Uptime", width=14)
        tbl.add_column("%", justify="right", width=5)
        tbl.add_column("Down", justify="right", width=8)
        tbl.add_column("Outages", justify="right", width=8)

        for row in view.hours:
            pct_text = Text("—", style="dim")
            if row.uptime_pct is not None:
                pct_text = Text(f"{row.uptime_pct:4.0f}%", style="bold white")
            down_text = (
                Text(format_duration(row.offline_s), style="dim")
                if row.offline_s > 0
                else Text("—", style="dim")
            )
            outage_text = (
                Text(str(row.outage_count), style="yellow")
                if row.outage_count
                else Text("—", style="dim")
            )
            tbl.add_row(
                row.label,
                self._uptime_bar(row.uptime_pct),
                pct_text,
                down_text,
                outage_text,
            )

        summary = Table.grid(padding=(0, 1))
        summary.add_column()
        avg_label = (
            format_duration(view.avg_outage_s)
            if view.avg_outage_s is not None
            else "—"
        )
        status_style = "bold green" if view.current_ok else "bold red"
        status_label = "online" if view.current_ok else "offline"
        if view.current_outage_s is not None:
            status_label = f"offline ({format_duration(view.current_outage_s)})"
        summary.add_row(
            Text.assemble(
                ("Avg outage ", "dim"),
                (avg_label, "bold white"),
                ("   Events ", "dim"),
                (str(view.outage_count), "bold white"),
                ("   XUNI window ", "dim"),
                (str(view.xuni_outage_count), "bold yellow"),
                ("   24h down ", "dim"),
                (format_duration(view.offline_s_24h), "bold white"),
                ("   Now ", "dim"),
                (status_label, status_style),
            )
        )

        body = Group(tbl, summary)
        return Panel(
            body,
            title="Server uptime (last 6h, newest first)",
            border_style="dim",
            padding=(0, 1),
        )

    def render(self) -> Panel:
        stats = self._stats
        wallet = self.settings.address
        if len(wallet) > 20:
            wallet = f"{wallet[:10]}...{wallet[-8:]}"

        header = Table.grid(expand=True)
        header.add_column(ratio=3)
        header.add_column(ratio=2, justify="right")

        title_block = Table.grid(expand=True)
        title_block.add_column()
        title_block.add_row(Text("XenBlocks Miner by Tony.x1", style="bold cyan"))
        title_block.add_row(
            Text(
                f"Wallet {wallet}  |  Backend {self.settings.backend}  |  {self._status}",
                style="dim",
            )
        )
        title_block.add_row(
            Text(f"{reward_era_label()}  ·  {current_reward_summary()}", style="dim cyan")
        )
        header.add_row(title_block, self._render_right_panel())

        rows = dashboard_rows(stats, self._pending_by_type, self._resubmission_by_type)
        accepted_tokens = accepted_tokens_by_kind(stats)

        tbl = Table(
            box=box.ROUNDED,
            expand=True,
            show_header=True,
            header_style="bold white",
        )
        tbl.add_column("", style="dim", width=16)
        tbl.add_column("XUNI", justify="right", style="bold yellow")
        tbl.add_column("XNM", justify="right", style="bold green")
        tbl.add_column("XBLK", justify="right", style="bold red")
        tbl.add_column("Total", justify="right", style="bold white")

        def add_row(label: str, values: dict[str, float], unit: str) -> None:
            fmt = self._styled_token_amount if unit == "tokens" else self._styled_count
            tbl.add_row(
                label,
                fmt(values["XUNI"], _TOKEN_STYLES["XUNI"]),
                fmt(values["XNM"], _TOKEN_STYLES["XNM"]),
                fmt(values["XBLK"], _TOKEN_STYLES["XBLK"]),
                fmt(row_total(values), "white"),
            )

        for label, values, unit in rows:
            add_row(label, values, unit)

        tbl.add_section()
        tbl.add_row(
            Text("Accepted tokens", style="dim italic"),
            Text("", style="dim"),
            Text("", style="dim"),
            Text("", style="dim"),
            self._styled_token_amount(row_total(accepted_tokens), "bold white"),
        )

        speed = f"{stats.hps_ema:,.0f} H/s" if stats.hps_ema > 0 else "warming up..."
        if self._difficulty is not None:
            diff = str(self._difficulty)
            if not self._network_ok:
                diff = f"{diff} (stale)"
        else:
            diff = "—"
        net = "online" if self._network_ok else "offline"

        footer = Table.grid(expand=True)
        footer.add_row(
            Text.assemble(
                ("Speed ", "dim"),
                (speed, "bold blue"),
                ("   Difficulty ", "dim"),
                (diff, "bold cyan"),
                ("   Network ", "dim"),
                (net, "bold green" if self._network_ok else "bold red"),
            )
        )

        extra_parts: list[str] = []
        if self._cuda_batch:
            if self._cuda_lanes > 1:
                extra_parts.append(
                    f"{self._cuda_lanes} lanes × {self._cuda_batch:,}"
                )
            else:
                extra_parts.append(f"batch {self._cuda_batch:,}")
        if self._gpu:
            extra_parts.append(
                f"VRAM {self._gpu.used_mib:,}/{self._gpu.total_mib:,} MiB"
            )
            extra_parts.append(f"GPU {self._gpu.util_pct}%")
            extra_parts.append(f"{self._gpu.temperature_c}°C")
        extra_parts.append(f"hashes {stats.total_hashes:,}")
        footer.add_row(Text("  |  ".join(extra_parts), style="dim"))

        # Recent events (fixed slot, no scroll)
        evt_tbl = Table(box=None, expand=True, padding=(0, 1))
        evt_tbl.add_column("Time", style="dim", width=10)
        evt_tbl.add_column("Event", width=10)
        evt_tbl.add_column("Type", width=8)
        evt_tbl.add_column("Detail", overflow="ellipsis")

        if not self._events:
            evt_tbl.add_row("—", "—", "—", Text("Waiting for activity...", style="dim italic"))
        else:
            for ev in list(self._events)[:6]:
                block_style = {
                    "XUNI": "yellow",
                    "XNM": "green",
                    "XBLK": "red",
                    "QUEUED": "magenta",
                }.get(ev.block, "white")
                action_style = {
                    "ACCEPTED": "bold green",
                    "QUEUED": "bold magenta",
                    "RESUBMIT": "bold magenta",
                    "FOUND": "bold white",
                    "FAILED": "bold red",
                    "WARN": "yellow",
                }.get(ev.action, "white")
                evt_tbl.add_row(
                    ev.ts,
                    Text(ev.action, style=action_style),
                    Text(ev.block, style=block_style),
                    ev.detail,
                )

        timelapse_panel = self._render_timelapse()

        body = Group(
            header,
            Text(""),
            tbl,
            Text(""),
            footer,
            Text(""),
            timelapse_panel,
            Text(""),
            Panel(evt_tbl, title="Recent", border_style="dim", padding=(0, 1)),
            Text(""),
            self._render_server_uptime(),
        )
        return Panel(body, border_style="cyan", padding=(1, 2))

    def _render_timelapse(self) -> Panel:
        if self._timelapse is None:
            return Panel(
                Text("Timelapse unavailable", style="dim italic"),
                title="Session Timelapse",
                border_style="dim",
                padding=(0, 1),
            )

        avg_hps = self._timelapse.average_hps()
        avg_label = f"{avg_hps:,.0f} H/s 1h avg" if avg_hps > 0 else "warming up"
        spark = self._timelapse.sparkline(width=48)
        split = self._timelapse.format_uptime_split(self._network_ok)

        tl = Table.grid(expand=True)
        tl.add_column()
        tl.add_row(
            Text.assemble(
                ("Elapsed ", "dim"),
                (self._timelapse.format_elapsed(), "bold white"),
                ("   ", "dim"),
                (split, "dim"),
            )
        )
        tl.add_row(
            Text.assemble(
                ("H/s 1h ", "dim"),
                (spark, "bold blue"),
                ("  ", "dim"),
                (avg_label, "cyan"),
            )
        )
        tl.add_row(Text(self._timelapse.event_line(), style="dim", overflow="ellipsis"))

        return Panel(
            tl,
            title="Session Timelapse (H/s last hour)",
            border_style="dim",
            padding=(0, 1),
        )