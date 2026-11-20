"""Run official HTTP smoke check and measure representative decision latency."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

from yeno.server import make_server
from yeno.strategy import Bot


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    bot = Bot()
    sample = json.loads((root / "research/yeno-sample-observation.json").read_text())
    server = make_server(port=0, bot=bot)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/decide"
    smoke = None
    durations, actions = [], {}
    try:
        completed = subprocess.run([sys.executable, str(root / "research/yeno-smoke-test.py"),
                                    "--url", url, "--observation", str(root / "research/yeno-sample-observation.json")],
                                   check=True, capture_output=True, text=True)
        smoke = json.loads(completed.stdout)
        base = sample["timestamp"] + 1
        for n in range(600):
            now = base + n
            sample["timestamp"] = sample["reference"]["observedAt"] = now
            sample["reference"]["btcMidUsd"] = 80500 + n % 2
            sample["market"]["secondsToClose"] = 100
            for side in ("YES", "NO"):
                sample["books"][side] = {"observedAt": now, "minOrderSize": 5,
                    "asks": [[0.94 + k * 0.0001, 1] for k in range(100)],
                    "bids": [[0.93 - k * 0.0001, 1] for k in range(100)]}
            # Half the warmed requests exercise position management, including
            # one-sided exits. The other half exercise the complete BUY path.
            sample["account"]["position"] = None
            if n % 4 >= 2:
                sample["account"]["position"] = {"market_id": sample["market"]["id"], "outcome": "YES",
                    "shares": 5, "buy_gross_usd": 4.7, "buy_fees_usd": 0.06674,
                    "sell_gross_usd": 0, "sell_fees_usd": 0}
                sample["books"]["YES"]["bids"] = [[0.99, 100]]
                sample["books"]["YES"]["asks"] = []
            request = Request(url, json.dumps(sample).encode(), {"Content-Type": "application/json"})
            start = time.perf_counter()
            with urlopen(request, timeout=1) as response:
                decision = json.load(response)
            durations.append((time.perf_counter() - start) * 1000)
            action = decision["action"]; actions[action] = actions.get(action, 0) + 1
    finally:
        server.shutdown(); server.server_close(); thread.join()
    durations.sort()
    result = {"official_smoke": smoke, "requests": len(durations), "actions": actions,
              "http_latency_ms": {"p50": durations[len(durations) // 2],
                                  "p95": durations[int(len(durations) * .95)],
                                  "p99": durations[int(len(durations) * .99)], "max": max(durations)},
              "under_200ms_all_requests": max(durations) < 200, "strategy_errors": bot.errors}
    output = root / "results/validation.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if bot.errors or max(durations) >= 200:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
