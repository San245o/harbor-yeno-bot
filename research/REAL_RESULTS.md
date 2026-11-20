# Real public capture evidence

These are local replays of public feeds captured forward on 15 November 2026. They are not official
Builderr runs, a qualification, a projected 24-hour result, or evidence of a broad trading advantage.
All money and orders are simulated; no real trades were opened.

## Four-hour revision

Across the same 48 markets from a $10 start, original Harbor closed 14 cycles for $141.34 of
eligible volume and $1.52 cash. The comparison strategy closed 26 for $262.16 and $7.47.
Harbor lost a $5 ticket in market eight that the comparison skipped. Both later lost a $5 ticket
entered in the opening minute of market 25. Harbor then lacked the minimum order cash.

Using the full BTC move between observed one-second closes avoided the first loss. That change
alone produced $252.18 volume and $7.49 cash. Delaying entries until 60 seconds into the market
avoided the second loss on this recording. Revised Harbor closed 24 cycles for $242.10 volume
and $12.40 cash. All three finished flat. This change was selected using this recording.

On the same tape thinned to at least one second between snapshots, revised Harbor and the
comparison each produced $161.54 volume and ended with $11.75 and $6.75 respectively. Of five
additional non-overlapping markets recorded earlier the same day, the two bots traded only one,
both closing about $10.03 in volume for a roughly four-cent gain on that capture. These checks
give little information about 24-hour qualification. [Full local results](../results/harbor-revision.json)

## Initial full five-minute window

Market opening 13:50 UTC (`1794750600`). Recorder started before opening and ended after close.
The first adapter treated the published positive target as final. The raw tape exposed a later target
revision; replay now marks it provisional until a causally received exact-opening TWAP agrees.
Both implementations receive the same corrected observation stream. No target is backfilled.

| Fixed configuration | Final cash | Closed-cycle gross volume | Closed cycles | Flat |
|---|---:|---:|---:|---|
| Incumbent | $10.03661528 | $10.02789328 | 1 | Yes |
| Reference | $10.03661531 | $10.02789531 | 1 | Yes |
| Fresh BTC history during provisional target | $10.02475567 | $20.01021167 | 2 | Yes |
| Four-cent model edge | $10.00000000 | $0 | 0 | Yes |

The incumbent/reference difference is negligible sizing arithmetic. Earlier history increased activity
but reduced cash on this sample. A stricter edge abstained. None is evidence of a superior entrant.
The configuration defaults remain unchanged.

Reproduce with the independently obtained reference source kept outside the deliverable:

```bash
python3 -m tools.replay_capture data/forward-20261115 --variants \
  --reference-dir /absolute/path/to/reference --output results/real-capture-causal.json
```

`results/real-capture-causal.json` retains input hashes and diagnostics. The earlier
`results/real-capture.json` uses the superseded target policy and remains an audit record rather than
current evidence. The source tape is intentionally excluded from the source ZIP.

## Longer session protocol

`research/candidates-20261115.json` freezes the three candidates and selection rule before the first
new full window finished. Six consecutive five-minute windows were requested, 14:10–14:40 UTC.
The first three are development; the last three are held out. No broad parameter sweep is authorized
by this protocol. Short-run metrics are cash, closed volume, fees, drawdown and unfinished cycles.
Even six windows cannot validate 24-hour target completion or tournament competitiveness.

Saved development and holdout reports are now available in
[`session2-development.json`](../results/session2-development.json) and
[`session2-holdout.json`](../results/session2-holdout.json). Both reports mark
`complete_data_validated: false`; their saved source hash differs from the later
four-hour revision. They do not validate the current candidate on an independent
24-hour window. Raw recordings are not included, so the saved results cannot be
independently reproduced from this repository alone.

## Source hash provenance

Saved reports retain their original source hashes. The submission preparation changes
`replay.cli.source_hash()` to use forward-slash relative paths on every platform; this
fixes the earlier Windows/Linux hash discrepancy. That helper change also changes the
current source hash because `replay/cli.py` itself is hashed. The decision runtime in
`yeno/` and its strategy configuration are unchanged by this packaging correction.
