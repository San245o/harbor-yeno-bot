import copy

from yeno.strategy import Bot


def observation(now: float = 1061.0, *, ask: float = 0.94, bid: float = 0.93,
                seconds: float = 100.0, position: dict | None = None) -> dict:
    return {"timestamp": now, "market": {"id": "test", "secondsToClose": seconds},
            "reference": {"btcMidUsd": 80100, "openingTargetUsd": 80000, "observedAt": now,
                          "targetObservedAt": 1000, "targetProvisional": False},
            "account": {"cashUsd": 10, "eligibleVolumeUsd": 0, "position": copy.deepcopy(position)},
            "rules": {"maximumBuyCashUsd": 5, "targetVolumeUsd": 1000, "evaluationWindowHours": 24},
            "books": {s: {"asks": [[ask, 100]], "bids": [[bid, 100]], "minOrderSize": 5,
                          "observedAt": now} for s in ("YES", "NO")}}


def warmed() -> Bot:
    bot = Bot()
    for second in range(61):
        obs = observation(1000 + second, ask=0.50, bid=0.49, seconds=300 - second)
        obs["reference"]["btcMidUsd"] += second % 2
        bot.decide(obs)
    return bot


def position(**updates) -> dict:
    result = {"market_id": "test", "outcome": "YES", "shares": 5,
              "buy_gross_usd": 4.7, "buy_fees_usd": 0.06674,
              "sell_gross_usd": 0, "sell_fees_usd": 0}
    result.update(updates)
    return result
