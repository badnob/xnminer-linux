from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.models import BlockHit


@dataclass
class MineBatchResult:
    hashes_done: int
    hit: BlockHit | None
    aborted: bool = False
    abort_reason: str = ""


class MinerBackend(ABC):
    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def set_lanes(self, lanes: int) -> None:
        ...

    @abstractmethod
    def mine_batch(self, batch_size: int) -> MineBatchResult:
        ...

    @property
    @abstractmethod
    def active_lanes(self) -> int:
        ...