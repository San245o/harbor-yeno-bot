"""Convert recorded public events causally; never backfill a target or book.

Replay consumes this generator directly to avoid expanding L2 deltas into a
multi-gigabyte snapshot file. Both raw receive and source times remain auditable.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import gzip
import heapq
import json
from pathlib import Path


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def journal_rows(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as source:
        for line in source:
            yield json.loads(line)


class RecordedEvents:
    def __init__(self, directory: Path, minimum_interval: float = 0.0):
        self.minimum_interval = minimum_interval
        self.directory = directory
        self.manifest = json.loads((directory / "manifest.json").read_text())
        self.stats: Counter = Counter()
        self.markets = {}
        self.assets = {}
        self.books = {}
        self.minimums = {}
        self.reference = None
        self.opening_twaps = {}
        self.covered_markets = set()

    def __iter__(self):
        next_snapshot = 0.0
        streams = [journal_rows(self.directory / "raw.jsonl.gz")]
        supplement = self.directory / "supplemental.jsonl"
        if supplement.exists():
            streams.append(journal_rows(supplement))
        for row in heapq.merge(*streams, key=lambda r: r["receivedAt"]):
            self.stats["raw_rows"] += 1
            now, source, payload = row["receivedAt"], row["source"], row["payload"]
            if source == "connection":
                if payload["feed"] == "clob":
                    self.books.clear()
                elif payload["feed"] == "reference":
                    self.reference = None
                self.stats["connection_events"] += 1
            elif source == "gamma_market":
                for market in payload:
                    minimum = float(market["orderMinSize"])
                    for asset in json.loads(market["clobTokenIds"]):
                        self.minimums[asset] = minimum
            elif source == "yeno_market":
                start, end = timestamp(payload["startDate"]), timestamp(payload["endDate"])
                mid = payload["id"]
                existing = self.markets.get(mid)
                target = float(payload["cryptoOpenPrice"]) if payload.get("cryptoOpenPrice") else None
                target_at = now
                if existing and existing["target"] == target:
                    target_at = existing["target_at"]
                elif existing and existing["target"] is not None and target != existing["target"]:
                    self.stats["target_revisions"] += 1
                # A pre-open placeholder is never treated as a final opening target.
                if now < start:
                    target = None
                market = {"id": mid, "start": start, "end": end, "target": target, "target_at": target_at,
                          "slug": payload["slug"], "assets": {"YES": payload["polymarketYesToken"],
                                                               "NO": payload["polymarketNoToken"]}}
                self.markets[mid] = market
                for side, asset in market["assets"].items():
                    self.assets[asset] = (mid, side)
            elif source == "reference" and payload.get("channel") == "crypto:btcusdt:twap60":
                data = payload["data"]
                source_at = float(data["ts"]) / 1000
                # Only exact opening timestamps can validate an opening target.
                # Receipt happens now, never retroactively at the source time.
                if source_at <= now and (source_at % 300 == 0 or
                        any(m["start"] == source_at for m in self.markets.values())):
                    self.opening_twaps[source_at] = (float(data["price"]), now)
            elif source == "reference" and payload.get("channel") == "crypto:btcusdt":
                data = payload["data"]
                self.reference = {"price": float(data["price"]), "received": now, "source": float(data["ts"]) / 1000}
            elif source == "clob":
                for message in payload:
                    kind = message["event_type"]
                    if kind == "book":
                        asset = message["asset_id"]
                        ts = float(message["timestamp"]) / 1000
                        if asset in self.books and ts < self.books[asset]["source"]:
                            self.stats["out_of_order_book_events"] += 1
                            continue
                        self.books[asset] = {"source": ts, "received": now,
                            "bids": {float(x["price"]): float(x["size"]) for x in message["bids"] if float(x["size"]) > 0},
                            "asks": {float(x["price"]): float(x["size"]) for x in message["asks"] if float(x["size"]) > 0}}
                    elif kind == "price_change":
                        ts = float(message["timestamp"]) / 1000
                        for change in message["price_changes"]:
                            asset = change["asset_id"]
                            book = self.books.get(asset)
                            if not book:
                                self.stats["deltas_before_snapshot"] += 1
                                continue
                            if ts < book["source"]:
                                self.stats["out_of_order_book_events"] += 1
                                continue
                            side = "bids" if change["side"] == "BUY" else "asks"
                            price, size = float(change["price"]), float(change["size"])
                            if size > 0:
                                book[side][price] = size
                            else:
                                book[side].pop(price, None)
                            # Only this asset gets a new receive timestamp.
                            book["received"], book["source"] = now, ts
            else:
                continue
            # At most one currently open five-minute session; no adjacent-market
            # prefetch or partial first/last market is included in a score.
            if now < next_snapshot:
                continue
            current = [m for m in self.markets.values() if m["start"] <= now < m["end"]
                       and self.manifest["started_at"] <= m["start"]
                       and self.manifest["ended_at"] >= m["end"]]
            if len(current) != 1:
                continue
            market = current[0]
            if self.reference is None:
                self.stats["missing_reference_or_target"] += 1
                continue
            if any(a not in self.books or a not in self.minimums for a in market["assets"].values()):
                self.stats["missing_snapshot_or_minimum"] += 1
                continue
            books = {}
            for side, asset in market["assets"].items():
                book = self.books[asset]
                books[side] = {"observedAt": book["received"], "sourceTimestamp": book["source"],
                    "minOrderSize": self.minimums[asset],
                    "bids": [[p, q] for p, q in sorted(book["bids"].items(), reverse=True) if 0 < p < 1],
                    "asks": [[p, q] for p, q in sorted(book["asks"].items()) if 0 < p < 1]}
            opening = self.opening_twaps.get(market["start"])
            validated = (market["target"] is not None and opening is not None
                         and abs(market["target"] - opening[0]) <= 0.00001)
            if not validated:
                self.stats["provisional_target_observations"] += 1
            self.covered_markets.add(market["slug"])
            self.stats["observations"] += 1
            next_snapshot = now + self.minimum_interval
            yield {"timestamp": now, "market": {"id": market["id"], "secondsToClose": market["end"] - now},
                   "reference": {"btcMidUsd": self.reference["price"], "openingTargetUsd": market["target"],
                                 "observedAt": self.reference["received"], "sourceObservedAt": self.reference["source"],
                                 "targetObservedAt": max(market["target_at"], opening[1]) if validated else market["target_at"],
                                 "targetProvisional": not validated},
                   "books": books}
