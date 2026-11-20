"""Replay a completed real public capture. Results are never an official score."""
import argparse
import hashlib
import json
import math
import runpy
from pathlib import Path

from replay.capture import RecordedEvents, timestamp
from replay.cli import reference_strategy, source_hash
from replay.engine import Replay
from yeno.strategy import Bot, Config


def load_outcomes(path):
    outcomes = {}
    for row in json.loads(path.read_text()):
        published_at = timestamp(row["resolvedAt"])
        if (row["resolution"] not in ("YES", "NO") or published_at < timestamp(row["endDate"])
                or row["id"] in outcomes):
            raise ValueError(f"Invalid venue resolution for {row['id']}")
        outcomes[row["id"]] = (published_at, row["resolution"])
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--reference-dir")
    parser.add_argument("--house", action="store_true", help="Compare the official starter house bot")
    parser.add_argument("--variants", action="store_true", help="Compare the fixed preregistered candidates")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--from-slot", type=int, help="Include markets opening at/after this Unix second")
    parser.add_argument("--through-slot", type=int, help="Include markets opening at/before this Unix second")
    parser.add_argument("--outcomes", type=Path, help="Venue market outcomes including resolvedAt")
    parser.add_argument("--minimum-interval", type=float, default=0, help="Minimum seconds between replay snapshots")
    args = parser.parse_args()
    if not math.isfinite(args.minimum_interval) or args.minimum_interval < 0:
        parser.error("minimum interval must be finite and nonnegative")
    outcomes = load_outcomes(args.outcomes) if args.outcomes else {}
    events = RecordedEvents(args.capture, args.minimum_interval)
    bots = {"harbor": Replay(Bot().decide, outcomes=outcomes)}
    if args.house:
        house_path = Path(__file__).resolve().parents[1] / "research/yeno-house-bot-v1.py"
        bots["house"] = Replay(runpy.run_path(str(house_path))["HouseBot"]().decide, outcomes=outcomes)
    if args.variants:
        bots["warmup"] = Replay(Bot(Config(learn_without_target=True)).decide, outcomes=outcomes)
        bots["edge04"] = Replay(Bot(Config(minimum_model_edge=0.04)).decide, outcomes=outcomes)
    if args.reference_dir:
        bots["reference"] = Replay(reference_strategy(args.reference_dir), outcomes=outcomes)
    active = set(bots)
    included_markets = set()
    for event in events:
        slot = round(event["timestamp"] + event["market"]["secondsToClose"] - 300)
        if args.from_slot is not None and slot < args.from_slot:
            continue
        if args.through_slot is not None and slot > args.through_slot:
            continue
        included_markets.add(slot)
        for name in sorted(active):
            if not bots[name].step(event):
                active.remove(name)
    hashes = {}
    for name in ("raw.jsonl.gz", "supplemental.jsonl", "manifest.json"):
        path = args.capture / name
        if path.exists():
            with path.open("rb") as source:
                hashes[name] = hashlib.file_digest(source, "sha256").hexdigest()
    result = {"evidence": "REAL_PUBLIC_FORWARD_CAPTURE", "official_parity": False,
              "minimum_snapshot_interval_seconds": args.minimum_interval,
              "outcomes_sha256": hashlib.sha256(args.outcomes.read_bytes()).hexdigest() if args.outcomes else None,
              "source_sha256": source_hash(), "capture_file_sha256": hashes,
              "contained_market_windows": sorted(events.covered_markets), "complete_data_validated": False,
              "data_diagnostics": dict(events.stats),
              "included_market_openings": sorted(included_markets),
              "candidate_configurations": {"harbor": {}, **({"warmup": {"learn_without_target": True},
                  "edge04": {"minimum_model_edge": 0.04}} if args.variants else {})},
              "strategies": {k: v.run(()) for k, v in bots.items()},
              "limitations": ["Public upstream books; not Builderr's private capture or canonical evaluator.",
                              "Target is provisional until matching a causally received exact-opening TWAP; missing samples block entries.",
                              "Observation freshness uses local receive time; original source times preserved.",
                              "When venue outcomes are supplied, expired inventory blocks trades until the recorded resolution time."]}
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
