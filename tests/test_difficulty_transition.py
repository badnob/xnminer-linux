import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from block_queue.store import DIFFICULTY_CHANGE_REASON, BlockStore
from config.settings import load_settings
from core.models import BlockHit
from core.supervisor import Supervisor


class DifficultyTransitionTests(unittest.TestCase):
    def _make_supervisor(self, tmp: str) -> Supervisor:
        root = Path(tmp)
        base = load_settings()
        settings = base.__class__(
            **{
                **base.__dict__,
                "backend": "cuda",
                "sample_interval_s": 5,
                "db_path": root / "blocks.db",
                "jsonl_path": root / "queue.jsonl",
                "rejected_jsonl_path": root / "rejected.jsonl",
                "log_path": root / "session.log",
            }
        )
        backend = MagicMock()
        backend.difficulty = 100
        with patch.object(Supervisor, "_build_backend", return_value=backend):
            sup = Supervisor(settings, use_dashboard=False)
        sup.store = BlockStore(
            settings.db_path,
            settings.jsonl_path,
            settings.rejected_jsonl_path,
        )
        sup.submit_pool = MagicMock()
        sup.submit_pool.run = MagicMock()
        sup.submitter = MagicMock()
        sup._network_difficulty = 1100
        return sup

    def test_difficulty_change_starts_defer_window(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sup = self._make_supervisor(tmp)
            sup._apply_network_difficulty(100)
            self.assertTrue(sup._in_difficulty_transition())
            self.assertEqual(sup._apply_network_difficulty(100), 100)

    def test_hit_during_transition_is_queued_without_live_submit(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sup = self._make_supervisor(tmp)
            sup._defer_submit_until = time.time() + 30
            hit = BlockHit(
                key="0x" + "11" * 16,
                hash_str="rawhash",
                block_type="XNM",
                attempts=1,
                strategy="random",
                memory_cost=100,
            )
            sup.handle_hit(hit)
            sup.submit_pool.run.assert_not_called()
            sup.submitter.submit.assert_not_called()
            pending = sup.store.pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["queue_reason"], DIFFICULTY_CHANGE_REASON)
            self.assertEqual(pending[0]["memory_cost"], 100)

    def test_flush_held_during_transition(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sup = self._make_supervisor(tmp)
            sup.store.enqueue(
                BlockHit(
                    key="0x" + "22" * 16,
                    hash_str="a" * 64,
                    block_type="XNM",
                    attempts=1,
                    strategy="random",
                ),
                reason="test",
            )
            sup._defer_submit_until = time.time() + 30
            sup.refresh_network = MagicMock(return_value=True)
            sup.flusher = MagicMock()
            flushed = sup._try_flush_pending_queue(context="queue service")
            self.assertEqual(flushed, 0)
            sup.flusher.flush.assert_not_called()


if __name__ == "__main__":
    unittest.main()