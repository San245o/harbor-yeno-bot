"""Receive-time BTC model with explicit observation gaps and bounded memory."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .execution import number


def valid_reference(obs: dict) -> bool:
    ref = obs.get("reference")
    now = number(obs.get("timestamp"))
    if not isinstance(ref, dict) or now is None or ref.get("targetProvisional") is not False:
        return False
    mid, target = number(ref.get("btcMidUsd")), number(ref.get("openingTargetUsd"))
    observed, target_at = number(ref.get("observedAt")), number(ref.get("targetObservedAt"))
    return (mid is not None and mid > 0 and target is not None and target > 0
            and observed is not None and 0 <= now - observed <= 1.5
            and target_at is not None and target_at <= now)


@dataclass(frozen=True)
class Forecast:
    yes: float
    sigma: float
    twap_covered: bool


class History:
    def __init__(self) -> None:
        self.points: dict[int, tuple[float, float]] = {}
        self.last_reference_at = -math.inf

    def observe(self, obs: dict, *, without_target: bool = False) -> None:
        if without_target:
            ref = obs.get("reference", {})
            now, mid = number(obs.get("timestamp")), number(ref.get("btcMidUsd"))
            observed = number(ref.get("observedAt"))
            if (now is None or mid is None or mid <= 0 or observed is None
                    or not 0 <= now - observed <= 1.5):
                return
        elif not valid_reference(obs):
            return
        ref = obs["reference"]
        ts, mid = float(ref["observedAt"]), float(ref["btcMidUsd"])
        if ts <= self.last_reference_at:
            return
        self.last_reference_at = ts
        self.points[math.floor(ts)] = (ts, mid)
        cutoff = math.floor(float(obs["timestamp"])) - 660
        self.points = {s: p for s, p in self.points.items() if s >= cutoff}

    def sigma(self, now: float) -> float | None:
        # A sparse feed makes the next observed price change less predictable.
        # Keep the full move between sampled seconds in the entry risk estimate.
        first = math.floor(now) - 300
        prices = [mid for second, (ts, mid) in self.points.items() if second >= first and ts <= now]
        if len(prices) < 60:
            return None
        moves = [b - a for a, b in zip(prices, prices[1:])]
        mean = sum(moves) / len(moves)
        sigma = math.sqrt(sum((move - mean) ** 2 for move in moves) / len(moves))
        return sigma if sigma > 1e-9 else None

    def integral(self, start: float, end: float) -> float | None:
        """Causal previous-tick integral. Missing intervals >3 s invalidate it."""
        if start >= end:
            return 0.0
        points = sorted(p for p in self.points.values() if p[0] <= end)
        prior = [p for p in points if p[0] <= start]
        if not prior or start - prior[-1][0] > 3:
            return None
        previous_at, price = prior[-1]
        cursor, area = start, 0.0
        for ts, mid in points:
            if ts <= start:
                continue
            if ts - previous_at > 3:
                return None
            area += price * (ts - cursor)
            cursor, previous_at, price = ts, ts, mid
        if end - previous_at > 3:
            return None
        return area + price * (end - cursor)

    def forecast(self, obs: dict, *, volatility_multiplier: float = 1.25,
                 settlement: str = "conservative") -> Forecast | None:
        if not valid_reference(obs):
            return None
        now = float(obs["timestamp"])
        seconds = number(obs.get("market", {}).get("secondsToClose"))
        sigma = self.sigma(now)
        if seconds is None or seconds <= 0 or sigma is None:
            return None
        ref = obs["reference"]
        mid, target = float(ref["btcMidUsd"]), float(ref["openingTargetUsd"])
        sigma *= volatility_multiplier
        def probability(mean: float, variance_time: float) -> float:
            z = (mean - target) / (sigma * math.sqrt(max(variance_time, 1e-6)))
            return 0.5 + 0.5 * math.erf(z / math.sqrt(2))
        spot = probability(mid, seconds)
        if seconds >= 60:
            twap = probability(mid, seconds - 40)
            covered = True
        else:
            area = self.integral(now + seconds - 60, now)
            covered = area is not None
            twap = probability((area + mid * seconds) / 60, seconds ** 3 / 10800) if covered else None
        if settlement == "spot":
            return Forecast(spot, sigma, covered)
        if twap is None:
            return None
        if settlement == "twap60":
            return Forecast(twap, sigma, covered)
        # The downloadable contract does not specify settlement averaging. Use
        # the probability closest to 0.5, or 0.5 when the two models disagree.
        yes = min(spot, twap) if min(spot, twap) >= 0.5 else max(spot, twap)
        if (spot - 0.5) * (twap - 0.5) < 0:
            yes = 0.5
        return Forecast(yes, sigma, covered)
