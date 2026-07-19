import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from config.settings import load_settings
from core.models import BlockHit
from core.supervisor import Supervisor
from block_queue.store import BlockStore


class GpuCooldownQueueTests(unittest.TestCase):
    def _make_supervisor(self, tmp: str) -> Supervisor:
        root = Path(tmp)
        base = load_settings()
        settings = base.__class__(
            **{
                **base.__dict__,
                "backend": "cuda",
                "db_path": root / "blocks.db",
                "jsonl_path": root / "queue.jsonl",
                "rejected_jsonl_path": root / "rejected.jsonl",
                "log_path": root / "session.log",
            }
        )
        with patch.object(Supervisor, "_build_backend", return_value=MagicMock()):
            sup = Supervisor(settings, use_dashboard=False)
        sup.store = BlockStore(
            settings.db_path,
            settings.jsonl_path,
            settings.rejected_jsonl_path,
        )
        sup.flusher = MagicMock()
        sup.flusher.flush.return_value = 1
        sup.metrics = MagicMock()
        return sup

    def _enqueue(self, store: BlockStore) -> None:
        store.enqueue(
            BlockHit(
                key="0x" + "11" * 16,
                hash_str="a" * 64,
                block_type="XNM",
                attempts=1,
                strategy="random",
                hps=1000.0,
            ),
            reason="test",
        )

    def test_flush_when_network_up(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sup = self._make_supervisor(tmp)
            self._enqueue(sup.store)
            sup.refresh_network = MagicMock(return_value=True)

            flushed = sup._try_flush_pending_queue(context="GPU cooldown")

            self.assertEqual(flushed, 1)
            sup.flusher.flush.assert_called_once()
            sup.refresh_network.assert_called_once()

    def test_hold_when_network_down(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sup = self._make_supervisor(tmp)
            self._enqueue(sup.store)
            sup.refresh_network = MagicMock(return_value=False)

            flushed = sup._try_flush_pending_queue(context="GPU cooldown")

            self.assertEqual(flushed, 0)
            sup.flusher.flush.assert_not_called()
            self.assertEqual(sup.store.pending_count(), 1)

    def test_aggressive_service_during_cooldown(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sup = self._make_supervisor(tmp)
            self._enqueue(sup.store)
            sup._try_flush_pending_queue = MagicMock(return_value=1)
            now = time.time()

            sup._service_pending_queue(
                now,
                now,
                0.0,
                5.0,
                aggressive=True,
            )

            sup._try_flush_pending_queue.assert_called_once_with(
                context="GPU cooldown"
            )

    def test_skip_repeat_graceful_stop_during_cooldown(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sup = self._make_supervisor(tmp)
            sup._cooldown_until = time.time() + 60
            sup.backend.is_running = False
            sup._graceful_gpu_stop = MagicMock()

            snap = MagicMock()
            snap.temperature_c = 80
            snap.used_mib = 1000
            snap.headroom_mib = 20000
            snap.total_mib = 24000

            ok = sup._apply_gpu_safety(snap)

            self.assertTrue(ok)
            sup._graceful_gpu_stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()