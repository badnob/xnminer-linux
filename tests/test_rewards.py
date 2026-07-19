import unittest
from datetime import date

from monitoring.rewards import (
    XNM_GENESIS_DATE,
    blocks_to_tokens,
    reward_per_block,
    xnm_reward_per_block,
    xnm_year_index,
)


class RewardHalvingTests(unittest.TestCase):
    def test_year1_is_10(self) -> None:
        self.assertEqual(xnm_year_index(XNM_GENESIS_DATE), 0)
        self.assertEqual(xnm_reward_per_block(XNM_GENESIS_DATE), 10.0)

    def test_year2_is_5(self) -> None:
        d = date(2024, 9, 13)
        self.assertEqual(xnm_year_index(d), 1)
        self.assertEqual(xnm_reward_per_block(d), 5.0)

    def test_year3_is_2_5(self) -> None:
        # After second yearly anniversary (~Sep 2025) → 2.5 XNM
        d = date(2026, 7, 17)
        self.assertEqual(xnm_year_index(d), 2)
        self.assertEqual(xnm_reward_per_block(d), 2.5)

    def test_xuni_and_xblk_fixed(self) -> None:
        self.assertEqual(reward_per_block("XUNI"), 1.0)
        self.assertEqual(reward_per_block("XBLK"), 1.0)

    def test_blocks_to_tokens(self) -> None:
        on = date(2026, 7, 16)
        self.assertEqual(blocks_to_tokens("XNM", 2976, on), 2976 * 2.5)
        self.assertEqual(blocks_to_tokens("XUNI", 3416, on), 3416.0)
        self.assertEqual(blocks_to_tokens("XNM", 5428, on), 13570.0)


if __name__ == "__main__":
    unittest.main()
