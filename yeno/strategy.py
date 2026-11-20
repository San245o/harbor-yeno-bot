"""Harbor: fee-aware favourite trading with persistent liquidation intent.

No network, wall clock, randomness, local cash ledger, or disk I/O. One Bot per
evaluation run. Account fields in every observation are authoritative.
"""
from __future__ import annotations

import math
import statistics
import threading
from dataclasses import dataclass
from typing import Any

from .execution import affordable, floor_cash, levels, number, quote, shifted
from .model import History, valid_reference


@dataclass(frozen=True)
class Config:
    learn_without_target: bool = False
    min_ask: float = 0.94
    max_ask: float = 0.969
    min_entry_seconds: float = 22.0
    # Observe the first minute of each five-minute market before risking a full ticket.
    max_entry_seconds: float = 240.0
    exit_seconds: float = 20.0
    minimum_model_edge: float = 0.03
    minimum_edge_per_share: float = 0.001
    take_profit_bid: float = 0.99
    exit_slippage_allowance: float = 0.005
    execution_reserve: float = 0.0
    sizing_stress: float = 0.02
    exit_depth_coverage: float = 0.1
    reentry_seconds: float = 0.5
    volatility_multiplier: float = 1.0
    # Underlying five-minute markets use 60-second TWAP (2026-08-14
    # predictions changelog). Keep the alternative model available for ablations.
    settlement: str = "twap60"


def pending(account: dict) -> bool:
    return bool(account.get("pendingAction") or account.get("pendingActions") or account.get("pendingBuy"))


def book_fresh(book: dict, now: float) -> bool:
    # The official minimal schema has no book timestamp: the evaluator promises
    # freshness there. If supplied, observedAt/receivedAt must be causal and fresh.
    for key in ("observedAt", "receivedAt"):
        if key in book:
            ts = number(book[key])
            if ts is None or not 0 <= now - ts <= 2:
                return False
    return True


