"""Cap CPU used for Argon2 verify + block submission."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

import psutil

T = TypeVar("T")
DEFAULT_SUBMIT_CPU_FRACTION = 0.30


def submit_worker_count(fraction: float = DEFAULT_SUBMIT_CPU_FRACTION) -> int:
    cores = os.cpu_count() or 1
    return max(1, int(cores * fraction))


def submit_cpu_affinity(fraction: float = DEFAULT_SUBMIT_CPU_FRACTION) -> list[int]:
    count = submit_worker_count(fraction)
    return list(range(count))


def _init_submit_worker(allowed_cpus: list[int]) -> None:
    try:
        psutil.Process().cpu_affinity(allowed_cpus)
    except (AttributeError, NotImplementedError, psutil.Error):
        pass


class SubmitCpuPool:
    """Thread pool pinned to a fraction of host cores for submit-side CPU work."""

    def __init__(self, fraction: float = DEFAULT_SUBMIT_CPU_FRACTION) -> None:
        self.fraction = fraction
        self.workers = submit_worker_count(fraction)
        self.allowed_cpus = submit_cpu_affinity(fraction)
        self._executor = ThreadPoolExecutor(
            max_workers=self.workers,
            initializer=_init_submit_worker,
            initargs=(self.allowed_cpus,),
            thread_name_prefix="submit",
        )

    def parallelism_for_single(self, configured: int = 1) -> int:
        """Argon2 lanes for one block — stay within the submit core budget."""
        return max(1, min(configured, self.workers))

    def run(self, fn: Callable[..., T], *args, **kwargs) -> T:
        future = self._executor.submit(fn, *args, **kwargs)
        return future.result()

    def map(self, fn: Callable[..., T], items: list) -> list[T]:
        if not items:
            return []
        if len(items) == 1:
            return [self.run(fn, items[0])]
        futures = [self._executor.submit(fn, item) for item in items]
        return [future.result() for future in futures]

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)