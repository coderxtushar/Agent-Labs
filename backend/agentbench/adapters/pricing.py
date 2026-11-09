"""Token price table used for **estimated** cost figures.

Every cost the platform shows carries a ``cost_source``.  Numbers derived here
are labelled ``estimated:<PRICE_TABLE_ID>`` so a report can never present a
guess as a provider-billed amount.

Prices are USD per million tokens, transcribed by hand from public pricing pages
on the date in the table ID.  They go stale.  When a model is not in the table
the platform reports ``None`` rather than substituting a similar model's price.
Override or extend the table with ``AGENTBENCH_PRICE_TABLE_PATH`` pointing at a
JSON file of the same shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PRICE_TABLE_ID = "builtin-price-table@2026-10"

#: model-id prefix -> (input USD per 1M tokens, output USD per 1M tokens)
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "llama-3.1-8b": (0.05, 0.08),
    "llama-3.3-70b": (0.59, 0.79),
    "qwen2.5-coder-32b": (0.08, 0.18),
    "deepseek-chat": (0.27, 1.10),
}

_cache: dict[str, tuple[float, float]] | None = None


def _table() -> dict[str, tuple[float, float]]:
    global _cache
    if _cache is not None:
        return _cache
    table = dict(_PRICES)
    override = os.environ.get("AGENTBENCH_PRICE_TABLE_PATH")
    if override:
        path = Path(override)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for key, value in data.items():
                    if (
                        isinstance(value, (list, tuple))
                        and len(value) == 2
                        and all(isinstance(v, (int, float)) for v in value)
                    ):
                        table[str(key)] = (float(value[0]), float(value[1]))
            except (json.JSONDecodeError, OSError):
                pass
    _cache = table
    return table


def lookup_prices(model: str) -> tuple[float, float] | None:
    """Longest-prefix match, so ``gpt-4o-2024-11-20`` resolves to ``gpt-4o``."""
    if not model:
        return None
    normalised = model.strip().lower()
    # Strip a provider namespace such as "openai/" or "anthropic/".
    if "/" in normalised:
        normalised = normalised.split("/")[-1]
    table = _table()
    best: tuple[int, tuple[float, float]] | None = None
    for prefix, prices in table.items():
        if normalised.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), prices)
    return best[1] if best else None


def estimate_cost_usd(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    """Estimated USD cost, or ``None`` when it cannot be computed honestly.

    Returns ``None`` if the provider reported no usage or the model is unknown -
    a missing number is more useful than a fabricated one.
    """
    if input_tokens is None and output_tokens is None:
        return None
    prices = lookup_prices(model)
    if prices is None:
        return None
    input_price, output_price = prices
    cost = ((input_tokens or 0) / 1_000_000) * input_price + (
        (output_tokens or 0) / 1_000_000
    ) * output_price
    return round(cost, 6)


def known_models() -> list[str]:
    return sorted(_table())


def reset_cache() -> None:
    global _cache
    _cache = None
