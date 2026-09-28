# Experiments

This directory stores versioned generated artifacts. The
[`sos_benchmark/sample_100.jsonl`](sos_benchmark/sample_100.jsonl) file is a
synthetic, unaudited development sample with a distribution summary and SHA-256
checksum. It is not an extractor evaluation or a dispatch-policy experiment.
No policy run definition or result is **implemented** yet.

Future run definitions should reference synthetic scenario inputs from
`simulation/` and configure each policy under the same event stream and seed.
Store raw per-scenario outcomes alongside generated aggregate results here.
Record dependency versions, policy versions, metric definitions, and the exact
commands needed to reproduce each reported numerical claim. Metric calculation
and policy comparison code belongs in `evaluation/`.
