"""Transparent causal toy markets for stress testing, never performance evidence.

Books are modelled from lagged spot probabilities, with exogenous depth and
regime changes. Neither bot influences generation. No future prices enter quotes.
"""
from __future__ import annotations

import math
import random


def events(seed: int = 17, markets: int = 288, regime: str = "smooth"):
    rng = random.Random(seed)
    start = 1_800_000_000.0
    mid = 80_000.0
    for index in range(markets):
        target = mid
        sigma = 3.0 if regime in ("smooth", "lagged") else 8.0
        drift = rng.choice((-1, 1)) * rng.uniform(0.4, 1.0)
        path = []
        cached_books = {}
        for elapsed in range(301):
            now, remaining = start + index * 300 + elapsed, 300 - elapsed
            if elapsed:
                mid += rng.gauss(drift, sigma)
                if regime == "choppy" and elapsed in (120, 210):
                    drift = -drift * 1.4
                    mid += rng.gauss(0, sigma * 10)
            path.append(mid)
            lagged = path[max(0, len(path) - (21 if regime == "lagged" else 4))]
            probability = 0.5 + 0.5 * math.erf((lagged - target) / (sigma * math.sqrt(max(remaining, 1)) * math.sqrt(2)))
            books = {}
            for side, fair in (("YES", probability), ("NO", 1 - probability)):
                # Asynchronous updates exercise the outcome-specific latency rule.
                if side in cached_books and elapsed % 7 == (0 if side == "YES" else 3):
                    books[side] = cached_books[side]
                    continue
                ask = min(0.99, max(0.02, math.ceil((fair + 0.006) * 100) / 100))
                bid = min(0.98, max(0.01, math.floor((fair - 0.006) * 100) / 100))
                bid_depth = rng.uniform(20, 60)
                if regime == "thin" and index % 3 == 0:
                    bid_depth = rng.uniform(0.2, 3)
                asks = [[ask, 30]]
                bids = [[bid, bid_depth]]
                if remaining < 25 and regime == "thin":
                    asks = []
                    bids = [[bid, min(bid_depth, 1.5)]]
                books[side] = {"observedAt": now, "minOrderSize": 5, "asks": asks, "bids": bids}
                cached_books[side] = books[side]
            event = {"timestamp": now, "market": {"id": f"synthetic-{index}", "secondsToClose": remaining},
                     "books": books,
                     "reference": {"btcMidUsd": mid, "openingTargetUsd": target, "observedAt": now,
                                   "targetObservedAt": start + index * 300, "targetProvisional": False}}
            if remaining == 0:
                # Resolution is emitted only at close, withheld from decisions.
                event["resolvedOutcome"] = "YES" if sum(path[-60:]) / 60 >= target else "NO"
            yield event
