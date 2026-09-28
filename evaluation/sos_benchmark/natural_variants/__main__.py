"""Offline command line entry point for Task 6 render and import stages."""

import argparse
import json
from pathlib import Path

from .pipeline import DEFAULT_VARIANT_DIR, import_variants
from .specs import DEFAULT_RENDER_SPECS_PATH, export_render_specs, load_benchmark_cases

DEFAULT_CASES_PATH = Path("experiments/sos_benchmark/sample_100.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("export", help="export provider-independent render specs")
    render.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    render.add_argument("--output", type=Path, default=DEFAULT_RENDER_SPECS_PATH)
    render.add_argument("--limit", type=int)
    ingest = commands.add_parser("import", help="import external JSONL batches offline")
    ingest.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    ingest.add_argument("--input", type=Path, required=True)
    ingest.add_argument("--output-dir", type=Path, default=DEFAULT_VARIANT_DIR)
    args = parser.parse_args()

    cases = load_benchmark_cases(args.cases)
    if args.command == "export":
        count = export_render_specs(cases, args.output, args.limit)
        print(json.dumps({"render_specs": count, "output": str(args.output)}, sort_keys=True))
    else:
        summary = import_variants(args.input, cases, args.output_dir)
        print(json.dumps(summary.model_dump(mode="json"), sort_keys=True))


if __name__ == "__main__":
    main()
