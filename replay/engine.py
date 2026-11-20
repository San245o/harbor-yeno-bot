"""Causal L2 replay with independent fill arithmetic and completed-cycle volume.

Input rows require per-outcome `observedAt` receive timestamps. No midpoint
fills, implicit quote refreshes, or settlement volume. This is a reconstruction
of the public contract, with no claim of official evaluator parity.
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass
from typing import Callable, Iterable


def parsed(rows: list, descending: bool = False) -> list[tuple[float, float]]:
    result = []
    for p, q in rows:
        p, q = float(p), float(q)
        if not math.isfinite(p) or not math.isfinite(q) or not 0 < p < 1 or q <= 0:
            raise ValueError("Invalid replay book level")
        result.append((p, q))
    return sorted(result, reverse=descending)


def fill(levels: list[tuple[float, float]], *, quantity: float = math.inf,
         budget: float = math.inf) -> tuple[float, float, float]:
    """Independent reference arithmetic. Rounded fees apply to the whole leg."""
    taken: list[tuple[float, float]] = []
    def totals(rows: list[tuple[float, float]]) -> tuple[float, float, float]:
        qty = sum(q for _, q in rows)
        gross = math.fsum(p * q for p, q in rows)
        fee = round(math.fsum(0.07 * p * (1 - p) * q for p, q in rows) + 1e-12, 5)
        fee += round(gross * 0.01 + 1e-12, 5)
        return qty, gross, fee
    left = quantity
    for p, available in levels:
        q = min(available, left)
        _, gross, fee = totals(taken + [(p, q)])
        if gross + fee > budget:
            lo, hi = 0.0, q
            for _ in range(42):
                mid = (lo + hi) / 2
                _, g, f = totals(taken + [(p, mid)])
                if g + f <= budget:
                    lo = mid
                else:
                    hi = mid
            q = lo
        if q > 0:
            taken.append((p, q))
            left -= q
        if q < available - 1e-9 or left <= 1e-9:
            break
    return totals(taken)


@dataclass
class Metrics:
    evidence: str = "local diagnostic; no official parity validation"
    qualified: bool = False
    cash_usd: float = 10.0
    volume_usd: float = 0.0
    fees_usd: float = 0.0
    max_drawdown_usd: float = 0.0
    cycles: int = 0
    settled_incomplete_cycles: int = 0
    decisions: int = 0
    invalid_decisions: int = 0
    rejected_buys: int = 0
    flat: bool = True
    elapsed_seconds: float = 0.0
    status: str = "NO_DATA"


class Replay:
    def __init__(self, decide: Callable[[dict], dict], *, adverse: float = 0.0,
                 latency: float = 0.250, target: float = 1000.0,
                 outcomes: dict[str, tuple[float, str]] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.decide, self.adverse, self.latency, self.target = decide, adverse, latency, target
        self.metrics = Metrics()
        self.position: dict | None = None
        self.pending: dict | None = None
        self.peak = 10.0
        self.start: float | None = None
        self.previous = -math.inf
        self.last_book_times: dict[tuple[str, str], float] = {}
        self.trace: list[dict] = []

    def books(self, event: dict) -> dict:
        now = float(event["timestamp"])
        result = event["books"]
        for side in ("YES", "NO"):
            book = result[side]
            ts = float(book["observedAt"])
            if not math.isfinite(ts) or ts > now:
                raise ValueError("Replay requires causal per-outcome observedAt")
            key = (str(event["market"]["id"]), side)
            if ts < self.last_book_times.get(key, -math.inf):
                raise ValueError("Book receive timestamps moved backwards")
            self.last_book_times[key] = ts
            parsed(book.get("asks", []))
            parsed(book.get("bids", []), True)
            minimum = float(book["minOrderSize"])
            if not math.isfinite(minimum) or minimum <= 0:
                raise ValueError("Invalid minimum order size")
        return result

    def execute(self, event: dict, books: dict) -> None:
        order = self.pending
        if order is None:
            return
        if order["market"] != event["market"]["id"]:
            self.pending = None
            return
        book = books[order["outcome"]]
        ts, now = float(book["observedAt"]), float(event["timestamp"])
        if ts < order["at"] + self.latency or ts <= order["book_at"] or now - ts > 2:
            return
        self.pending = None
        if order["action"] == "BUY":
            if self.position is not None or float(event["market"]["secondsToClose"]) <= 15:
                self.metrics.rejected_buys += 1
                return
            asks = [(p + self.adverse, q) for p, q in parsed(book.get("asks", [])) if p + self.adverse < 1]
            q, gross, fees = fill(asks, budget=min(order["cash"], self.metrics.cash_usd, 5))
            if q + 1e-9 < float(book["minOrderSize"]):
                self.metrics.rejected_buys += 1
                return
            self.metrics.cash_usd -= gross + fees
            self.position = {"market_id": order["market"], "outcome": order["outcome"], "shares": q,
                             "buy_gross_usd": gross, "buy_fees_usd": fees,
                             "sell_gross_usd": 0.0, "sell_fees_usd": 0.0}
        else:
            if self.position is None or self.position["market_id"] != order["market"]:
                return
            bids = [(p - self.adverse, q) for p, q in parsed(book.get("bids", []), True) if p - self.adverse > 0]
            q, gross, fees = fill(bids, quantity=self.position["shares"])
            if q <= 0:
                return
            self.position["shares"] -= q
            self.position["sell_gross_usd"] += gross
            self.position["sell_fees_usd"] += fees
            self.metrics.cash_usd += gross - fees
            if self.position["shares"] <= 1e-8:
                self.metrics.volume_usd += self.position["buy_gross_usd"] + self.position["sell_gross_usd"]
                self.metrics.cycles += 1
                self.position = None
        self.metrics.fees_usd += fees
        self.trace.append({"timestamp": now, "action": order["action"], "shares": q,
                           "gross": gross, "fees": fees, "cash": self.metrics.cash_usd})

    def mark(self, books: dict, market_id: str) -> None:
        equity = self.metrics.cash_usd
        if self.position is not None and self.position["market_id"] == market_id:
            _, gross, fees = fill(parsed(books[self.position["outcome"]].get("bids", []), True),
                                  quantity=self.position["shares"])
            equity += gross - fees
        self.peak = max(self.peak, equity)
        self.metrics.max_drawdown_usd = max(self.metrics.max_drawdown_usd, self.peak - equity)

    def settle(self, winner: str) -> None:
        if self.position["outcome"] == winner:
            self.metrics.cash_usd += self.position["shares"]
        self.position = None
        self.metrics.settled_incomplete_cycles += 1

    def step(self, event: dict) -> bool:
        now = float(event["timestamp"])
        if not math.isfinite(now) or now < self.previous:
            raise ValueError("Replay must be receive-time ordered")
        self.previous = now
        if self.start is None:
            self.start = now
        if now - self.start > 86400:
            return False
        self.metrics.elapsed_seconds = now - self.start
        seconds = float(event["market"]["secondsToClose"])
        if not math.isfinite(seconds):
            raise ValueError("Non-finite market clock")
        books = self.books(event)
        if self.position is not None and self.position["market_id"] != event["market"]["id"]:
            self.pending = None
            resolution = self.outcomes.get(self.position["market_id"])
            if resolution is None:
                self.metrics.status = "INCOMPLETE_MISSING_RESOLUTION"
                return False
            published_at, winner = resolution
            if now < published_at:
                return True
            self.settle(winner)
        if seconds <= 0:
            self.pending = None
            if self.position is not None and event.get("resolvedOutcome") in ("YES", "NO"):
                self.settle(event["resolvedOutcome"])
            self.mark(books, event["market"]["id"])
            return True
        self.execute(event, books)
        self.mark(books, event["market"]["id"])
        if self.metrics.volume_usd >= self.target and self.position is None and self.pending is None:
            self.metrics.qualified, self.metrics.status = True, "LOCAL_TARGET_CROSSING"
            return False
        if self.pending is not None or any(now - float(books[s]["observedAt"]) > 2 for s in ("YES", "NO")):
            return True
        obs = {"schemaVersion": 1, "timestamp": now, "market": copy.deepcopy(event["market"]),
               "reference": copy.deepcopy(event.get("reference")), "books": copy.deepcopy(books),
               "account": {"cashUsd": self.metrics.cash_usd, "eligibleVolumeUsd": self.metrics.volume_usd,
                           "completedCycles": self.metrics.cycles, "position": copy.deepcopy(self.position)},
               "rules": {"maximumBuyCashUsd": 5, "targetVolumeUsd": self.target, "evaluationWindowHours": 24}}
        self.metrics.decisions += 1
        try:
            response = self.decide(obs)
            if not isinstance(response, dict):
                raise ValueError("Response is not an object")
            action = response["action"]
            if action == "HOLD" and set(response) == {"action"}:
                return True
            if action == "SELL" and set(response) == {"action"} and self.position is not None:
                outcome, cash = self.position["outcome"], None
            elif action == "BUY" and set(response) == {"action", "outcome", "maxCashUsd"}:
                cash, outcome = response["maxCashUsd"], response["outcome"]
                if (isinstance(cash, bool) or not isinstance(cash, (float, int)) or not math.isfinite(cash)
                        or not 0 < cash <= min(5, self.metrics.cash_usd) + 1e-9 or self.position is not None
                        or seconds <= 15 or outcome not in ("YES", "NO")):
                    raise ValueError("Invalid BUY")
            else:
                raise ValueError("Invalid decision")
            self.pending = {"action": action, "outcome": outcome, "cash": cash, "at": now,
                            "market": event["market"]["id"], "book_at": float(books[outcome]["observedAt"])}
        except Exception:
            self.metrics.invalid_decisions += 1
        return True

    def run(self, events: Iterable[dict]) -> dict:
        for event in events:
            if not self.step(event):
                break
        self.metrics.flat = self.position is None and self.pending is None
        if self.metrics.status == "NO_DATA" and self.start is not None:
            self.metrics.status = "DATA_ENDED_FLAT" if self.metrics.flat else "DATA_ENDED_OPEN"
        return asdict(self.metrics)
