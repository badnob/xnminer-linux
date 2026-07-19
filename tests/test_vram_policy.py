import unittest

from efficiency.vram_policy import (
    DEFAULT_DESKTOP_HEADROOM_PCT,
    DEFAULT_TARGET_VRAM_PCT,
    resolve_vram_caps,
)


class VramPolicyTests(unittest.TestCase):
    def test_scales_5090_profile_to_percentages(self) -> None:
        # ~32 GB card — should land near the original fixed caps.
        caps = resolve_vram_caps(32607)
        self.assertAlmostEqual(caps.target_mib, 22528, delta=20)
        self.assertAlmostEqual(caps.headroom_mib, 8192, delta=20)
        self.assertAlmostEqual(caps.emergency_mib, 30252, delta=30)
        self.assertAlmostEqual(caps.min_headroom_mib, 1200, delta=20)
        self.assertAlmostEqual(caps.runtime_overhead_mib, 2048, delta=20)

    def test_8gb_card_keeps_proportional_headroom(self) -> None:
        caps = resolve_vram_caps(8192)
        # Fixed 8 GB headroom would leave 0; % policy leaves ~25%.
        self.assertGreater(caps.headroom_mib, 1500)
        self.assertLess(caps.headroom_mib, 2500)
        self.assertGreater(caps.target_mib, 4000)
        self.assertLess(caps.target_mib + caps.headroom_mib, caps.total_mib + 1)
        # Must still fit a batch budget (target - overhead > 0).
        self.assertGreater(caps.target_mib - caps.runtime_overhead_mib, 0)

    def test_12gb_card_usable(self) -> None:
        caps = resolve_vram_caps(12288)
        self.assertGreaterEqual(caps.headroom_mib, 512)
        self.assertGreater(caps.target_mib - caps.runtime_overhead_mib, 1000)

    def test_absolute_override(self) -> None:
        caps = resolve_vram_caps(
            12288,
            target_mib_override=6000,
            headroom_mib_override=2000,
        )
        self.assertEqual(caps.target_mib, 6000)
        self.assertEqual(caps.headroom_mib, 2000)

    def test_defaults_match_reference_ratios(self) -> None:
        self.assertAlmostEqual(DEFAULT_TARGET_VRAM_PCT, 69.09, places=1)
        self.assertAlmostEqual(DEFAULT_DESKTOP_HEADROOM_PCT, 25.12, places=1)


if __name__ == "__main__":
    unittest.main()
