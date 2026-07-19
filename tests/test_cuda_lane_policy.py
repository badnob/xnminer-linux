from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from efficiency.cuda_lane_policy import (
    load_lane_policy,
    record_temp_lane_reduction,
    restore_lane_cap_if_cool,
    save_lane_policy,
)


class CudaLanePolicyTests(unittest.TestCase):
    def test_restore_lane_cap_when_cool_at_reference_difficulty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "gpu_lane_cap.json"
            log_path = Path(tmp) / "gpu_temp_lane.log"
            state = load_lane_policy(policy_path, config_max_lanes=4)
            state.effective_max_lanes = 2
            save_lane_policy(policy_path, state)

            restored, changed = restore_lane_cap_if_cool(
                policy_path,
                log_path,
                state,
                temperature_c=60,
                warn_temp_c=72,
                difficulty=1100,
                reference_difficulty=1100,
            )
            self.assertTrue(changed)
            self.assertEqual(restored.effective_max_lanes, 4)

    def test_restore_lane_cap_skipped_when_still_warm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "gpu_lane_cap.json"
            log_path = Path(tmp) / "gpu_temp_lane.log"
            state = load_lane_policy(policy_path, config_max_lanes=4)
            state.effective_max_lanes = 2
            save_lane_policy(policy_path, state)

            restored, changed = restore_lane_cap_if_cool(
                policy_path,
                log_path,
                state,
                temperature_c=71,
                warn_temp_c=72,
                difficulty=1100,
                reference_difficulty=1100,
            )
            self.assertFalse(changed)
            self.assertEqual(restored.effective_max_lanes, 2)

    def test_reduce_lane_cap_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "gpu_lane_cap.json"
            log_path = Path(tmp) / "gpu_temp_lane.log"
            state = load_lane_policy(policy_path, config_max_lanes=4)
            self.assertEqual(state.effective_max_lanes, 4)

            updated = record_temp_lane_reduction(
                policy_path,
                log_path,
                state,
                temperature_c=76,
                difficulty=100,
                lanes_active=4,
                lanes_before=4,
                lanes_after=3,
                reason="test",
            )
            self.assertEqual(updated.effective_max_lanes, 3)
            self.assertEqual(updated.temp_reductions, 1)

            reloaded = load_lane_policy(policy_path, config_max_lanes=4)
            self.assertEqual(reloaded.effective_max_lanes, 3)
            self.assertTrue(log_path.exists())


if __name__ == "__main__":
    unittest.main()