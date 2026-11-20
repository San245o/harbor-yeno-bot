"""Inspect receive-time coverage without evaluating or selecting a trading strategy."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path


def audit(directory: Path, *, in_progress: bool = False) -> dict:
    counts, connections = Counter(), []
    previous = None
    sequence = 0
    bad_sequence = backwards = 0
    feed_last, feed_max_gap, feed_large_gaps = {}, {}, Counter()
    markets, gamma_assets, snapshots = {}, set(), set()
    first = last = None
    partial_stream = False
    try:
        with gzip.open(directory / 'raw.jsonl.gz', 'rt') as source:
            for line in source:
                row = json.loads(line)
                now, kind, payload = row['receivedAt'], row['source'], row['payload']
                first = now if first is None else first
                last = now
                backwards += previous is not None and now < previous
                bad_sequence += row['sequence'] != sequence + 1
                previous, sequence = now, row['sequence']
                counts[kind] += 1
                if kind == 'connection':
                    connections.append({'receivedAt': now, **payload})
                if kind == 'yeno_market':
                    markets[payload['slug']] = [payload['polymarketYesToken'], payload['polymarketNoToken']]
                if kind == 'gamma_market':
                    for market in payload:
                        gamma_assets.update(json.loads(market['clobTokenIds']))
                if kind == 'clob':
                    snapshots.update(m['asset_id'] for m in payload if m['event_type'] == 'book')
                feed = kind if kind == 'clob' else (payload.get('channel') if kind == 'reference' else None)
                if feed:
                    if feed in feed_last:
                        gap = now - feed_last[feed]
                        feed_max_gap[feed] = max(feed_max_gap.get(feed, 0), gap)
                        feed_large_gaps[feed] += gap > (2 if feed == 'clob' else 1.5)
                    feed_last[feed] = now
    except (EOFError, json.JSONDecodeError):
        if not in_progress:
            raise
        partial_stream = True
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    return {'evidence': 'CAPTURE_QUALITY_ONLY', 'in_progress': in_progress, 'partial_gzip_tail': partial_stream,
            'first_received_at': first, 'last_received_at': last, 'events': dict(counts),
            'nonmonotonic_receive_rows': backwards, 'nonconsecutive_sequence_rows': bad_sequence,
            'max_receive_gap_seconds': feed_max_gap, 'receive_gaps_above_freshness_threshold': dict(feed_large_gaps),
            'connection_events': connections,
            'markets': {slug: {'gamma_minimum_available': all(a in gamma_assets for a in assets),
                               'initial_snapshots_available': all(a in snapshots for a in assets)}
                        for slug, assets in sorted(markets.items())},
            'reached_planned_end': manifest.get('reached_planned_end') if manifest else None,
            'limitations': 'Feed-wide receive gaps do not prove per-outcome freshness or lossless delivery. Book freshness is independently enforced at every replay fill.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--in-progress', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit(args.capture, in_progress=args.in_progress)
    rendered = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end='')


if __name__ == '__main__':
    main()
