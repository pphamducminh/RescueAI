# RescueAI

RescueAI is a **decision-support project** for disaster rescue coordination. The intended workflow turns SOS reports and changing road conditions into recommendations for a human dispatcher to review. A dispatcher makes operational decisions; the software does not make autonomous rescue decisions.

## Current status

This repository has a **local competition demo**, not a working rescue service. The demo connects [SOS Schema v1](docs/SOS%20Schema%20v1.md), a narrow offline phrase extractor, provisional priority suggestions, a [dynamic road graph](backend/routing/README.md), [one-wave dispatch](backend/dispatch/README.md), a route dashboard, road blocking, automatic proposal replanning, and explicit dispatcher approval. It uses a synthetic map and in-memory state. LLM extraction, an operational approval system, persistence, live teams, and end-to-end event replay remain unimplemented. See the [demo launch guide](docs/DEMO.md).

An [offline synthetic SOS benchmark generator](evaluation/sos_benchmark/README.md) now produces typed Vietnamese text reports and gold annotations for development. The 100-record sample is synthetic and has not been human reviewed; no extractor scores are reported.

The optional [SOS natural-language variation pipeline](evaluation/sos_benchmark/natural_variants/README.md) exports provider-independent render specs from frozen benchmark cases and validates imported paraphrases offline. It does not call a generation API or change ground truth. Variants need independent semantic review and fresh evidence spans before inclusion in a frozen benchmark.

The agreed scope and completion criteria are in [MVP Architecture v1](docs/MVP%20Architecture%20v1.md). Do not describe a planned component as functional until its behavior and tests exist.

## Development setup

Use Python 3.11 or newer. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Configuration is read from environment variables. `.env.example` documents `RESCUEAI_ENV` and the integer `RESCUEAI_SIMULATION_SEED`. The standalone dispatch comparison accepts its seed through `--seed`; use the same value to reproduce its synthetic scenario inputs. The `.env` file is local and ignored by Git.

Run the checks:

```sh
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy .
```

Generate and summarize the reproducible benchmark sample:

```sh
.venv/bin/python -m evaluation.sos_benchmark.generator --seed 412073 --count 100 --output experiments/sos_benchmark/sample_100.jsonl
.venv/bin/python -m evaluation.sos_benchmark.summary experiments/sos_benchmark/sample_100.jsonl
```

Export ten safe render specs from that sample:

```sh
.venv/bin/python -m evaluation.sos_benchmark.natural_variants export \
  --cases experiments/sos_benchmark/sample_100.jsonl --limit 10
```

See the [variation pipeline instructions](evaluation/sos_benchmark/natural_variants/README.md) for the import format and review boundary.

Measure road-graph routing and replanning latency on deterministic grid inputs:

```sh
.venv/bin/python -m evaluation.road_graph_benchmark \
  --sides 5 10 20 30 --seed 412073 --warmups 10 --repeats 100 \
  --output evaluation/results/road_graph_latency.json
```

See the [routing guide](backend/routing/README.md) for graph semantics and
costs, and the [measured results](evaluation/results/README.md) for the
workload and its limits.

Compare all four dispatch strategies on the same seeded, synthetic one-wave
scenarios and write raw per-request CSV, per-scenario CSV, and full JSON results:

```sh
.venv/bin/python -m evaluation.dispatch_comparison \
  --seed 412073 --scenarios 8 --horizon-seconds 3600 \
  --output-prefix evaluation/results/dispatch_comparison
```

The comparison assumes instant **simulated** approval and first arrival at the
planned route time. It does not operate the API or dispatch teams. See the
[dispatch guide](backend/dispatch/README.md) and [evaluation guide](evaluation/README.md)
for the objective, metrics, and limits.

Run the repeatable dispatch benchmark on generated scenarios, with each seed
producing 100 scenarios that all four strategies share:

```sh
.venv/bin/python -m evaluation.run \
  --scenarios 100 --seeds 1 2 3 4 5 \
  --strategies fcfs nearest greedy rescueai \
  --output-dir evaluation/results/benchmark
```

The runner saves its configuration, seeds, scenario snapshots, raw per-run and
per-request CSV, and a validated JSON summary. The sample command produces
500 scenario snapshots and 2,000 strategy runs if every run completes. To
revalidate saved raw results or run the paired uniform-dispatch-weight ablation:

```sh
.venv/bin/python -m evaluation.aggregate_metrics --input-dir evaluation/results/benchmark
.venv/bin/python -m evaluation.run_ablation \
  --scenarios 100 --seeds 1 2 3 4 5 --strategies rescueai \
  --output-dir evaluation/results/weight_ablation
```

The separate road-graph trial runner saves every timed route, block, and
replan operation before summarizing its measured latency:

```sh
.venv/bin/python -m evaluation.benchmark_routing \
  --sides 5 10 20 30 --seed 412073 --warmups 3 --repeats 30 \
  --output-dir evaluation/results/routing_benchmark
```

See the [evaluation guide](evaluation/README.md) for artifact names, metrics,
failure handling, and the difference between these one-wave workloads and the
proposed full evaluation protocol.

Build the dependency-free frontend and start the local demo API:

```sh
npm --prefix frontend run build
.venv/bin/python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/demo`. In another terminal, run
`curl http://127.0.0.1:8000/health` to check that the API
process is running. The [demo guide](docs/DEMO.md) gives a step-by-step SOS,
review, road-blocking, replanning and approval walkthrough.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `backend/api/` | FastAPI transport and request handling |
| `backend/domain/` | Shared typed domain schemas, separate from API logic |
| `backend/extraction/`, `backend/priority/` | SOS contracts and narrow offline demo rules |
| `backend/demo/` | In-memory synthetic demo state and review/approval integration |
| `backend/routing/`, `backend/dispatch/` | Dynamic road graph and four one-wave dispatch proposal strategies |
| `backend/simulation/` | Seed helper; future simulation clock and playback logic |
| `simulation/` | Placeholder for versioned synthetic scenario inputs |
| `evaluation/` | Offline synthetic SOS benchmark and optional variant triage; routing trials, dispatch benchmark runner, ablation, and raw-result validation |
| `experiments/` | Synthetic benchmark development sample and future experiment runs |
| `data/sos_benchmark/natural_variants/` | Task 6 render specs and separate raw, accepted, rejected, and review queues |
| `frontend/` | Dependency-free local route and review dashboard |
| `tests/` | Backend and domain tests |
| `docs/` | Architecture, AI use, and dataset documentation |

See [Architecture](docs/ARCHITECTURE.md), [AI usage](docs/AI_USAGE.md), and [Dataset card](docs/DATASET_CARD.md) for boundaries and reporting requirements.
