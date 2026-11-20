import copy
import math
import random
import time
import unittest

from yeno.execution import affordable, levels, quote
from yeno.model import History
from yeno.strategy import Bot, Config
from .helpers import observation, position, warmed


class StrategyTests(unittest.TestCase):
    def test_enters_profitable_liquid_setup_with_full_cash_cap(self):
        result = warmed().decide(observation())
        self.assertEqual(result["action"], "BUY")
        self.assertEqual(result["outcome"], "YES")
        self.assertGreater(result["maxCashUsd"], 4.99)
        self.assertLessEqual(result["maxCashUsd"], 5)

    def test_optional_warmup_learns_mid_but_cannot_enter_on_provisional_target(self):
        obs = observation()
        obs["reference"]["targetProvisional"] = True
        obs["reference"]["openingTargetUsd"] = None
        candidate = Bot(Config(learn_without_target=True))
        incumbent = Bot()
        self.assertEqual(candidate.decide(obs), {"action": "HOLD"})
        incumbent.decide(obs)
        self.assertEqual(len(candidate.history.points), 1)
        self.assertEqual(len(incumbent.history.points), 0)
        obs["timestamp"] -= 10
        candidate.history.observe(obs, without_target=True)
        self.assertEqual(len(candidate.history.points), 1)

    def test_no_reference_history_means_no_entry(self):
        self.assertEqual(Bot().decide(observation()), {"action": "HOLD"})

    def test_fee_negative_99_cent_exit_is_rejected(self):
        # 96.9 cents -> 99 cents has negative net P&L despite a higher exit bid.
        self.assertLess(quote([(0.99, 5)], 5).net, quote([(0.969, 5)], 5).cost)
        self.assertEqual(warmed().decide(observation(ask=0.969, bid=0.96))["action"], "HOLD")

    def test_bad_reference_fields_never_enter(self):
        for key, value in (("observedAt", 1062), ("observedAt", 1000), ("targetObservedAt", 1062),
                           ("targetProvisional", True), ("targetProvisional", "false"),
                           ("btcMidUsd", float("nan")), ("openingTargetUsd", 0)):
            with self.subTest(key=key, value=value):
                obs = observation()
                obs["reference"][key] = value
                self.assertEqual(warmed().decide(obs)["action"], "HOLD")

    def test_no_funding_or_rule_cap_override(self):
        for cap, cash in ((5, 0), (1, 10), (100, 10), (5, 4.5)):
            obs = observation()
            obs["rules"]["maximumBuyCashUsd"], obs["account"]["cashUsd"] = cap, cash
            result = warmed().decide(obs)
            if result["action"] == "BUY":
                self.assertLessEqual(result["maxCashUsd"], min(5, cap, cash))

    def test_depth_is_a_condition_for_entry(self):
        obs = observation()
        obs["books"]["YES"]["bids"] = [[0.93, 0.1]]
        self.assertEqual(warmed().decide(obs)["action"], "HOLD")

    def test_entry_allows_depth_that_requires_split_exits(self):
        obs = observation()
        obs["books"]["YES"]["bids"] = [[0.93, 2]]
        self.assertEqual(warmed().decide(obs)["action"], "BUY")

    def test_partial_take_profit_starts_before_deadline(self):
        obs = observation(bid=0.99, seconds=60, position=position())
        obs["books"]["YES"]["bids"] = [[0.99, 2]]
        bot = Bot()
        self.assertEqual(bot.decide(obs)["action"], "SELL")
        self.assertEqual(bot.last_reason, "partial_take_profit")

    def test_shallow_exit_gets_more_time(self):
        bot = Bot()
        for n in range(5):
            obs = observation(1061+n, seconds=44-n, position=position())
            obs["books"]["YES"]["bids"] = [[0.93, 0.25]]
            result = bot.decide(obs)
        self.assertEqual(result["action"], "SELL")
        self.assertEqual(bot.last_reason, "depth_adjusted_deadline")

    def test_transient_thin_quote_does_not_trigger_early_exit(self):
        bot = Bot()
        obs = observation(seconds=40, position=position())
        obs["books"]["YES"]["bids"] = [[0.93, 0.25]]
        self.assertEqual(bot.decide(obs)["action"], "HOLD")
        self.assertEqual(bot.decide(observation(1062, seconds=39, position=position()))["action"], "HOLD")

    def test_no_reentry_after_depth_deadline_exit(self):
        bot = warmed()
        for n in range(5):
            obs = observation(1061+n, seconds=44-n, position=position())
            obs["books"]["YES"]["bids"] = [[0.93, 0.25]]
            bot.decide(obs)
        self.assertEqual(bot.decide(observation(1067, seconds=38))["action"], "HOLD")
        self.assertEqual(bot.last_reason, "late_exit_market_block")

    def test_stale_book_pending_and_final_minute(self):
        cases = []
        obs = observation(); obs["books"]["YES"]["observedAt"] = 1000; cases.append(obs)
        obs = observation(); obs["account"]["pendingAction"] = {"action": "BUY"}; cases.append(obs)
        obs = observation(seconds=15); cases.append(obs)
        obs = observation(); obs["evaluationSecondsRemaining"] = 30; cases.append(obs)
        for obs in cases:
            self.assertEqual(warmed().decide(obs)["action"], "HOLD")

    def test_deadline_exit_works_without_asks_reference_or_history(self):
        obs = observation(seconds=20, position=position())
        obs["reference"] = None
        obs["books"]["YES"]["asks"] = []
        self.assertEqual(Bot().decide(obs), {"action": "SELL"})

    def test_exit_continues_after_partial_fill_and_price_change(self):
        bot = Bot()
        obs = observation(bid=0.99, position=position())
        self.assertEqual(bot.decide(obs), {"action": "SELL"})
        # Remaining amount is below minimum BUY size. A prior SELL is not a new cycle.
        obs = observation(1062, bid=0.94, position=position(shares=2, sell_gross_usd=2.4, sell_fees_usd=0.03))
        self.assertEqual(bot.decide(obs), {"action": "SELL"})
        self.assertEqual(bot.last_reason, "finish_partial_exit")

    def test_partial_exit_accounting_uses_whole_cycle_cost(self):
        obs = observation(bid=0.99, position=position(shares=2, sell_gross_usd=2.95, sell_fees_usd=0.03))
        self.assertEqual(Bot().decide(obs)["action"], "SELL")

    def test_bad_entry_fill_does_not_disable_99_cent_exit(self):
        obs = observation(bid=0.99, position=position(shares=5.09302, buy_gross_usd=4.94023, buy_fees_usd=0.05977))
        self.assertEqual(Bot().decide(obs)["action"], "SELL")

    def test_tiny_best_bid_does_not_hide_bad_exit_vwap(self):
        obs = observation(bid=0.99, position=position())
        obs["books"]["YES"]["bids"] = [[0.99, 0.1], [0.8, 10]]
        self.assertEqual(Bot().decide(obs)["action"], "HOLD")

    def test_flat_target_stops_but_open_target_flattens(self):
        for pos, action in ((None, "HOLD"), (position(), "SELL")):
            obs = observation(position=pos)
            obs["account"]["eligibleVolumeUsd"] = 1000
            self.assertEqual(Bot().decide(obs)["action"], action)

    def test_old_market_exits_and_rewind_does_not_inject_history(self):
        bot = warmed()
        obs = observation(position=position(market_id="old"))
        self.assertEqual(bot.decide(obs)["action"], "SELL")
        history = copy.deepcopy(bot.history.points)
        self.assertEqual(bot.decide(observation(999))["action"], "HOLD")
        self.assertEqual(history, bot.history.points)

    def test_sparse_volatility_preserves_observed_move_size(self):
        h = History()
        for n in range(61):
            obs = observation(1000 + 4 * n)
            obs["reference"]["btcMidUsd"] = 80000 + (4 if n % 2 else 0)
            h.observe(obs)
        self.assertAlmostEqual(h.sigma(1240), 4.0)

    def test_new_market_waits_one_minute_even_with_warm_history(self):
        bot = warmed()
        self.assertEqual(bot.decide(observation(seconds=240.01)), {"action": "HOLD"})
        self.assertEqual(bot.decide(observation(1062, seconds=240))["action"], "BUY")
        self.assertEqual(bot.decide(observation(1063, seconds=299, bid=.99, position=position())),
                         {"action": "SELL"})

    def test_twap_requires_coverage_and_time_weights_intervals(self):
        h = History()
        h.points = {0: (0, 100), 2: (2, 104), 3: (3, 106)}
        self.assertEqual(h.integral(0, 3), 304)
        self.assertIsNone(h.integral(0, 8))

    def test_book_math_handles_invalid_and_multilevel_inputs(self):
        book = levels([[0.5, 1], [0.51, 10], [float("nan"), 5], [0.4, float("inf")], None], buy=True)
        result = affordable(book, 2.0)
        self.assertLessEqual(result.cost, 2.0)
        self.assertGreater(result.shares, 3.5)
        self.assertLess(result.shares, 4)

    def test_malformed_json_values_never_raise(self):
        values = [None, [], "bad", 1, True, {}, float("nan"), float("inf")]
        for value in values:
            self.assertEqual(Bot().decide(value)["action"], "HOLD")
        for field in ("market", "account", "rules", "books", "reference"):
            for value in values:
                obs = observation(); obs[field] = value
                self.assertIn(Bot().decide(obs)["action"], ("HOLD", "SELL"))

    def test_randomized_outputs_obey_contract_and_preserve_input(self):
        rng = random.Random(49)
        bot = warmed()
        for n in range(1000):
            ask = rng.uniform(0.01, 0.99)
            obs = observation(1061 + n, ask=ask, bid=max(0.005, ask - 0.01), seconds=rng.uniform(0, 300))
            obs["account"]["cashUsd"] = rng.uniform(0, 15)
            obs["reference"]["btcMidUsd"] += rng.uniform(-300, 300)
            before = copy.deepcopy(obs)
            result = bot.decide(obs)
            self.assertEqual(obs, before)
            self.assertIn(result["action"], ("HOLD", "BUY", "SELL"))
            if result["action"] == "BUY":
                self.assertEqual(set(result), {"action", "outcome", "maxCashUsd"})
                self.assertTrue(math.isfinite(result["maxCashUsd"]))
                self.assertLessEqual(result["maxCashUsd"], min(5, obs["account"]["cashUsd"]))
                self.assertGreater(obs["market"]["secondsToClose"], 15)


if __name__ == "__main__":
    unittest.main()
