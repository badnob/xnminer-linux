import unittest
from unittest.mock import patch

from core.models import GpuSnapshot, MiningStats
from monitoring.woodyminer_stats import (
    build_stat_payload,
    derive_machine_id,
    WoodyminerStatsUploader,
)


class WoodyminerStatsTests(unittest.TestCase):
    def test_derive_machine_id_is_stable_hex(self) -> None:
        first = derive_machine_id(0)
        second = derive_machine_id(0)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in first))

    def test_build_stat_payload_matches_native_shape(self) -> None:
        stats = MiningStats(
            hps_ema=123456.789,
            total_hashes=999,
            accepted_live_xnm=3,
            accepted_flush_xblk=2,
            rejected_live_xnm=1,
        )
        gpu = GpuSnapshot(
            index=0,
            name="RTX 4090",
            total_mib=24576,
            used_mib=12288,
            free_mib=12288,
            util_pct=88,
            power_w=250.5,
            temperature_c=65,
        )
        payload = build_stat_payload(
            machine_id="abc123",
            miner_address="0x1234567890abcdef1234567890abcdef12345678",
            stats=stats,
            gpu=gpu,
            difficulty=1100,
            uptime_s=3600,
            custom_name="miner1",
        )
        self.assertEqual(payload["machineId"], "abc123")
        self.assertEqual(payload["minerAddr"], "0x1234567890abcdef1234567890abcdef12345678")
        self.assertEqual(payload["totalHashrate"], "123456.79")
        self.assertEqual(payload["acceptedBlocks"], 5)
        self.assertEqual(payload["normalBlocks"], 3)
        self.assertEqual(payload["superBlocks"], 2)
        self.assertEqual(payload["rejectedBlocks"], 1)
        self.assertEqual(payload["customName"], "miner1")
        self.assertEqual(len(payload["gpus"]), 1)
        self.assertEqual(payload["gpus"][0]["power"], 250500)

    @patch("monitoring.woodyminer_stats.urllib.request.urlopen")
    def test_upload_once_returns_http_status(self, mock_urlopen) -> None:
        class _Resp:
            status = 201

            def read(self) -> bytes:
                return b'{"message":"Data uploaded successfully"}'

        mock_urlopen.return_value.__enter__.return_value = _Resp()
        uploader = WoodyminerStatsUploader(
            upload_url="https://woodyminer.com/api/stat/upload",
            upload_period_s=60,
            custom_name="miner1",
            miner_address="0xabc",
            machine_id="deadbeef",
            get_stats=lambda: MiningStats(),
            get_gpu=lambda: None,
            get_difficulty=lambda: 1100,
            session_started_at=0.0,
            logger=type("L", (), {"info": lambda *a, **k: None, "warn": lambda *a, **k: None})(),
        )
        status, body = uploader.upload_once()
        self.assertEqual(status, 201)
        self.assertIn("uploaded", body.lower())


if __name__ == "__main__":
    unittest.main()