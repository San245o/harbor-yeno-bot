"""Decision-time estimates only; the external evaluator owns every fill."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def levels(rows: Any, *, buy: bool) -> list[tuple[float, float]]:
    """Reject malformed/non-finite levels and aggregate identical prices."""
    merged: dict[float, float] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        p, q = number(row[0]), number(row[1])
        if p is not None and q is not None and 0 < p < 1 and q > 0:
            merged[p] = merged.get(p, 0.0) + q
    return sorted(((p, q) for p, q in merged.items() if math.isfinite(q)), reverse=not buy)


@dataclass(frozen=True)
class Quote:
    shares: float = 0.0
    gross: float = 0.0
    fees: float = 0.0

    @property
    def cost(self) -> float:
        return self.gross + self.fees

    @property
    def net(self) -> float:
        return self.gross - self.fees


def quote(book: list[tuple[float, float]], shares: float) -> Quote:
    """Walk all available levels; a partial quote explicitly reports its shares."""
    left, gross, protocol, done = max(0.0, shares), 0.0, 0.0, 0.0
    for p, available in book:
        q = min(left, available)
        gross += p * q
        protocol += 0.07 * q * p * (1.0 - p)
        done += q
        left -= q
        if left <= 1e-10:
            break
    fees = round(protocol + 1e-12, 5) + round(0.01 * gross + 1e-12, 5)
    return Quote(done, gross, fees)


def affordable(book: list[tuple[float, float]], cash: float) -> Quote:
    if not book or cash <= 0:
        return Quote()
    lo, hi = 0.0, min(sum(q for _, q in book), cash / book[0][0])
    full = quote(book, hi)
    if full.cost <= cash:
        return full
    for _ in range(38):
        mid = (lo + hi) / 2
        if quote(book, mid).cost <= cash:
            lo = mid
        else:
            hi = mid
    return quote(book, lo)


def shifted(book: list[tuple[float, float]], amount: float) -> list[tuple[float, float]]:
    # Do not invent executable levels beyond the price boundaries.
    return [(p + amount, q) for p, q in book if 0 < p + amount < 1]


def floor_cash(value: float) -> float:
    return max(0.0, math.floor(value * 1_000_000) / 1_000_000)
