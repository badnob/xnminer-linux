from __future__ import annotations

import multiprocessing as mp
import time
from typing import Callable

from config.settings import Settings
from core.models import BlockHit
from mining.argon2_common import argon2_hash, classify_hash
from mining.base import MineBatchResult, MinerBackend


def _worker_loop(
    lane_id: int,
    salt_hex: str,
    memory_cost: int,
    time_cost: int,
    parallelism: int,
    hash_len: int,
    key_fn_factory: Callable[[int], Callable[[int], str]],
    stop_event: mp.Event,
    hit_queue: mp.Queue,
    count_value: mp.Value,
) -> None:
    key_fn = key_fn_factory(lane_id)
    local_index = lane_id * 1_000_000
    while not stop_event.is_set():
        batch = 50
        for _ in range(batch):
            if stop_event.is_set():
                break
            key = key_fn(local_index)
            local_index += 1
            h = argon2_hash(
                key,
                salt_hex,
                memory_cost=memory_cost,
                time_cost=time_cost,
                parallelism=parallelism,
                hash_len=hash_len,
            )
            with count_value.get_lock():
                count_value.value += 1
            block_type = classify_hash(h)
            if block_type:
                hit_queue.put(
                    {
                        "key": key,
                        "hash_str": h,
                        "block_type": block_type,
                        "attempts": local_index,
                        "lane": lane_id,
                    }
                )
                return


class CpuArgon2Backend(MinerBackend):
    def __init__(
        self,
        settings: Settings,
        key_fn_factory: Callable[[int], Callable[[int], str]],
        strategy_name: str,
    ) -> None:
        self.settings = settings
        self.key_fn_factory = key_fn_factory
        self.strategy_name = strategy_name
        self._lanes = settings.cpu_lanes
        self._processes: list[mp.Process] = []
        self._stop_event = mp.Event()
        self._hit_queue: mp.Queue = mp.Queue()
        self._count_value = mp.Value("Q", 0)
        self._last_count = 0

    def start(self) -> None:
        self._spawn_workers()

    def stop(self) -> None:
        self._stop_event.set()
        for proc in self._processes:
            proc.join(timeout=2)
            if proc.is_alive():
                proc.terminate()
        self._processes.clear()

    def set_lanes(self, lanes: int) -> None:
        lanes = max(1, lanes)
        if lanes == self._lanes and self._processes:
            return
        self.stop()
        self._lanes = lanes
        self._stop_event = mp.Event()
        self._hit_queue = mp.Queue()
        self._spawn_workers()

    def _spawn_workers(self) -> None:
        for lane in range(self._lanes):
            proc = mp.Process(
                target=_worker_loop,
                args=(
                    lane,
                    self.settings.salt_hex,
                    self.settings.memory_cost,
                    self.settings.time_cost,
                    self.settings.parallelism,
                    self.settings.hash_len,
                    self.key_fn_factory,
                    self._stop_event,
                    self._hit_queue,
                    self._count_value,
                ),
                daemon=True,
            )
            proc.start()
            self._processes.append(proc)

    def mine_batch(self, batch_size: int) -> MineBatchResult:
        deadline = time.time() + 1.0
        hit = None
        while time.time() < deadline:
            try:
                raw = self._hit_queue.get(timeout=0.05)
                hit = BlockHit(
                    key=raw["key"],
                    hash_str=raw["hash_str"],
                    block_type=raw["block_type"],
                    attempts=raw["attempts"],
                    strategy=self.strategy_name,
                )
                self._stop_event.set()
                break
            except Exception:
                pass

        with self._count_value.get_lock():
            total = self._count_value.value
        done = total - self._last_count
        self._last_count = total
        return MineBatchResult(hashes_done=max(done, 0), hit=hit)

    @property
    def active_lanes(self) -> int:
        return self._lanes