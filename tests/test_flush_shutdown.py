import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from block_queue.flush import QueueFlusher
from block_queue.policy import in_xuni_window, ready_to_flush
from config.settings import load_settings
from core.models import BlockHit
from monitoring.logger import SessionLogger
from block_queue.store import OUTSIDE_XUNI_WINDOW_REASON, SHUTDOWN_PENDING_REASON, BlockStore


class ReadyToFlushTests(unittest.TestCase):
    def test_xnm_ready_anytime(self) -> None:
        outside = datetime(2026, 7, 13, 12, 30, 0)
        self.assertEqual(ready_to_flush("XNM", now=outside), (True, "ready"))

    def test_xuni_outside_window_not_ready(self) -> None:
        outside = datetime(2026, 7, 13, 12, 30, 0)
        self.assertEqual(
            ready_to_flush("XUNI", now=outside),
            (False, "waiting_for_xuni_window"),
        )

    def test_xuni_inside_window_ready(self) -> None:
        inside = datetime(2026, 7, 13, 12, 58, 0)
        self.assertEqual(ready_to_flush("XUNI", now=inside), (True, "ready"))


class XuniWindowTests(unittest.TestCase):
    def test_minute_55_is_outside_window(self) -> None:
        self.assertFalse(in_xuni_window(datetime(2026, 7, 13, 12, 55, 0)))

    def test_minute_56_is_inside_window(self) -> None:
        self.assertTrue(in_xuni_window(datetime(2026, 7, 13, 12, 56, 0)))

    def test_minute_04_is_inside_window(self) -> None:
        self.assertTrue(in_xuni_window(datetime(2026, 7, 13, 13, 4, 59)))

    def test_minute_05_is_outside_window(self) -> None:
        self.assertFalse(in_xuni_window(datetime(2026, 7, 13, 13, 5, 0)))


