import unittest
from datetime import datetime

from monitoring.periods import (
    mining_day,
    mining_week_days,
    mining_week_start,
    previous_mining_day,
    previous_mining_week_days,
    previous_mining_week_start,
)


class MiningPeriodTests(unittest.TestCase):
    def test_mining_day_rolls_at_1am(self) -> None:
        before = datetime(2026, 7, 17, 0, 59, 0)
        after = datetime(2026, 7, 17, 1, 0, 0)
        self.assertEqual(mining_day(before).isoformat(), "2026-07-16")
        self.assertEqual(mining_day(after).isoformat(), "2026-07-17")
        self.assertEqual(previous_mining_day(after).isoformat(), "2026-07-16")

    def test_week_starts_monday_1am(self) -> None:
        # Friday Jul 17 2026 after 1am → week starts Monday Jul 13
        now = datetime(2026, 7, 17, 14, 0, 0)
        self.assertEqual(mining_week_start(now).isoformat(), "2026-07-13")
        self.assertEqual(previous_mining_week_start(now).isoformat(), "2026-07-06")
        days = mining_week_days(now)
        self.assertEqual(days[0].isoformat(), "2026-07-13")
        self.assertEqual(days[-1].isoformat(), "2026-07-17")
        prior = previous_mining_week_days(now)
        self.assertEqual(len(prior), 7)
        self.assertEqual(prior[0].isoformat(), "2026-07-06")
        self.assertEqual(prior[-1].isoformat(), "2026-07-12")

    def test_monday_before_1am_belongs_to_previous_week(self) -> None:
        # Monday 00:30 → still Sunday mining day → previous week
        now = datetime(2026, 7, 20, 0, 30, 0)  # Monday
        self.assertEqual(mining_day(now).isoformat(), "2026-07-19")  # Sunday
        self.assertEqual(mining_week_start(now).isoformat(), "2026-07-13")


if __name__ == "__main__":
    unittest.main()
