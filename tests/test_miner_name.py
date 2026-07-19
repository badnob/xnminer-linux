import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.wallet_setup import (
    _ensure_worker_id,
    apply_worker_name,
    ensure_wallet_configured,
    generate_random_miner_name,
    is_placeholder_miner_name,
    is_valid_worker_name,
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

    def test_valid_worker_name(self) -> None:
        self.assertTrue(is_valid_worker_name("MyRig-01"))
        self.assertTrue(is_valid_worker_name("Tony.x1"))
        self.assertFalse(is_valid_worker_name(""))
        self.assertFalse(is_valid_worker_name("miner1"))
        self.assertFalse(is_valid_worker_name("bad/name"))

    def test_apply_worker_custom_and_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "miner.ini"
            ini.write_text(
                "[account]\nworker =\n\n[monitoring]\nwoodyminer_custom_name =\n",
                encoding="utf-8",
            )
            self.assertEqual(apply_worker_name(ini, "Rig-A"), "Rig-A")
            text = ini.read_text(encoding="utf-8")
            self.assertIn("worker = Rig-A", text)
            self.assertIn("woodyminer_custom_name = Rig-A", text)

            ini.write_text(
                "[account]\nworker =\n\n[monitoring]\nwoodyminer_custom_name =\n",
                encoding="utf-8",
            )
            auto = apply_worker_name(ini, None)
            self.assertTrue(auto.startswith("xnminer-"))

    def test_first_run_prompts_worker_empty_means_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "miner.ini"
            ini.write_text(
                "\n".join(
                    [
                        "[account]",
                        "address =",
                        "worker =",
                        "",
                        "[monitoring]",
                        "woodyminer_custom_name =",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch("config.wallet_setup.sys.stdin.isatty", return_value=True),
                patch(
                    "config.wallet_setup.prompt_for_wallet",
                    return_value="0x1234567890abcdef1234567890abcdef12345678",
                ),
                patch("config.wallet_setup.prompt_for_worker_name", return_value=None),
            ):
                ensure_wallet_configured(ini, interactive=True)
            text = ini.read_text(encoding="utf-8")
            self.assertIn(
                "address = 0x1234567890abcdef1234567890abcdef12345678", text
            )
            # Auto name written
            self.assertRegex(text, r"worker = xnminer-[0-9a-f]{8}")

    def test_first_run_prompts_worker_custom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "miner.ini"
            ini.write_text(
                "\n".join(
                    [
                        "[account]",
                        "address =",
                        "worker =",
                        "",
                        "[monitoring]",
                        "woodyminer_custom_name =",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch("config.wallet_setup.sys.stdin.isatty", return_value=True),
                patch(
                    "config.wallet_setup.prompt_for_wallet",
                    return_value="0x1234567890abcdef1234567890abcdef12345678",
                ),
                patch(
                    "config.wallet_setup.prompt_for_worker_name",
                    return_value="Desk-Rig-1",
                ),
            ):
                ensure_wallet_configured(ini, interactive=True)
            text = ini.read_text(encoding="utf-8")
            self.assertIn("worker = Desk-Rig-1", text)
            self.assertIn("woodyminer_custom_name = Desk-Rig-1", text)


if __name__ == "__main__":
    unittest.main()
