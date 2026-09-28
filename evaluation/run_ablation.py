"""Compare reviewed versus uniform approved dispatch weights on paired cases.

Only the one-wave weight ablation is implemented. Missing reviewed weights stay
pending review, and fixed evaluation weights do not change between variants.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .run import DEFAULT_HORIZON_SECONDS, BenchmarkConfig, run_benchmark

DEFAULT_OUTPUT_DIR = Path("evaluation/results/weight_ablation")


def run_ablation(
    *,
    scenarios_per_seed: int,
    seeds: tuple[int, ...],
    strategies: tuple[str, ...] = ("rescueai",),
    horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Persist baseline and weight-ablated raw records for the same scenarios."""

    return run_benchmark(
        BenchmarkConfig(
            scenarios_per_seed=scenarios_per_seed,
            seeds=seeds,
            strategies=strategies,
            variants=("baseline", "uniform_weights"),
            horizon_seconds=horizon_seconds,
            output_dir=output_dir,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=int, default=100, help="generated cases per seed")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1])
    parser.add_argument("--strategies", nargs="+", default=["rescueai"])
    parser.add_argument("--horizon-seconds", type=int, default=DEFAULT_HORIZON_SECONDS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    try:
        report = run_ablation(
            scenarios_per_seed=args.scenarios,
            seeds=tuple(args.seeds),
            strategies=tuple(args.strategies),
            horizon_seconds=args.horizon_seconds,
            output_dir=args.output_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "variants": ["baseline", "uniform_weights"],
                "scenario_variant_count": report["scenario_variant_count"],
                "strategy_run_count": report["strategy_run_count"],
                "failure_count": report["failure_count"],
                "raw_files": ["scenarios.jsonl", "runs.csv", "requests.csv"],
                "summary": "summary.json",
            },
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