class Bot:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.history = History()
        self.lock = threading.Lock()
        self.last_timestamp = -math.inf
        self.started_at: float | None = None
        self.was_open = False
        self.liquidating = False
        self.next_entry_at = -math.inf
        self.depth_samples: dict[int, tuple[float, float]] = {}
        self.blocked_market: str | None = None
        self.last_reason = "starting"
        self.errors = 0

    def result(self, action: str, reason: str, **values: Any) -> dict:
        self.last_reason = reason
        return {"action": action, **values}

    def sell(self, reason: str) -> dict:
        self.liquidating = True
        return self.result("SELL", reason)

    def decide(self, obs: Any) -> dict:
        with self.lock:
            try:
                return self._decide(obs)
            except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
                self.errors += 1
                return self.result("HOLD", "malformed_observation")

    def _decide(self, obs: Any) -> dict:
        if not isinstance(obs, dict):
            return self.result("HOLD", "malformed_observation")
        account, market, books, rules = (obs.get(k) for k in ("account", "market", "books", "rules"))
        if not all(isinstance(x, dict) for x in (account, market, books, rules)):
            return self.result("HOLD", "missing_contract_fields")
        now, seconds = number(obs.get("timestamp")), number(market.get("secondsToClose"))
        if now is None or now < self.last_timestamp:
            return self.result("HOLD", "noncausal_timestamp")
        self.last_timestamp = now
        if self.started_at is None:
            self.started_at = now
        if pending(account):
            return self.result("HOLD", "pending_action")
        position = account.get("position")
        market_id = market.get("id")
        if not isinstance(market_id, str) or not market_id:
            return self.result("HOLD", "missing_market_id")
        if position is None and self.was_open:
            self.next_entry_at = max(self.next_entry_at, now + self.config.reentry_seconds)
        if position is None:
            self.was_open = self.liquidating = False
            self.depth_samples.clear()
        elif not self.was_open:
            self.was_open = True
        self.history.observe(obs, without_target=self.config.learn_without_target)

        hours = number(rules.get("evaluationWindowHours"))
        hours = min(hours, 24.0) if hours is not None and hours > 0 else 24.0
        remaining_run = self.started_at + hours * 3600 - now
        if "evaluationSecondsRemaining" in obs:
            explicit = number(obs["evaluationSecondsRemaining"])
            if explicit is not None:
                remaining_run = min(remaining_run, explicit)
        volume = number(account.get("eligibleVolumeUsd"))
        target = number(rules.get("targetVolumeUsd"))
        target = target if target is not None and target > 0 else 1000.0

        # Exit handling precedes all entry filters, reference checks and volume
        # stopping. SELL remains legal even if asks disappear or history is empty.
        if position is not None:
            if not isinstance(position, dict):
                return self.result("HOLD", "malformed_position")
            if self.liquidating:
                return self.sell("finish_partial_exit")
            if position.get("market_id") != market_id:
                return self.sell("old_market_position")
            if seconds is None or min(seconds, remaining_run) <= self.config.exit_seconds:
                return self.sell("deadline_exit")
            if volume is not None and volume >= target:
                return self.sell("flatten_at_target")
            return self.manage_position(obs, position)

        if volume is None or volume >= target:
            return self.result("HOLD", "target_reached_or_missing_volume")
        if seconds is None or not max(16.0, self.config.min_entry_seconds) < seconds <= self.config.max_entry_seconds:
            return self.result("HOLD", "entry_time")
        if remaining_run <= 40.0:
            return self.result("HOLD", "run_deadline")
        if market_id == self.blocked_market:
            return self.result("HOLD", "late_exit_market_block")
        if now < self.next_entry_at:
            return self.result("HOLD", "retry_cooldown")
        if not valid_reference(obs):
            return self.result("HOLD", "invalid_reference")
        forecast = self.history.forecast(obs, volatility_multiplier=self.config.volatility_multiplier,
                                         settlement=self.config.settlement)
        if forecast is None:
            return self.result("HOLD", "insufficient_causal_history")
        cash, rule_cap = number(account.get("cashUsd")), number(rules.get("maximumBuyCashUsd"))
        if cash is None or rule_cap is None or min(cash, rule_cap) <= 0:
            return self.result("HOLD", "invalid_cash_limit")
        cap = floor_cash(min(5.0, rule_cap, cash - 0.00002))
        ref = obs["reference"]
        favoured = "YES" if float(ref["btcMidUsd"]) >= float(ref["openingTargetUsd"]) else "NO"
        candidates: list[tuple[float, str, float]] = []
        for outcome, probability in (("YES", forecast.yes), ("NO", 1 - forecast.yes)):
            if outcome != favoured:
                continue
            book = books.get(outcome)
            if not isinstance(book, dict) or not book_fresh(book, now):
                continue
            asks, bids = levels(book.get("asks"), buy=True), levels(book.get("bids"), buy=False)
            minimum = number(book.get("minOrderSize"))
            if not asks or not bids or minimum is None or minimum <= 0:
                continue
            if bids[0][0] >= asks[0][0]:
                continue
            if not self.config.min_ask <= asks[0][0] <= self.config.max_ask:
                continue
            if probability - asks[0][0] < self.config.minimum_model_edge:
                continue
            buy = affordable(asks, cap)
            # Two-cent adverse sizing is separate from the optional execution
            # reserve in the break-even check; neither guarantees future fills.
            stress = affordable(shifted(asks, self.config.sizing_stress), cap)
            if stress.shares < minimum + 1e-6 or buy.shares < minimum:
                continue
            depth = sum(q for p, q in bids if p >= bids[0][0] - 0.02 - 1e-9)
            if depth < self.config.exit_depth_coverage * buy.shares:
                continue
            # Partial exits are allowed. Do not demand the entire position fit
            # in a single snapshot; require some usable bids and plan the exit.
            entry = quote(shifted(asks, self.config.execution_reserve), buy.shares)
            if entry.shares + 1e-8 < buy.shares:
                continue
            # A successful intended 99-cent exit must cover both fee layers.
            # This is a break-even check, not an expected-profit forecast.
            exit_price = self.config.take_profit_bid - self.config.execution_reserve
            proceeds = quote([(exit_price, buy.shares)], buy.shares)
            edge = (proceeds.net - entry.cost) / buy.shares
            if edge < self.config.minimum_edge_per_share:
                continue
            # Spend only the displayed affordable cost, avoiding oversizing when
            # the ask book is thin. Floor prevents crossing the hard cap.
            amount = floor_cash(min(cap, buy.cost))
            if affordable(shifted(asks, self.config.sizing_stress), amount).shares < minimum + 1e-6:
                continue
            candidates.append((edge, outcome, amount))
        if not candidates:
            return self.result("HOLD", "no_executable_edge")
        _, outcome, amount = max(candidates)
        self.next_entry_at = now + self.config.reentry_seconds
        return self.result("BUY", "net_edge_and_exit_depth", outcome=outcome, maxCashUsd=amount)

    def manage_position(self, obs: dict, position: dict) -> dict:
        side, shares = position.get("outcome"), number(position.get("shares"))
        if side not in ("YES", "NO") or shares is None or shares <= 0:
            return self.result("HOLD", "malformed_position")
        book = obs["books"].get(side)
        if not isinstance(book, dict) or not book_fresh(book, float(obs["timestamp"])):
            return self.result("HOLD", "unavailable_exit_book")
        bids = levels(book.get("bids"), buy=False)
        if not bids:
            return self.result("HOLD", "no_exit_bids")
        available = sum(q for _, q in bids)
        now = float(obs["timestamp"])
        self.depth_samples[math.floor(now)] = (now, available)
        self.depth_samples = {s: row for s, row in self.depth_samples.items() if row[0] >= now - 10}
        samples = list(self.depth_samples.values())
        # A single thin update is not evidence of sustained liquidity loss.
        # Avoid exiting early and re-buying on the next fuller snapshot.
        sustained = len(samples) >= 3 and now - min(row[0] for row in samples) >= 4
        estimated_depth = statistics.median(row[1] for row in samples) if sustained else math.inf
        # With fresh books up to 2 s old and 250 ms latency, allow several
        # updates plus a 5 s buffer. This is a conservative scheduling heuristic,
        # not a promise that displayed liquidity will replenish.
        slices = math.ceil(shares / max(estimated_depth, 1e-9))
        exit_buffer = max(self.config.exit_seconds, min(60.0, 5.0 + 2.25 * slices))
        seconds = number(obs["market"].get("secondsToClose"))
        if seconds is not None and seconds <= exit_buffer:
            self.blocked_market = str(obs["market"]["id"])
            return self.sell("depth_adjusted_deadline")
        liquidation = quote(bids, shares)
        # Entry cost is sunk. A worse-than-request-time fill must not disable
        # the 99-cent exit and strand capital until the deadline. Inspect VWAP
        # so a tiny 99-cent quote cannot hide much worse depth underneath it.
        if bids[0][0] >= self.config.take_profit_bid and liquidation.shares > 0:
            average_bid = liquidation.gross / liquidation.shares
            if average_bid >= self.config.take_profit_bid - self.config.exit_slippage_allowance:
                return self.sell("partial_take_profit" if liquidation.shares + 1e-8 < shares else "take_profit_bid")
        return self.result("HOLD", "hold_position")
