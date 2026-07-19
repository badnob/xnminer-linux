from __future__ import annotations

from strategies.base import KeyFactory
from strategies.fibonacci_strategy import make_fibonacci_key_fn
from strategies.random_strategy import make_random_key_fn

STRATEGIES: dict[str, KeyFactory] = {
    "random": make_random_key_fn,
    "fibonacci": make_fibonacci_key_fn,
}


def list_strategies() -> list[str]:
    return sorted(STRATEGIES.keys())


def build_key_factory(name: str) -> KeyFactory:
    factory = STRATEGIES.get(name.lower())
    if not factory:
        raise ValueError(f"Unknown strategy '{name}'. Available: {', '.join(list_strategies())}")
    return factory