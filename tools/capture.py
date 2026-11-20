"""Read-only recorder of public YeNo metadata/reference and upstream L2 events.

No wallet, credentials, order submission, or synthetic market data. Raw events
carry local receive time and retain the source's original timestamps separately.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import gzip
import json
from pathlib import Path
import time
from urllib.request import urlopen

import websockets

API = "https://api.yeno.trade/api"
CLOB = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REFERENCE = "wss://api.yeno.trade"


class Journal:
    def __init__(self, directory: Path, limit: int, disk_limit: int):
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.file = gzip.open(directory / "raw.jsonl.gz", "wt", compresslevel=3)
        self.epoch, self.monotonic = time.time(), time.monotonic()
        self.counts: Counter = Counter()
        self.bytes = 0
        self.limit = limit
        self.disk_limit = disk_limit
        self.seq = 0
        self.market_slugs = set()

    def now(self):
        return self.epoch + time.monotonic() - self.monotonic

    def write(self, source, payload):
        self.seq += 1
        row = {"sequence": self.seq, "receivedAt": self.now(), "source": source, "payload": payload}
        line = json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
        self.bytes += len(line.encode())
        if self.bytes > self.limit:
            raise RuntimeError("Capture uncompressed byte limit reached")
        self.file.write(line)
        self.counts[source] += 1
        if source == "yeno_market":
            self.market_slugs.add(payload["slug"])
        if self.seq % 10000 == 0 and (self.directory / "raw.jsonl.gz").stat().st_size > self.disk_limit:
            raise RuntimeError("Capture compressed disk limit reached")


def get(path: str):
    with urlopen(API + path, timeout=10) as response:
        payload = json.load(response)
    if payload.get("success") is not True:
        raise ValueError("Public API did not return success")
    return payload["data"]


async def heartbeat(ws, message):
    while True:
        await asyncio.sleep(10)
        await ws.send(message)


async def stream(journal, source, url, subscription, end, asset_state=None):
    while journal.now() < end:
        try:
            async with websockets.connect(url, open_timeout=15, close_timeout=2, max_size=4_194_304) as ws:
                journal.write("connection", {"feed": source, "event": "connected"})
                if asset_state is not None:
                    subscription = [dict(subscription[0], assets_ids=asset_state["assets"])]
                for message in subscription:
                    await ws.send(json.dumps(message))
                subscribed = set(asset_state["assets"]) if asset_state is not None else set()
                ping = asyncio.create_task(heartbeat(ws, "PING" if source == "clob" else json.dumps({"type": "PING"})))
                try:
                    while journal.now() < end:
                        if asset_state is not None:
                            desired = set(asset_state["assets"])
                            for operation, assets in (("subscribe", desired - subscribed),
                                                       ("unsubscribe", subscribed - desired)):
                                if assets:
                                    await ws.send(json.dumps({"operation": operation, "assets_ids": sorted(assets),
                                                              "custom_feature_enabled": True}))
                            subscribed = desired
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=min(5, max(.1, end - journal.now())))
                        except asyncio.TimeoutError:
                            continue
                        if raw == "PONG":
                            continue
                        payload = json.loads(raw)
                        if source == "clob":
                            selected = [m for m in (payload if isinstance(payload, list) else [payload])
                                        if m.get("event_type") in ("book", "price_change", "tick_size_change", "market_resolved")]
                            if not selected:
                                continue
                            payload = selected
                        elif "data" not in payload:
                            continue
                        journal.write(source, payload)
                finally:
                    ping.cancel()
                    await asyncio.gather(ping, return_exceptions=True)
        except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
            journal.write("connection", {"feed": source, "event": "disconnected", "error": str(exc)[:300]})
            await asyncio.sleep(2)


async def metadata(journal, end, asset_state):
    last = {}
    gamma_seen = set()
    while journal.now() < end:
        current = int(journal.now()) // 300 * 300
        slots = [current, current + 300]
        current_assets = []
        for slot in slots:
            try:
                market = await asyncio.to_thread(get, f"/markets/btc-updown-5m-{slot}")
                journal.write("yeno_market", market)
                current_assets.extend(market[k] for k in ("polymarketYesToken", "polymarketNoToken"))
                if market["id"] not in gamma_seen:
                    def gamma():
                        url = "https://gamma-api.polymarket.com/markets?slug=" + market["slug"]
                        with urlopen(url, timeout=10) as response:
                            return json.load(response)
                    underlying = await asyncio.to_thread(gamma)
                    if underlying:
                        journal.write("gamma_market", underlying)
                        gamma_seen.add(market["id"])
                if market["id"] not in last or journal.now() - last[market["id"]] >= 30:
                    book = await asyncio.to_thread(get, "/trades/orderbook/" + market["id"])
                    journal.write("yeno_book_metadata", book)
                    last[market["id"]] = journal.now()
            except (OSError, ValueError, KeyError) as exc:
                journal.write("metadata_error", {"slot": slot, "error": str(exc)[:300]})
        # Only replace the subscriptions when both mappings were fetched. A
        # transient metadata failure must not silently unsubscribe a live market.
        if len(current_assets) == 4:
            asset_state["assets"] = current_assets
        journal.file.flush()
        await asyncio.sleep(10)


async def capture(args):
    journal = Journal(args.output, args.max_mb * 1024 * 1024, args.max_disk_mb * 1024 * 1024)
    current = int(journal.now()) // 300 * 300
    # Capture the active window for warm-up and the next full five-minute window.
    slots = [current, current + 300]
    end = current + (args.windows + 1) * 300 + 3 if args.seconds is None else journal.now() + args.seconds
    try:
        markets = []
        for slot in slots:
            market = await asyncio.to_thread(get, f"/markets/btc-updown-5m-{slot}")
            journal.write("yeno_market", market)
            markets.append(market)
        assets = [m[k] for m in markets for k in ("polymarketYesToken", "polymarketNoToken")]
        asset_state = {"assets": assets}
        print(json.dumps({"recording": str(args.output), "complete_market_slug": markets[1]["slug"],
                          "planned_seconds": round(end - journal.now()), "full_windows_requested": args.windows,
                          "real_data": True}), flush=True)
        tasks = [
            asyncio.create_task(stream(journal, "clob", CLOB,
                [{"type": "market", "assets_ids": assets, "initial_dump": True, "custom_feature_enabled": True}], end, asset_state)),
            asyncio.create_task(stream(journal, "reference", REFERENCE,
                [{"type": "SUBSCRIBE", "channel": c} for c in ("crypto:btcusdt", "crypto:btcusdt:twap60")], end)),
            asyncio.create_task(metadata(journal, end, asset_state))]
        while journal.now() < end:
            for task in tasks:
                if task.done():
                    task.result()
            await asyncio.sleep(min(1, max(.01, end - journal.now())))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        journal.file.close()
        manifest = {"evidence": "PUBLIC_FORWARD_CAPTURE", "official_evaluator_data": False,
                    "started_at": journal.epoch, "ended_at": journal.now(), "market_slots": slots,
                    "recorded_market_slugs": sorted(journal.market_slugs), "reached_planned_end": journal.now() >= end,
                    "full_windows_requested": args.windows,
                    "events": dict(journal.counts), "uncompressed_bytes": journal.bytes,
                    "sources": {"metadata": API, "books": CLOB, "reference": REFERENCE},
                    "notes": "Local receive timestamps. Raw source timestamps retained. No trades placed."}
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, help="Short connectivity check; omit to capture the next complete window")
    parser.add_argument("--windows", type=int, default=1, help="Consecutive complete five-minute windows to capture")
    parser.add_argument("--max-mb", type=int, default=1024, help="Uncompressed safety limit")
    parser.add_argument("--max-disk-mb", type=int, default=2048, help="Compressed disk safety limit")
    args = parser.parse_args()
    if args.seconds is not None and args.seconds <= 0:
        parser.error("seconds must be positive")
    if min(args.windows, args.max_mb, args.max_disk_mb) <= 0:
        parser.error("windows and byte limits must be positive")
    asyncio.run(capture(args))


if __name__ == "__main__":
    main()
