import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from monitoring.local_stats import LocalMiningStatsTracker
from monitoring.periods import mining_day, mining_week_start, previous_mining_week_start


class LocalMiningStatsTests(unittest.TestCase):
    def test_record_accept_persists_mining_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mining_stats_history.json"
            tracker = LocalMiningStatsTracker(path)
            now = datetime(2026, 7, 17, 14, 0, 0)
            tracker.record_accept("XNM", now=now)
            tracker.record_accept("XNM", now=now)
            tracker.record_accept("XUNI", now=now)

            raw = json.loads(path.read_text(encoding="utf-8"))
            key = mining_day(now).isoformat()
            self.assertEqual(raw["days"][key]["XNM"], 2)
            self.assertEqual(raw["days"][key]["XUNI"], 1)

    def test_before_1am_counts_toward_previous_calendar_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mining_stats_history.json"
            tracker = LocalMiningStatsTracker(path)
            now = datetime(2026, 7, 17, 0, 30, 0)
            tracker.record_accept("XNM", now=now)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("2026-07-16", raw["days"])
            self.assertEqual(raw["days"]["2026-07-16"]["XNM"], 1)

    def test_view_uses_token_amounts_with_halving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mining_stats_history.json"
            tracker = LocalMiningStatsTracker(path)
            now = datetime(2026, 7, 17, 14, 0, 0)
            for _ in range(4):
                tracker.record_accept("XNM", now=now)
            tracker.record_accept("XUNI", now=now)
            view = tracker.view(now=now)
            # 4 blocks × 2.5 XNM = 10 tokens; XUNI 1×1 = 1
            self.assertEqual(view.today_xnm, 10.0)
            self.assertEqual(view.today_blocks_xnm, 4)
            self.assertEqual(view.today_xuni, 1.0)
            self.assertIn("2.5", view.reward_era)

    def test_view_day_delta_in_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mining_stats_history.json"
            now = datetime(2026, 7, 17, 14, 0, 0)
            today = mining_day(now)
            yesterday = today - timedelta(days=1)
            # yesterday 10 blocks → 25 XNM; today 20 blocks → 50 XNM
            path.write_text(
                json.dumps(
                    {
                        "days": {
                            today.isoformat(): {"XNM": 20, "XUNI": 0, "XBLK": 0},
                            yesterday.isoformat(): {"XNM": 10, "XUNI": 0, "XBLK": 0},
                        }
                    }
                ),
                encoding="utf-8",
            )
            tracker = LocalMiningStatsTracker(path)
            view = tracker.view(now=now)
            self.assertEqual(view.today_xnm, 50.0)
            self.assertEqual(view.xnm_day.delta, 25.0)

    def test_view_week_uses_monday_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mining_stats_history.json"
            now = datetime(2026, 7, 17, 14, 0, 0)  # Friday
            this_start = mining_week_start(now)
            prior_start = previous_mining_week_start(now)
            days = {}
            for offset in range(7):
                day = prior_start + timedelta(days=offset)
                days[day.isoformat()] = {"XNM": 10, "XUNI": 0, "XBLK": 0}
            for offset in range((mining_day(now) - this_start).days + 1):
                day = this_start + timedelta(days=offset)
                days[day.isoformat()] = {"XNM": 20, "XUNI": 0, "XBLK": 0}
            path.write_text(json.dumps({"days": days}), encoding="utf-8")

            tracker = LocalMiningStatsTracker(path)
            view = tracker.view(now=now)
            # this week 5*20*2.5=250; prior 7*10*2.5=175 → delta 75
            self.assertEqual(view.xnm_week.delta, 75.0)

    def test_missing_baseline_shows_no_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mining_stats_history.json"
            now = datetime(2026, 7, 17, 14, 0, 0)
            today = mining_day(now)
            path.write_text(
                json.dumps(
                    {
                        "days": {
                            today.isoformat(): {"XNM": 20, "XUNI": 5, "XBLK": 0},
                        }
                    }
                ),
                encoding="utf-8",
            )
            tracker = LocalMiningStatsTracker(path)
            view = tracker.view(now=now)
            self.assertIsNone(view.xnm_day.delta)
            self.assertIsNone(view.xnm_week.delta)


if __name__ == "__main__":
    unittest.main()
