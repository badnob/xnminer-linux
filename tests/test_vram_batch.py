from __future__ import annotations

import unittest
from dataclasses import replace

from mining.vram_batch import (
    CUDA_ENGINE_RESERVE_BYTES,
    clamp_plan_to_caps,
    cuda_lane_count,
    plan_cuda_batch,
    select_batch_size,
    vram_budget_bytes,
    vram_cap_batch_budget_bytes,
)

REF = 1100
MAX_LANES = 4


class VramBatchTests(unittest.TestCase):
    def test_budget_respects_target_and_desktop_headroom(self) -> None:
        total = 32607 * 1024 * 1024
        free = (32607 - 2864) * 1024 * 1024
        budget = vram_budget_bytes(
            total,
            free,
            target_mib=22528,
            desktop_headroom_mib=8192,
        )
        self.assertEqual(budget // (1024 * 1024), 19664)

    def test_plan_stays_within_configured_limits(self) -> None:
        total = 32607 * 1024 * 1024
        free = (32607 - 2864) * 1024 * 1024
        plan = plan_cuda_batch(
            total,
            free,
            target_mib=22528,
            desktop_headroom_mib=8192,
            difficulty=1100,
            reference_difficulty=REF,
            max_lanes=MAX_LANES,
            runtime_overhead_mib=2048,
        )
        self.assertGreater(plan.batch_per_lane, 0)
        self.assertEqual(plan.lanes, 1)
        self.assertLessEqual(plan.projected_used_mib, plan.target_mib)
        self.assertGreaterEqual(plan.projected_headroom_mib, plan.desktop_headroom_mib)

    def test_explicit_batch_is_clamped_to_budget(self) -> None:
        total = 32607 * 1024 * 1024
        free = (32607 - 2864) * 1024 * 1024
        plan = plan_cuda_batch(
            total,
            free,
            target_mib=22528,
            desktop_headroom_mib=8192,
            difficulty=1100,
            reference_difficulty=REF,
            max_lanes=MAX_LANES,
            explicit_batch=50000,
            runtime_overhead_mib=2048,
        )
        per_lane_budget = plan.budget_bytes
        max_batch = select_batch_size(per_lane_budget, 1100)
        self.assertEqual(plan.batch_per_lane, max_batch)
        self.assertLess(plan.batch_per_lane, 50000)

    def test_cap_budget_ignores_live_free_memory(self) -> None:
        total = 32607 * 1024 * 1024
        cold = vram_cap_batch_budget_bytes(
            total,
            target_mib=22528,
            desktop_headroom_mib=8192,
            runtime_overhead_mib=2048,
        )
        hot = vram_cap_batch_budget_bytes(
            total,
            target_mib=22528,
            desktop_headroom_mib=8192,
            runtime_overhead_mib=2048,
        )
        self.assertEqual(cold, hot)
        self.assertEqual(cold[0] // (1024 * 1024), 20480)

    def test_low_difficulty_spins_up_lanes_and_fills_cap(self) -> None:
        total = 32607 * 1024 * 1024
        free = (32607 - 2864) * 1024 * 1024
        plan_100 = plan_cuda_batch(
            total,
            free,
            target_mib=22528,
            desktop_headroom_mib=8192,
            difficulty=100,
            reference_difficulty=REF,
            max_lanes=MAX_LANES,
            runtime_overhead_mib=2048,
        )
        plan_1100 = plan_cuda_batch(
            total,
            free,
            target_mib=22528,
            desktop_headroom_mib=8192,
            difficulty=1100,
            reference_difficulty=REF,
            max_lanes=MAX_LANES,
            runtime_overhead_mib=2048,
        )
        self.assertEqual(plan_100.lanes, 4)
        self.assertEqual(plan_1100.lanes, 1)
        self.assertGreater(plan_100.batch_per_lane, plan_1100.batch_per_lane)
        self.assertLessEqual(
            abs(plan_100.batch_vram_mib - plan_1100.batch_vram_mib),
            2,
        )
        self.assertLessEqual(plan_100.projected_used_mib, plan_100.target_mib)
        self.assertLessEqual(plan_1100.projected_used_mib, plan_1100.target_mib)

    def test_lane_count_crosses_from_one_to_max(self) -> None:
        self.assertEqual(
            cuda_lane_count(1100, reference_difficulty=REF, max_lanes=MAX_LANES),
            1,
        )
        self.assertEqual(
            cuda_lane_count(100, reference_difficulty=REF, max_lanes=MAX_LANES),
            4,
        )
        self.assertEqual(
            cuda_lane_count(550, reference_difficulty=REF, max_lanes=MAX_LANES),
            2,
        )

    def test_harvest_plan_fills_budget_within_caps(self) -> None:
        total = 32607 * 1024 * 1024
        free = (32607 - 2864) * 1024 * 1024
        plan = plan_cuda_batch(
            total,
            free,
            target_mib=22528,
            desktop_headroom_mib=8192,
            difficulty=100,
            reference_difficulty=REF,
            max_lanes=MAX_LANES,
            runtime_overhead_mib=2048,
        )
        self.assertTrue(plan.within_limits())
        self.assertTrue(plan.fills_budget())
        self.assertEqual(plan.lanes, 4)

    def test_clamp_plan_shrinks_to_caps(self) -> None:
        total = 32607 * 1024 * 1024
        free = (32607 - 2864) * 1024 * 1024
        plan = plan_cuda_batch(
            total,
            free,
            target_mib=22528,
            desktop_headroom_mib=8192,
            difficulty=1100,
            reference_difficulty=REF,
            max_lanes=MAX_LANES,
            runtime_overhead_mib=2048,
        )
        over_batch = plan.batch_per_lane + 5000
        over_vram = plan.batch_vram_mib + 5000
        over = replace(
            plan,
            batch_per_lane=over_batch,
            batch_size=over_batch,
            batch_vram_mib=over_vram,
            batch_vram_bytes=over_vram * 1024 * 1024,
            projected_used_mib=over_vram + plan.runtime_overhead_mib,
            projected_headroom_mib=max(
                0,
                plan.projected_used_mib
                + plan.projected_headroom_mib
                - (over_vram + plan.runtime_overhead_mib),
            ),
        )
        self.assertFalse(over.within_limits())
        clamped = clamp_plan_to_caps(over)
        self.assertTrue(clamped.within_limits())
        self.assertLessEqual(clamped.batch_per_lane, over.batch_per_lane)

    def test_python_budget_matches_native_reserve_convention(self) -> None:
        budget = 19664 * 1024 * 1024
        batch = select_batch_size(budget, 1100)
        native_arg = budget + CUDA_ENGINE_RESERVE_BYTES
        from mining.vram_batch import memory_limited_batch_size

        self.assertEqual(batch, memory_limited_batch_size(native_arg, 1100))


if __name__ == "__main__":
    unittest.main()