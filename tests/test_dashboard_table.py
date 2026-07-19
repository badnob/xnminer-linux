import unittest
from datetime import date

from core.models import MiningStats
from monitoring.dashboard_stats import (
    accepted_tokens_by_kind,
    dashboard_rows,
    queued_by_kind,
    rejected_by_kind,
    row_total,
)


class DashboardTableTests(unittest.TestCase):
    def _stats(self) -> MiningStats:
        s = MiningStats()
        s.accepted_live_xuni = 10
        s.accepted_flush_xuni = 2
        s.accepted_live_xnm = 20
        s.accepted_flush_xnm = 5
        s.accepted_live_xblk = 3
        s.accepted_flush_xblk = 1
        s.rejected_live_xuni = 1
        s.rejected_flush_xuni = 0
        s.rejected_live_xnm = 2
        s.rejected_flush_xnm = 1
        s.rejected_live_xblk = 0
        s.rejected_flush_xblk = 1
        s.failed_live_xuni = 1
        s.failed_live_xnm = 2
        s.failed_live_xblk = 0
        return s

    def test_accepted_tokens_apply_halving(self) -> None:
        # 25 XNM blocks × 2.5 = 62.5 tokens in year 3
        on = date(2026, 7, 17)
        accepted = accepted_tokens_by_kind(self._stats(), on=on)
        self.assertEqual(accepted["XUNI"], 12.0)
        self.assertEqual(accepted["XNM"], 62.5)
        self.assertEqual(accepted["XBLK"], 4.0)
        self.assertEqual(row_total(accepted), 78.5)

    def test_rejected_sums_live_and_flush(self) -> None:
        rejected = rejected_by_kind(self._stats())
        self.assertEqual(rejected["XUNI"], 1)
        self.assertEqual(rejected["XNM"], 3)
        self.assertEqual(rejected["XBLK"], 1)
        self.assertEqual(row_total(rejected), 5)

    def test_queued_and_failed_use_db_pending_split(self) -> None:
        pending = {"XUNI": 4, "XNM": 1, "XBLK": 0}
        retry = {"XUNI": 2, "XNM": 0, "XBLK": 1}
        queued = queued_by_kind(pending, resubmission=False)
        failed_queue = queued_by_kind(retry, resubmission=True)
        self.assertEqual(queued["XUNI"], 4)
        self.assertEqual(failed_queue["XBLK"], 1)

    def test_dashboard_row_order_and_labels(self) -> None:
        rows = dashboard_rows(
            self._stats(),
            {"XUNI": 4, "XNM": 1, "XBLK": 0},
            {"XUNI": 2, "XNM": 0, "XBLK": 1},
            on=date(2026, 7, 17),
        )
        self.assertEqual(
            [name for name, _, _ in rows],
            [
                "Found (session)",
                "Accepted (today)",
                "Rejected (pool)",
                "Queued",
                "Resubmit",
            ],
        )
        accepted_label, accepted, unit = rows[1]
        self.assertEqual(unit, "tokens")
        self.assertEqual(row_total(accepted), 78.5)
        self.assertEqual(rows[3][1]["XNM"], 1)
        self.assertEqual(rows[4][1]["XUNI"], 2)
        self.assertEqual(row_total(rows[4][1]), 3)


if __name__ == "__main__":
    unittest.main()
