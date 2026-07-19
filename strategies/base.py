from __future__ import annotations

from typing import Callable

KeyFactory = Callable[[int], Callable[[int], str]]