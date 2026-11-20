# Research conclusions

The challenge is constrained capital turnover. Start with $10 and generate $1,000 of gross executed
BUY-plus-SELL notional from fully closed cycles. The $5 fee-inclusive BUY ceiling means roughly a hundred
successful full-ticket cycles are needed. Among qualifying entries, cash at that first flat crossing wins;
raw volume beyond the target has no value. Drawdown and then elapsed time break exact cash ties.

## Economics

The 1% overlay alone costs roughly $10 per $1,000 gross volume, before protocol fees, spreads or adverse
execution. Blind churn therefore consumes the entire starting bankroll. Higher-priced favourites reduce
the protocol fee per dollar of volume because its factor is `0.07 × (1 − price)`.

But a high win probability is not enough. Before five-decimal rounding:

| Entry price | Entry cost per share, both fees | Net 99-cent sale per share | Net difference |
|---|---:|---:|---:|
| 0.950 | 0.962825 | 0.979407 | +0.016582 |
| 0.960 | 0.972288 | 0.979407 | +0.007119 |
| 0.969 | 0.980794 | 0.979407 | −0.001387 |

This explains the explicit L2/fee break-even gate. It rejects a trade even when its intended successful
exit would lose money. It does not predict whether that exit will be available.

## Core retained, targeted improvements

The implementation keeps high-price favourite entries, full tickets, a three-cent volatility/TWAP model
edge, 99-cent take profit and a 20-second deadline. Harbor waits through the opening minute and retains the full move between sampled BTC seconds when estimating entry risk. It uses
time-weighted observed TWAP integration, fee checks across the entire ask book, and persistent liquidation
intent across partial fills.

Fee profitability belongs in the entry check. Once filled, the entry cost is sunk: a bad delayed fill
must not disable an otherwise valid 99-cent exit. Inspect executable bid VWAP to avoid a tiny attractive
top quote hiding poor depth, but do not wait for an unavailable break-even price.

Requiring a full exit to fit in one visible snapshot is too restrictive: the contract permits split exits.
Our entry gate permits split exits but rejects effectively absent bids. A sustained depth shortage can
advance the deadline. That trigger uses several seconds of observations, preventing one transient thin
quote from causing an early loss followed by an immediate re-entry into the same market.

All accounting remains evaluator-owned. The bot never fabricates volume or prices and never uses future
targets. Its decision path uses no network, wall clock, random numbers or disk writes.

## What is and is not established

Regression tests establish contract behavior and specific arithmetic/causal-ordering properties. The
official smoke test establishes HTTP compatibility. Synthetic replays exposed implementation trade-offs
but are not evidence of a winning strategy. In particular, choppy regimes and two-cent adverse fills remain
weaknesses of the retained core. We do not claim broad outperformance.

Real public feeds have been reached and recorded. Their input provenance, outcome-specific timestamps,
minimum order sizes, target availability and missing-data handling are more important than a flattering
score from a custom simulator. A representative held-out 24-hour capture and official evaluator parity
are still missing. The submission should be described as an experimental candidate until those checks exist.

## Primary sources

- [Challenge and scoring](https://builderr.ai/challenges/yeno-volume-bots#scoring)
- [Evaluator contract](https://builderr.ai/downloads/yeno-evaluator-contract.md)
- [Starter kit](https://builderr.ai/downloads/yeno-starter-kit.zip)
- [Polymarket predictions changelog](https://docs.polymarket.com/changelog/predictions)

The contest's frozen fee and latency rules take precedence over later changes to live venue execution.
