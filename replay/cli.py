from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib
import json
from pathlib import Path
import sys

from yeno.strategy import Bot, Config
from .engine import Replay
from .synthetic import events


def reference_strategy(directory: str):
    """Load an explicitly supplied, locally reviewed reference in this process."""
    path = Path(directory).resolve()
    if not (path / "bot" / "strategy.py").is_file():
        raise ValueError("Reference directory must contain bot/strategy.py")
    sys.path.insert(0, str(path))
    module = importlib.import_module("bot.strategy")
    memory = module.Memory()
    return lambda observation: module.decide(observation, mem=memory)


def rows(path: Path):
    with path.open() as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def source_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for directory in ("yeno", "replay"):
        for path in sorted((root / directory).glob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local diagnostic; not the official evaluator")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Receive-time JSONL with observedAt on each outcome book")
    source.add_argument("--synthetic", choices=("smooth", "choppy", "thin", "lagged"))
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--markets", type=int, default=288)
    parser.add_argument("--adverse", type=float, default=0.0, help="Adverse price shift per execution leg")
    parser.add_argument("--reference-dir", help="Optional local reviewed reference source; never downloaded automatically")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.markets <= 0 or not 0 <= args.adverse < 0.5:
        parser.error("Use positive markets and 0 <= adverse < 0.5")
    bots = {"harbor": Replay(Bot().decide, adverse=args.adverse)}
    if args.reference_dir:
        bots["reference"] = Replay(reference_strategy(args.reference_dir), adverse=args.adverse)
    active = set(bots)
    digest, count = hashlib.sha256(), 0
    source_rows = rows(args.input) if args.input else events(args.seed, args.markets, args.synthetic)
    for row in source_rows:
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n")
        count += 1
        for name in sorted(active):
            if not bots[name].step(row):
                active.remove(name)
    report = {"evidence": "SYNTHETIC_DIAGNOSTIC" if args.synthetic else "USER_DATA_LOCAL_REPLAY",
              "official_parity": False, "input_sha256": digest.hexdigest(), "source_sha256": source_hash(),
              "configuration": asdict(Config()), "rows": count,
              "regime": args.synthetic, "seed": args.seed if args.synthetic else None,
              "adverse_per_leg": args.adverse, "strategies": {k: v.run(()) for k, v in bots.items()}}
    text = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
