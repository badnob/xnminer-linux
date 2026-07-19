import tempfile
import unittest
from pathlib import Path

from config.wallet_setup import (
    _ensure_worker_id,
    generate_random_miner_name,
    is_placeholder_miner_name,
)


class MinerNameTests(unittest.TestCase):
    def test_random_name_shape(self) -> None:
        name = generate_random_miner_name()
        self.assertTrue(name.startswith("xnminer-"))
        self.assertEqual(len(name), len("xnminer-") + 8)
        self.assertNotEqual(name, generate_random_miner_name())

    def test_placeholders(self) -> None:
        self.assertTrue(is_placeholder_miner_name(""))
        self.assertTrue(is_placeholder_miner_name("miner1"))
        self.assertTrue(is_placeholder_miner_name("XenBlockScan"))
        self.assertFalse(is_placeholder_miner_name("my-rig-01"))
        self.assertFalse(is_placeholder_miner_name("Tony.x1"))

    def test_ensure_replaces_shared_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "miner.ini"
            ini.write_text(
                "\n".join(
                    [
                        "[account]",
                        "address = 0x1234567890abcdef1234567890abcdef12345678",
                        "worker = miner1",
                        "",
                        "[monitoring]",
                        "woodyminer_custom_name =",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            name = _ensure_worker_id(ini)
            self.assertTrue(name.startswith("xnminer-"))
            text = ini.read_text(encoding="utf-8")
            self.assertIn(f"worker = {name}", text)
            self.assertIn(f"woodyminer_custom_name = {name}", text)
            # Second call must keep the same name
            self.assertEqual(_ensure_worker_id(ini), name)

    def test_ensure_keeps_custom_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "miner.ini"
            ini.write_text(
                "\n".join(
                    [
                        "[account]",
                        "worker = MyRig-Alpha",
                        "",
                        "[monitoring]",
                        "woodyminer_custom_name = MyRig-Alpha",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(_ensure_worker_id(ini), "MyRig-Alpha")
            text = ini.read_text(encoding="utf-8")
            self.assertIn("worker = MyRig-Alpha", text)


if __name__ == "__main__":
    unittest.main()
