import unittest
from unittest.mock import MagicMock, patch

from core.models import GpuSnapshot
from efficiency.gpu_power import GpuPowerBooster


class GpuPowerBoosterTests(unittest.TestCase):
    def _snap(self, temp_c: int) -> GpuSnapshot:
        return GpuSnapshot(
            index=0,
            name="Test GPU",
            total_mib=32607,
            used_mib=9000,
            free_mib=23607,
            util_pct=90,
            power_w=350.0,
            temperature_c=temp_c,
        )

    def test_apply_boosts_toward_target_pct(self) -> None:
        monitor = MagicMock()
        monitor.get_power_limits_mw.return_value = (330_000, 200_000, 600_000)
        monitor.set_power_limit_mw.return_value = True

        booster = GpuPowerBooster(
            monitor,
            target_pct=100,
            warn_temp_c=72,
            max_temp_c=75,
            windows_performance_mode=False,
        )
        self.assertTrue(booster.apply())
        monitor.set_power_limit_mw.assert_called_once_with(600_000)

    def test_apply_skips_when_already_at_target(self) -> None:
        monitor = MagicMock()
        monitor.get_power_limits_mw.return_value = (600_000, 200_000, 600_000)

        booster = GpuPowerBooster(
            monitor,
            target_pct=100,
            warn_temp_c=72,
            max_temp_c=75,
            windows_performance_mode=False,
        )
        self.assertFalse(booster.apply())
        monitor.set_power_limit_mw.assert_not_called()

    def test_adjust_steps_down_near_warn_temp(self) -> None:
        monitor = MagicMock()
        monitor.get_power_limits_mw.return_value = (330_000, 200_000, 600_000)
        monitor.set_power_limit_mw.return_value = True

        booster = GpuPowerBooster(
            monitor,
            target_pct=100,
            warn_temp_c=72,
            max_temp_c=75,
            windows_performance_mode=False,
        )
        booster.apply()
        monitor.set_power_limit_mw.reset_mock()

        booster.adjust(self._snap(71))
        self.assertTrue(monitor.set_power_limit_mw.called)
        new_limit = monitor.set_power_limit_mw.call_args.args[0]
        self.assertLess(new_limit, 600_000)

    def test_restore_puts_back_original_limit(self) -> None:
        monitor = MagicMock()
        monitor.get_power_limits_mw.return_value = (330_000, 200_000, 600_000)
        monitor.set_power_limit_mw.return_value = True

        booster = GpuPowerBooster(
            monitor,
            target_pct=100,
            warn_temp_c=72,
            max_temp_c=75,
            windows_performance_mode=False,
        )
        booster.apply()
        monitor.set_power_limit_mw.reset_mock()

        booster.restore()
        monitor.set_power_limit_mw.assert_called_once_with(330_000)


if __name__ == "__main__":
    unittest.main()