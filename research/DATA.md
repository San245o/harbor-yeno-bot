# Verified market data sources

Verified with successful anonymous reads and live WebSocket messages on 15 November 2026.
These are public market sources, **not** Builderr's private capture or a claim to reproduce its exact pipeline.

| Data | Source | Observed behavior |
|---|---|---|
| YeNo market mapping and opening target | `GET https://api.yeno.trade/api/markets/btc-updown-5m-{openingUnixSecond}` | Returns UUID, start/end, `cryptoOpenPrice`, condition ID and both outcome token IDs. |
| YeNo displayed L2 snapshot | `GET https://api.yeno.trade/api/trades/orderbook/{UUID}` | Returns YES/NO bids and asks. A combined timestamp does not prove which outcome last updated. |
| Underlying minimum BUY size | `GET https://gamma-api.polymarket.com/markets?slug={slug}` | `orderMinSize` was 5 on the captured markets; the YeNo wrapper reported 1. Use the stricter verified underlying value. |
| Exact outcome L2 events | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Subscribe to the market's two `assets_ids`; initial `book` snapshots plus `price_change` deltas identify each outcome separately. |
| BTC spot reference | `wss://api.yeno.trade`, channel `crypto:btcusdt` | `price` plus a source `ts`, approximately once per second in the connectivity check. |
| Settlement-reference TWAP | Same socket, channel `crypto:btcusdt:twap60` | Price, source timestamp and `windowSeconds: 60`. |
| BTC history for research | `GET https://api.yeno.trade/api/crypto/btc/history?since={UnixSecond}` | Timestamped historical prices. Add `&source=twap60` for the 60-second series. |

Public protocol documentation: [Polymarket market WebSocket](https://docs.polymarket.com/api-reference/wss/market),
[settlement changelog](https://docs.polymarket.com/changelog/predictions).
YeNo's actual market descriptions identify the [BTC/USD 60-second Chainlink TWAP](https://data.chain.link/streams/btc-usd-twap-60s-streams).

The direct CLOB HTTP `/time` request returned HTTP 403/error 1010 from this environment, while the public
market WebSocket connected and delivered snapshots and deltas. The recorder uses that working public feed.
No authentication or trade API is used.

## Why this is different from a candle backtest

An order must execute on a later update of **its own outcome**, at least 250 ms after the decision.
A NO update cannot refresh a stale YES quote. Record local receive time, preserve original source time,
and apply deltas only after an initial snapshot. On reconnect, invalidate reconstructed books until fresh
snapshots arrive. Do not rewrite earlier rows using opening targets discovered later.

`tools.capture` stores compressed raw messages and metadata. `replay.capture` processes them in receive
order, retains independent timestamps for YES and NO, and rejects backwards source-time deltas. It does
not fill from trades, last prices, candle highs/lows or midpoint quotes.

## Material limitations

- Builderr does not publish its canonical evaluator, capture paths or exact source-selection pipeline.
  The [downloadable contract](https://builderr.ai/downloads/yeno-evaluator-contract.md) explicitly says this.
- `cryptoOpenPrice` has no public provisional flag in the observed response. The first capture showed a post-open revision. Our adapter now
  marks the target provisional until it agrees (within $0.00001) with an exact-opening-timestamp TWAP
  already received. It records the later validation/publication time without backfilling. Missing opening
  samples therefore block entry for that market. This conservative adapter policy still requires organizer
  parity confirmation; it does not prove identical evaluator inputs.
- Public spot/Chainlink input may differ from the exact `btcMidUsd` reference Builderr uses. Preserve both
  spot and TWAP feeds so this can be checked instead of guessed.
- A window contained between recorder start/end does not prove lossless coverage. Connection events,
  missing snapshots, missing metadata and stale references must be inspected. No capture is labelled an
  official validated dataset.
- Raw historical prices fetched today cannot be treated as if received before today's fetch. They are
  useful for model research, not reconstruction of an earlier receive-time order-book tape.
- One five-minute sample can verify ingestion. It cannot establish a 24-hour strategy advantage.

To judge competitiveness, collect contiguous sessions on multiple days, freeze the strategy before
evaluating later days, compare identical inputs, and include adverse latency/slippage and missing-feed
cases. Only Builderr's unseen 24-hour run can produce an official qualification result.
