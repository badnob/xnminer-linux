import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from monitoring.server_uptime import ServerUptimeTracker, _outage_overlaps_xuni


class ServerUptimeTrackerTests(unittest.TestCase):
    def test_outage_overlaps_xuni_window(self) -> None:
        start = datetime(2026, 7, 13, 12, 57, 0).timestamp()
        end = datetime(2026, 7, 13, 12, 59, 30).timestamp()
        self.assertTrue(_outage_overlaps_xuni(start, end))

        outside_start = datetime(2026, 7, 13, 12, 10, 0).timestamp()
        outside_end = datetime(2026, 7, 13, 12, 20, 0).timestamp()
        self.assertFalse(_outage_overlaps_xuni(outside_start, outside_end))

    def test_record_probe_tracks_hourly_uptime_and_outages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_uptime.json"
            tracker = ServerUptimeTracker(path)
            # Use recent timestamps so retention pruning does not drop data.
            base = time.time() - 900
            base = datetime.fromtimestamp(base).replace(
                minute=0, second=0, microsecond=0
            ).timestamp()

            tracker.record_probe(True, ts=base)
            tracker.record_probe(True, ts=base + 60)
            tracker.record_probe(False, ts=base + 120)
            tracker.record_probe(False, ts=base + 420)
            tracker.record_probe(True, ts=base + 720)

            view = tracker.view(now=base + 900)
            self.assertTrue(any(row.uptime_pct is not None for row in view.hours))
            self.assertTrue(any(row.offline_s > 0.0 for row in view.hours))
            self.assertEqual(view.outage_count, 1)
            self.assertIsNotNone(view.avg_outage_s)
            self.assertAlmostEqual(view.avg_outage_s, 600.0, delta=1.0)
            self.assertTrue(view.current_ok)
            self.assertEqual(len(view.hours), 6)
            # Current hour first (descending), marked with *.
            self.assertTrue(view.hours[0].label.endswith("*"))

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("hourly", raw)
            self.assertEqual(len(raw["outages"]), 1)
            self.assertTrue(raw["last_ok"])
            self.assertIsNotNone(raw["last_probe_at"])

    def test_view_reports_current_outage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ServerUptimeTracker(Path(tmp) / "server_uptime.json")
            base = time.time() - 200
            tracker.record_probe(True, ts=base)
            tracker.record_probe(False, ts=base + 30)
            view = tracker.view(now=base + 150)
            self.assertFalse(view.current_ok)
            self.assertIsNotNone(view.current_outage_s)
            self.assertAlmostEqual(view.current_outage_s, 120.0, delta=1.0)

    def test_view_projects_open_interval_after_last_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_uptime.json"
            tracker = ServerUptimeTracker(path)
            base = time.time() - 2000
            tracker.record_probe(True, ts=base)
            # No further probes; view should still show online time through `now`.
            view = tracker.view(now=base + 1800)
            self.assertTrue(any((row.uptime_pct or 0) >= 99.0 for row in view.hours))
            self.assertTrue(view.current_ok)

    def test_state_restored_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_uptime.json"
            tracker = ServerUptimeTracker(path)
            base = time.time() - 200
            tracker.record_probe(True, ts=base)
            tracker.record_probe(False, ts=base + 60)

            restored = ServerUptimeTracker(path)
            view = restored.view(now=base + 120)
            self.assertFalse(view.current_ok)
            self.assertIsNotNone(view.current_outage_s)


if __name__ == "__main__":
    unittest.main()
