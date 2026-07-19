import unittest
from unittest.mock import patch

from block_queue.submit_cpu import (
    DEFAULT_SUBMIT_CPU_FRACTION,
    SubmitCpuPool,
    submit_cpu_affinity,
    submit_worker_count,
)


class SubmitCpuBudgetTests(unittest.TestCase):
    @patch("block_queue.submit_cpu.os.cpu_count", return_value=24)
    def test_thirty_percent_of_cores(self, _mock_cpu: object) -> None:
        self.assertEqual(submit_worker_count(0.30), 7)
        self.assertEqual(submit_cpu_affinity(0.30), list(range(7)))

    @patch("block_queue.submit_cpu.os.cpu_count", return_value=4)
    def test_minimum_one_core(self, _mock_cpu: object) -> None:
        self.assertEqual(submit_worker_count(0.30), 1)

    @patch("block_queue.submit_cpu.os.cpu_count", return_value=None)
    def test_missing_cpu_count_falls_back_to_one(self, _mock_cpu: object) -> None:
        self.assertEqual(submit_worker_count(DEFAULT_SUBMIT_CPU_FRACTION), 1)

    @patch("block_queue.submit_cpu.os.cpu_count", return_value=16)
    def test_pool_parallelism_for_single_caps_configured(self, _mock_cpu: object) -> None:
        pool = SubmitCpuPool(0.30)
        self.assertEqual(pool.workers, 4)
        self.assertEqual(pool.parallelism_for_single(8), 4)
        self.assertEqual(pool.parallelism_for_single(1), 1)
        pool.shutdown()

    @patch("block_queue.submit_cpu.os.cpu_count", return_value=10)
    @patch("block_queue.submit_cpu.psutil.Process")
    def test_pool_pins_workers_to_allowed_cpus(
        self, mock_process_cls, _mock_cpu: object
    ) -> None:
        pool = SubmitCpuPool(0.30)
        pool.run(lambda: None)
        mock_process_cls.return_value.cpu_affinity.assert_called()
        pool.shutdown()


if __name__ == "__main__":
    unittest.main()