class ShutdownFlushTests(unittest.TestCase):
    def _make_flusher(self, tmp: str) -> tuple[QueueFlusher, BlockStore, MagicMock]:
        root = Path(tmp)
        base = load_settings()
        settings = base.__class__(
            **{
                **base.__dict__,
                "db_path": root / "blocks.db",
                "jsonl_path": root / "queue.jsonl",
                "rejected_jsonl_path": root / "rejected.jsonl",
                "log_path": root / "session.log",
            }
        )
        store = BlockStore(
            settings.db_path,
            settings.jsonl_path,
            settings.rejected_jsonl_path,
        )
        submitter = MagicMock()
        logger = SessionLogger(settings.log_path, echo_console=False)
        flusher = QueueFlusher(
            store,
            submitter,
            logger,
            settings,
            lambda: 1100,
        )
        return flusher, store, submitter

    def _enqueue(self, store: BlockStore, block_type: str, key: str) -> None:
        store.enqueue(
            BlockHit(
                key=key,
                hash_str="a" * 64,
                block_type=block_type,
                attempts=1,
                strategy="random",
                hps=1000.0,
            ),
            reason="test",
        )

    @patch("block_queue.flush.prepare_hit_for_submit")
    def test_shutdown_submits_xnm_and_xblk(self, mock_prepare) -> None:
        mock_prepare.side_effect = lambda hit, **_: hit
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            flusher, store, submitter = self._make_flusher(tmp)
            self._enqueue(store, "XNM", "0x" + "11" * 16)
            self._enqueue(store, "XBLK", "0x" + "22" * 16)
            submitter.submit.return_value = {"ok": True, "status": 200, "body": "ok"}

            flushed = flusher.flush(on_shutdown=True)

            self.assertEqual(flushed, 2)
            self.assertEqual(submitter.submit.call_count, 2)
            self.assertEqual(store.pending_count(), 0)

    @patch("block_queue.flush.prepare_hit_for_submit")
    @patch("block_queue.flush.ready_to_flush")
    def test_shutdown_holds_xuni_outside_window(
        self, mock_ready, mock_prepare
    ) -> None:
        mock_prepare.side_effect = lambda hit, **_: hit

        def ready_side_effect(block_type, *, now=None):
            if block_type == "XUNI":
                return False, "waiting_for_xuni_window"
            return True, "ready"

        mock_ready.side_effect = ready_side_effect

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            flusher, store, submitter = self._make_flusher(tmp)
            self._enqueue(store, "XNM", "0x" + "11" * 16)
            self._enqueue(store, "XUNI", "0x" + "33" * 16)
            submitter.submit.return_value = {"ok": True, "status": 200, "body": "ok"}

            flushed = flusher.flush(on_shutdown=True)

            self.assertEqual(flushed, 1)
            self.assertEqual(submitter.submit.call_count, 1)
            self.assertEqual(store.pending_count(), 1)
            pending = store.pending()
            self.assertEqual(pending[0]["block_type"], "XUNI")


    @patch("block_queue.flush.prepare_hit_for_submit")
    def test_shutdown_marks_failed_xnm_for_next_start(self, mock_prepare) -> None:
        mock_prepare.side_effect = lambda hit, **_: hit
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            flusher, store, submitter = self._make_flusher(tmp)
            self._enqueue(store, "XNM", "0x" + "66" * 16)
            submitter.submit.return_value = {
                "ok": False,
                "status": 503,
                "body": "unavailable",
            }

            flushed = flusher.flush(on_shutdown=True)

            self.assertEqual(flushed, 0)
            self.assertEqual(store.pending_count(), 1)
            pending = store.pending()
            self.assertEqual(pending[0]["queue_reason"], SHUTDOWN_PENDING_REASON)

    @patch("block_queue.flush.prepare_hit_for_submit")
    def test_shutdown_marks_held_xuni_reason(self, mock_prepare) -> None:
        mock_prepare.side_effect = lambda hit, **_: hit
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            flusher, store, submitter = self._make_flusher(tmp)
            self._enqueue(store, "XUNI", "0x" + "77" * 16)

            with patch("block_queue.flush.ready_to_flush", return_value=(False, "waiting")):
                flusher.flush(on_shutdown=True)

            pending = store.pending()
            self.assertEqual(pending[0]["queue_reason"], OUTSIDE_XUNI_WINDOW_REASON)

    @patch("block_queue.flush.prepare_hit_for_submit")
    def test_defer_to_next_start_skips_submit(self, mock_prepare) -> None:
        mock_prepare.side_effect = lambda hit, **_: hit
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            flusher, store, submitter = self._make_flusher(tmp)
            self._enqueue(store, "XNM", "0x" + "88" * 16)
            self._enqueue(store, "XBLK", "0x" + "99" * 16)

            deferred = flusher.defer_to_next_start()

            self.assertEqual(deferred, 2)
            submitter.submit.assert_not_called()
            pending = store.pending()
            self.assertEqual(len(pending), 2)
            for row in pending:
                self.assertEqual(row["queue_reason"], SHUTDOWN_PENDING_REASON)

    @patch("block_queue.flush.prepare_hit_for_submit")
    def test_defer_holds_xuni_outside_window(self, mock_prepare) -> None:
        mock_prepare.side_effect = lambda hit, **_: hit
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            flusher, store, submitter = self._make_flusher(tmp)
            self._enqueue(store, "XUNI", "0x" + "aa" * 16)

            with patch("block_queue.flush.ready_to_flush", return_value=(False, "waiting")):
                deferred = flusher.defer_to_next_start()

            self.assertEqual(deferred, 1)
            submitter.submit.assert_not_called()
            pending = store.pending()
            self.assertEqual(pending[0]["queue_reason"], OUTSIDE_XUNI_WINDOW_REASON)

    @patch("block_queue.flush.prepare_hit_for_submit")
    def test_held_blocks_remain_for_next_startup_flush(self, mock_prepare) -> None:
        mock_prepare.side_effect = lambda hit, **_: hit
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            flusher, store, submitter = self._make_flusher(tmp)
            self._enqueue(store, "XUNI", "0x" + "44" * 16)
            self._enqueue(store, "XNM", "0x" + "55" * 16)

            with patch("block_queue.flush.ready_to_flush") as mock_ready:
                mock_ready.side_effect = lambda block_type, *, now=None: (
                    (False, "waiting_for_xuni_window")
                    if block_type == "XUNI"
                    else (True, "ready")
                )
                submitter.submit.return_value = {"ok": True, "status": 200, "body": "ok"}
                flusher.flush(on_shutdown=True)

            self.assertEqual(store.pending_count(), 1)
            self.assertEqual(store.pending_by_type()["XUNI"], 1)
            self.assertEqual(store.pending_by_type()["XNM"], 0)

            flusher2, store2, submitter2 = self._make_flusher(tmp)
            self.assertEqual(store2.pending_count(), 1)
            submitter2.submit.return_value = {"ok": True, "status": 200, "body": "ok"}
            with patch("block_queue.flush.ready_to_flush", return_value=(True, "ready")):
                flushed = flusher2.flush()
            self.assertEqual(flushed, 1)
            self.assertEqual(store2.pending_count(), 0)


if __name__ == "__main__":
    unittest.main()