from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from config.settings import load_settings
from mining.backends.cuda_native import CudaNativeBackend
from mining.cuda_engine import CudaBatchResult, CudaMatch


class CudaParallelLaneTests(unittest.TestCase):
    def _backend(self, tmp: str) -> CudaNativeBackend:
        root = Path(tmp)
        base = load_settings()
        settings = base.__class__(
            **{
                **base.__dict__,
                "log_path": root / "session.log",
            }
        )
        with patch.object(CudaNativeBackend, "__init__", lambda self, *a, **k: None):
            backend = CudaNativeBackend(settings, "random")
        backend.settings = settings
        backend.strategy_name = "random"
        backend._started = True
        backend._lanes = 4
        backend._batch_per_lane = 1000
        backend._difficulty = 100
        backend._abort_check = None
        backend._lane_engines = {}
        backend._parallel_mode = "native"
        backend._engine = MagicMock()
        backend._engine.parallel_lanes_supported = True
        return backend

    def _ok_result(self) -> CudaBatchResult:
        return CudaBatchResult(
            ok=True,
            error="",
            attempts=1000,
            hashrate=50000.0,
            elapsed_ms=10.0,
            batch_size=1000,
            matches=[],
        )

    def test_sync_lane_engines_on_replan(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            backend = self._backend(tmp)
            backend._engine.set_lane_count.reset_mock()
            backend._sync_lane_engines()
            backend._engine.set_lane_count.assert_called_once_with(4)

    def test_parallel_mine_batch_uses_all_lanes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            backend = self._backend(tmp)
            backend._engine.run_lane_batch.side_effect = (
                lambda lane, **_: self._ok_result()
            )

            result = backend.mine_batch(batch_size=1000)

            self.assertEqual(result.hashes_done, 4000)
            self.assertEqual(backend._engine.run_lane_batch.call_count, 4)
            called_lanes = {
                call.args[0] for call in backend._engine.run_lane_batch.call_args_list
            }
            self.assertEqual(called_lanes, {0, 1, 2, 3})

    def test_single_lane_still_uses_lane_zero(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            backend = self._backend(tmp)
            backend._lanes = 1
            backend._parallel_mode = "sequential"
            backend._engine.parallel_lanes_supported = False
            backend._engine.run_batch.return_value = self._ok_result()

            result = backend.mine_batch(batch_size=1000)

            self.assertEqual(result.hashes_done, 1000)
            backend._engine.run_batch.assert_called_once()
            self.assertEqual(backend._engine.run_batch.call_args.kwargs["key_prefix"], "0000")

    def test_dll_copy_mode_runs_lanes_in_parallel(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            backend = self._backend(tmp)
            backend._engine.parallel_lanes_supported = False
            backend._parallel_mode = "dll-copies"
            copy_engines = {
                1: MagicMock(),
                2: MagicMock(),
                3: MagicMock(),
            }
            for eng in copy_engines.values():
                eng.run_batch.return_value = self._ok_result()
            backend._lane_engines = copy_engines
            backend._engine.run_batch.return_value = self._ok_result()

            result = backend.mine_batch(batch_size=1000)

            self.assertEqual(result.hashes_done, 4000)
            backend._engine.run_batch.assert_called_once()
            self.assertEqual(sum(e.run_batch.call_count for e in copy_engines.values()), 3)

    def test_sync_lane_engines_spawns_dll_copies_without_native_api(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            base = load_settings()
            settings = base.__class__(
                **{
                    **base.__dict__,
                    "log_path": root / "session.log",
                    "cuda_dll_path": root / "xen_cuda.dll",
                }
            )
            settings.cuda_dll_path.write_bytes(b"fake-dll")
            with patch.object(CudaNativeBackend, "__init__", lambda self, *a, **k: None):
                backend = CudaNativeBackend(settings, "random")
            backend.settings = settings
            backend._started = True
            backend._lanes = 3
            backend._lane_workers_dir = root / "cuda_lane_workers"
            backend._lane_engines = {}
            backend._parallel_mode = "sequential"
            backend._engine = MagicMock()
            backend._engine.parallel_lanes_supported = False

            mock_engine_cls = MagicMock()
            created: list[MagicMock] = []

            def make_engine(path):
                eng = MagicMock()
                created.append(eng)
                return eng

            mock_engine_cls.side_effect = make_engine
            with patch("mining.backends.cuda_native.CudaEngine", mock_engine_cls):
                with patch("mining.backends.cuda_native.shutil.copy2") as copy2:
                    backend._sync_lane_engines()

            self.assertEqual(backend._parallel_mode, "dll-copies")
            self.assertEqual(set(backend._lane_engines), {1, 2})
            self.assertEqual(copy2.call_count, 2)
            self.assertEqual(len(created), 2)
            for eng in created:
                eng.init.assert_called_once()


if __name__ == "__main__":
    unittest.main()