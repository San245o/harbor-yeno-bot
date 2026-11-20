import unittest

from replay.engine import Replay, fill
from .helpers import observation


def event(now, *, yes_at=None, seconds=100, bid=0.6, depth=100):
    result = observation(now, ask=0.5, bid=bid, seconds=seconds)
    result["books"]["YES"]["observedAt"] = now if yes_at is None else yes_at
    result["books"]["YES"]["bids"] = [[bid, depth]]
    return result


def roundtrip(obs):
    return {"action": "SELL"} if obs["account"]["position"] else {"action": "BUY", "outcome": "YES", "maxCashUsd": 5}


class ReplayTests(unittest.TestCase):
    def test_other_outcome_update_cannot_execute_pending_buy(self):
        r = Replay(roundtrip)
        r.step(event(1000))
        r.step(event(1000.3, yes_at=1000))
        self.assertIsNone(r.position)
        self.assertIsNotNone(r.pending)
        r.step(event(1000.4))
        self.assertIsNotNone(r.position)
        self.assertEqual(r.trace[0]["timestamp"], 1000.4)

    def test_buy_needs_250ms_even_with_an_earlier_update(self):
        r = Replay(roundtrip)
        r.step(event(1000)); r.step(event(1000.2))
        self.assertIsNone(r.position)
        r.step(event(1000.25))
        self.assertIsNotNone(r.position)

    def test_partial_sells_only_credit_fully_closed_cycle(self):
        r = Replay(roundtrip, target=1)
        r.step(event(1000)); r.step(event(1000.3))
        bought = r.position["shares"]
        r.step(event(1000.6, depth=2))
        self.assertEqual(r.metrics.volume_usd, 0)
        self.assertAlmostEqual(r.position["shares"], bought - 2)
        r.step(event(1000.9))
        self.assertIsNone(r.position)
        self.assertGreater(r.metrics.volume_usd, 9)
        self.assertEqual(r.metrics.cycles, 1)
        self.assertTrue(r.metrics.qualified)
        self.assertIsNone(r.pending)

    def test_resolution_is_cash_but_never_volume(self):
        def buy_once(obs):
            return {"action": "HOLD"} if obs["account"]["position"] else {"action": "BUY", "outcome": "YES", "maxCashUsd": 5}
        r = Replay(buy_once)
        r.step(event(1000)); r.step(event(1000.3))
        last = event(1100, seconds=0); last["resolvedOutcome"] = "YES"
        r.step(last)
        self.assertIsNone(r.position)
        self.assertEqual(r.metrics.volume_usd, 0)
        self.assertEqual(r.metrics.settled_incomplete_cycles, 1)
        self.assertGreater(r.metrics.cash_usd, 10)

    def test_expired_position_waits_for_published_outcome(self):
        r = Replay(roundtrip, outcomes={"test": (1120.0, "YES")})
        r.step(event(1000))
        r.step(event(1000.3))
        locked_cash = r.metrics.cash_usd
        shares = r.position["shares"]
        for now in (1101, 1119.99, 1120):
            row = event(now)
            row["market"]["id"] = "next"
            self.assertTrue(r.step(row))
            if now < 1120:
                self.assertEqual(r.metrics.cash_usd, locked_cash)
                self.assertIsNone(r.pending)
        self.assertAlmostEqual(r.metrics.cash_usd, locked_cash + shares)
        self.assertIsNone(r.position)
        self.assertEqual(r.metrics.volume_usd, 0)
        self.assertEqual(r.metrics.settled_incomplete_cycles, 1)
        self.assertEqual(r.pending["market"], "next")

    def test_one_sided_exit_and_minimum_size_for_buy_only(self):
        r = Replay(roundtrip, target=1)
        r.step(event(1000)); r.step(event(1000.3))
        last = event(1000.6); last["books"]["YES"]["asks"] = []
        r.step(last)
        self.assertEqual(r.metrics.cycles, 1)

    def test_expiry_blocks_pending_entry(self):
        r = Replay(roundtrip)
        r.step(event(1000, seconds=15.1)); r.step(event(1000.3, seconds=14.8))
        self.assertIsNone(r.position)
        self.assertEqual(r.metrics.cash_usd, 10)
        self.assertEqual(r.metrics.rejected_buys, 1)

    def test_missing_book_time_is_rejected(self):
        row = event(1000); del row["books"]["YES"]["observedAt"]
        with self.assertRaises(KeyError):
            Replay(roundtrip).step(row)

    def test_fill_budget_includes_both_fee_layers(self):
        q, gross, fees = fill([(0.5, 20)], budget=5)
        self.assertLessEqual(gross + fees, 5)
        self.assertAlmostEqual(q, 5 / 0.5225, places=4)
        self.assertGreater(fees, 0.2)

    def test_invalid_responses_are_counted(self):
        for response in ({"action": "BUY", "outcome": "YES", "maxCashUsd": 5.01},
                         {"action": "SELL"}, {"action": "HOLD", "extra": 0},
                         {"action": "BUY", "outcome": "YES", "maxCashUsd": float("nan")}):
            r = Replay(lambda obs: response)
            r.step(event(1000))
            self.assertEqual(r.metrics.invalid_decisions, 1)


if __name__ == "__main__":
    unittest.main()
