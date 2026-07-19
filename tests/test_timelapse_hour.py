import tempfile
import time
import unittest
from pathlib import Path

from core.models import MiningStats
from monitoring.timelapse import SessionTimelapse


class TimelapseHourWindowTests(unittest.TestCase):
    def test_sparkline_covers_full_hour_width(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tl = SessionTimelapse(
                Path(tmp) / "tl.jsonl",
                sample_interval_s=1.0,
                max_samples=200,
                window_s=3600.0,
            )
            now = time.time()
            tl._started = now - 3600
            # Samples across the hour: low early, high late.
            for minutes, hps in ((50, 100_000), (30, 200_000), (10, 400_000), (0, 500_000)):
                ts = now - minutes * 60
                stats = MiningStats(hps_ema=hps, accepted_live_xnm=1)
                tl._last_sample_at = 0.0
                # Inject directly to control timestamps.
                from monitoring.timelapse import TimelapseSample

                tl._samples.append(
                    TimelapseSample(
                        elapsed_s=int(ts - tl._started),
                        hps=hps,
                        vram_mib=0,
                        temp_c=0,
                        pending=0,
                        accepted=1,
                        network_ok=True,
                        wall_ts=ts,
                    )
                )

            spark = tl.sparkline(width=48, now=now)
            self.assertEqual(len(spark), 48)
            # Left side should be lower/earlier; right side higher.
            self.assertNotEqual(spark.strip(), "")
            avg = tl.average_hps(now=now)
            self.assertGreater(avg, 100_000)
            self.assertLess(avg, 500_000)

    def test_average_ignores_samples_older_than_hour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tl = SessionTimelapse(
                Path(tmp) / "tl.jsonl",
                sample_interval_s=1.0,
                window_s=3600.0,
            )
            now = time.time()
            from monitoring.timelapse import TimelapseSample

            tl._samples.append(
                TimelapseSample(
                    elapsed_s=0,
                    hps=10_000,
                    vram_mib=0,
                    temp_c=0,
                    pending=0,
                    accepted=0,
                    network_ok=True,
                    wall_ts=now - 7200,
                )
            )
            tl._samples.append(
                TimelapseSample(
                    elapsed_s=0,
                    hps=200_000,
                    vram_mib=0,
                    temp_c=0,
                    pending=0,
                    accepted=0,
                    network_ok=True,
                    wall_ts=now - 60,
                )
            )
            self.assertAlmostEqual(tl.average_hps(now=now), 200_000.0)


if __name__ == "__main__":
    unittest.main()
