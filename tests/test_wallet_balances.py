import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from monitoring.periods import mining_day, mining_week_start, previous_mining_day
from monitoring.wallet_balances import (
    TokenBalances,
    WalletBalanceTracker,
    _pct_change,
    _token_change,
    fetch_wallet_balances,
)


class WalletBalanceTrackerTests(unittest.TestCase):
    def test_pct_change_handles_zero_baseline(self) -> None:
        self.assertIsNone(_pct_change(10.0, 0.0))
        self.assertEqual(_pct_change(10.0, 100.0), 10.0)

    def test_view_computes_day_and_week_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "balance_history.json"
            now = datetime(2026, 7, 17, 14, 0, 0)
            yesterday = previous_mining_day(now).isoformat()  # Jul 16
            week_start = mining_week_start(now).isoformat()  # Mon Jul 13
            history.write_text(
                json.dumps(
                    {
                        "wallet": "0xabc",
                        "snapshots": {
                            yesterday: {"xnm": 100.0, "xuni": 40.0, "xblk": 2.0},
                            week_start: {"xnm": 80.0, "xuni": 30.0, "xblk": 1.0},
                        },
                    }
                ),
                encoding="utf-8",
            )
            tracker = WalletBalanceTracker("0xabc", history, refresh_interval_s=9999)
            tracker._current = TokenBalances(xnm=110.0, xuni=45.0, xblk=3.0)
            tracker._updated_at = 1.0
            tracker._status = "ok"

            view = tracker.view(now=now)
            self.assertEqual(view.previous_day_label, "Jul 16")
            self.assertEqual(view.previous_week_label, "Jul 13")
            self.assertEqual(view.xnm_day.delta, 10.0)
            self.assertEqual(view.xnm_day.pct, 10.0)
            self.assertEqual(view.xnm_week.delta, 30.0)
            self.assertEqual(view.xnm_week.pct, 37.5)
            self.assertEqual(view.xuni_day.delta, 5.0)
            self.assertEqual(view.xblk_week.delta, 2.0)

    def test_day_does_not_reuse_week_snapshot(self) -> None:
        """Only Jul 13 exists: day must be vs Jul 16 (missing → —), week vs Jul 13."""
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "balance_history.json"
            now = datetime(2026, 7, 17, 14, 0, 0)
            history.write_text(
                json.dumps(
                    {
                        "wallet": "0xabc",
                        "snapshots": {
                            "2026-07-13": {
                                "xnm": 100.0,
                                "xuni": 40.0,
                                "xblk": 2.0,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            tracker = WalletBalanceTracker("0xabc", history, refresh_interval_s=9999)
            tracker._current = TokenBalances(xnm=150.0, xuni=50.0, xblk=2.0)
            tracker._updated_at = 1.0
            tracker._status = "ok"

            view = tracker.view(now=now)
            self.assertEqual(view.previous_day_label, "Jul 16")
            self.assertIsNone(view.xnm_day.delta)
            self.assertEqual(view.previous_week_label, "Jul 13")
            self.assertEqual(view.xnm_week.delta, 50.0)

    def test_token_change_returns_none_without_baseline(self) -> None:
        change = _token_change(10.0, None)
        self.assertIsNone(change.delta)
        self.assertIsNone(change.pct)

    def test_record_snapshot_writes_mining_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "balance_history.json"
            tracker = WalletBalanceTracker("0xabc", history, refresh_interval_s=9999)
            now = datetime(2026, 7, 17, 0, 30, 0)  # before 1am → Jul 16
            tracker._record_snapshot(
                TokenBalances(xnm=1.0, xuni=2.0, xblk=3.0), now=now
            )
            raw = json.loads(history.read_text(encoding="utf-8"))
            self.assertEqual(raw["snapshots"]["2026-07-16"]["xnm"], 1.0)

    @patch.object(WalletBalanceTracker, "maybe_refresh")
    def test_daily_refresh_fires_after_1am(self, mock_refresh) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = WalletBalanceTracker("0xabc", Path(tmp) / "balance_history.json")
            fake_now = datetime(2026, 7, 13, 2, 0, 0)
            with patch("monitoring.wallet_balances.datetime") as dt:
                dt.now.return_value = fake_now
                dt.side_effect = lambda *a, **k: datetime(*a, **k) if a else fake_now
                tracker.maybe_daily_refresh()
            mock_refresh.assert_called_once_with(force=True)
            self.assertEqual(tracker._daily_refresh_date, "2026-07-13")

    @patch.object(WalletBalanceTracker, "maybe_refresh")
    def test_daily_refresh_skips_before_1am(self, mock_refresh) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = WalletBalanceTracker("0xabc", Path(tmp) / "balance_history.json")
            fake_now = datetime(2026, 7, 13, 0, 30, 0)
            with patch("monitoring.wallet_balances.datetime") as dt:
                dt.now.return_value = fake_now
                tracker.maybe_daily_refresh()
            mock_refresh.assert_not_called()

    @patch.object(WalletBalanceTracker, "maybe_refresh")
    def test_daily_refresh_runs_once_per_mining_day(self, mock_refresh) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = WalletBalanceTracker("0xabc", Path(tmp) / "balance_history.json")
            fake_now = datetime(2026, 7, 13, 3, 0, 0)
            with patch("monitoring.wallet_balances.datetime") as dt:
                dt.now.return_value = fake_now
                dt.side_effect = lambda *a, **k: datetime(*a, **k) if a else fake_now
                tracker.maybe_daily_refresh()
                tracker.maybe_daily_refresh()
            mock_refresh.assert_called_once_with(force=True)

    @patch.object(WalletBalanceTracker, "maybe_refresh")
    def test_refresh_on_launch_marks_daily_after_1am(self, mock_refresh) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = WalletBalanceTracker("0xabc", Path(tmp) / "balance_history.json")
            fake_now = datetime(2026, 7, 13, 9, 0, 0)
            with patch("monitoring.wallet_balances.datetime") as dt:
                dt.now.return_value = fake_now
                dt.side_effect = lambda *a, **k: datetime(*a, **k) if a else fake_now
                tracker.refresh_on_launch()
            self.assertEqual(tracker._daily_refresh_date, "2026-07-13")
            mock_refresh.assert_called_once_with(force=True)

    @patch("monitoring.wallet_balances._rpc_call")
    def test_fetch_wallet_balances(self, mock_rpc) -> None:
        mock_rpc.side_effect = [
            "0xde0b6b3a7640000",
        ]

        def erc20_balance(rpc_url, contract, wallet, timeout_s):
            if contract.endswith("00002"):
                return 4.0
            return 5.0

        with patch("monitoring.wallet_balances._erc20_balance", side_effect=erc20_balance):
            balances, cached = fetch_wallet_balances("0x" + "11" * 20, timeout_s=1.0)
        self.assertEqual(balances.xnm, 1.0)
        self.assertEqual(balances.xuni, 4.0)
        self.assertEqual(balances.xblk, 5.0)
        self.assertEqual(cached, [])

    @patch("monitoring.wallet_balances._erc20_balance")
    @patch("monitoring.wallet_balances._rpc_call")
    def test_fetch_wallet_balances_uses_fallback_on_xuni_timeout(
        self, mock_rpc, mock_erc20
    ) -> None:
        mock_rpc.return_value = "0xde0b6b3a7640000"
        mock_erc20.side_effect = [
            TimeoutError("timed out"),
            5.0,
        ]
        fallback = TokenBalances(xnm=0.0, xuni=99.0, xblk=0.0)
        balances, cached = fetch_wallet_balances(
            "0x" + "11" * 20,
            timeout_s=1.0,
            fallback=fallback,
        )
        self.assertEqual(balances.xnm, 1.0)
        self.assertEqual(balances.xuni, 99.0)
        self.assertEqual(balances.xblk, 5.0)
        self.assertEqual(cached, ["XUNI"])

    def test_seed_from_history_shows_cached_balances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "balance_history.json"
            today = mining_day().isoformat()
            history.write_text(
                json.dumps(
                    {
                        "wallet": "0xabc",
                        "snapshots": {
                            today: {"xnm": 10.0, "xuni": 20.0, "xblk": 3.0},
                        },
                    }
                ),
                encoding="utf-8",
            )
            tracker = WalletBalanceTracker("0xabc", history, refresh_interval_s=9999)
            view = tracker.view()
            self.assertEqual(view.status, "cached")
            self.assertIsNotNone(view.current)
            self.assertEqual(view.current.xuni, 20.0)


if __name__ == "__main__":
    unittest.main()
