import gzip
import json
from pathlib import Path
import tempfile
import unittest

from replay.capture import RecordedEvents


class CaptureTests(unittest.TestCase):
    def rows(self):
        market = {"id": "m", "slug": "btc-test", "startDate": "1970-01-01T00:16:40Z",
                  "endDate": "1970-01-01T00:21:40Z", "cryptoOpenPrice": "80000",
                  "polymarketYesToken": "yes", "polymarketNoToken": "no"}
        def record(at, source, payload):
            return {"receivedAt": at, "source": source, "payload": payload}
        books = [{"event_type": "book", "asset_id": side, "timestamp": "1001000",
                  "bids": [{"price": "0.49", "size": "10"}], "asks": [{"price": "0.5", "size": "10"}]}
                 for side in ("yes", "no")]
        return [record(990, "yeno_market", market),
                record(999, "gamma_market", [{"orderMinSize": 5, "clobTokenIds": '["yes", "no"]'}]),
                record(1001, "clob", books),
                record(1002, "reference", {"channel": "crypto:btcusdt", "data": {"price": 80100, "ts": 1002000}}),
                record(1003, "reference", {"channel": "crypto:btcusdt:twap60", "data": {"price": 80000, "ts": 1000000}}),
                record(1010, "yeno_market", market),
                record(1011, "clob", [{"event_type": "price_change", "timestamp": "1011000", "price_changes": [
                    {"asset_id": "no", "side": "BUY", "price": "0.49", "size": "0"}]}])]

    def load(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "manifest.json").write_text(json.dumps({"started_at": 980, "ended_at": 1301}))
            with gzip.open(path / "raw.jsonl.gz", "wt") as source:
                for row in rows:
                    source.write(json.dumps(row) + "\n")
            return list(RecordedEvents(path))

    def test_targets_are_not_backfilled_and_outcomes_have_independent_times(self):
        result = self.load(self.rows())
        self.assertTrue(result)
        self.assertTrue(result[0]["reference"]["targetProvisional"])
        self.assertIsNone(result[0]["reference"]["openingTargetUsd"])
        self.assertFalse(result[-1]["reference"]["targetProvisional"])
        last = result[-1]
        self.assertEqual(last["reference"]["targetObservedAt"], 1010)
        self.assertEqual(last["books"]["YES"]["observedAt"], 1001)
        self.assertEqual(last["books"]["NO"]["observedAt"], 1011)
        self.assertEqual(last["books"]["NO"]["bids"], [])
        self.assertEqual(last["books"]["YES"]["minOrderSize"], 5)

    def test_target_revision_is_causal_and_requires_opening_twap_match(self):
        rows = self.rows()
        rows[4]["payload"]["data"]["price"] = 80001
        rows.append({"receivedAt": 1012, "source": "yeno_market", "payload": {
            **rows[0]["payload"], "cryptoOpenPrice": "80001"}})
        events = self.load(rows)
        self.assertTrue(all(e["reference"]["targetProvisional"] for e in events[:-1]))
        self.assertFalse(events[-1]["reference"]["targetProvisional"])
        self.assertEqual(events[-1]["reference"]["targetObservedAt"], 1012)

    def test_reconnect_invalidates_books_until_new_snapshots(self):
        rows = self.rows()
        rows.append({"receivedAt": 1012, "source": "connection", "payload": {"feed": "clob", "event": "connected"}})
        rows.append({"receivedAt": 1013, "source": "clob", "payload": [{"event_type": "price_change", "timestamp": "1013000",
                     "price_changes": [{"asset_id": "yes", "side": "BUY", "price": ".49", "size": "2"}]}]})
        self.assertLess(self.load(rows)[-1]["timestamp"], 1012)


if __name__ == "__main__":
    unittest.main()
