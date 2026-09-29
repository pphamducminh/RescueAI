# Local competition demo

This demo joins the implemented SOS schema, a narrow offline phrase extractor,
provisional priority suggestions, a synthetic road graph, one-wave dispatch,
route display, road updates, replanning, and explicit dispatcher approval. It
is an in-memory demonstration, not an operational rescue service.

## Launch from the repository root

Use Python 3.11 or newer and Node.js. The frontend has no external JavaScript
dependencies. In one terminal:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm --prefix frontend run build
.venv/bin/python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/demo**. The API health endpoint is
`http://127.0.0.1:8000/health`. Build the frontend again after editing its
source files. There is no separate frontend server or `npm install` step.

## Walkthrough

1. Submit `2 people are trapped. One is unconscious.` and select **North district** as a *suggested* incident node. The rule extractor records exact quoted text evidence; the priority weight is provisional. The SOS remains pending because no dispatcher has accepted its node, capability need, or weight.
2. Enter a dispatcher ID in the header. In the review panel, explicitly accept **North district**, choose **basic** and **medical** capabilities, enter a dispatch weight such as `5`, and submit the review. A one-wave assignment proposal appears with a route and predicted first-response time. The proposed weight is an illustrative queue policy, not a medical diagnosis.
3. Block one directed road edge on the proposed route, such as `base_a_to_junction`. The graph revision changes and a new proposal is computed automatically. The blocked edge is absent from its route. The previous proposal ID cannot be approved after this update.
4. Approve the **current** proposal with a dispatcher ID. The approval record keeps its proposal ID, graph revision, actor and time. Blocking another road creates another proposal; it does not silently rewrite the recorded approval.
5. Use **Reset demo** to restore the synthetic map and clear in-memory reports and approvals.

The map is a synthetic six-node network. Roads are directed; blocking one
direction does not block its reverse edge. Teams start at fixed nodes. The
graph chooses a route by travel time plus the configured risk penalty, and
dispatch compares the resulting routes' travel ETAs. Approval records a
decision but does not move teams or contact responders.

## Verification and offline evaluation

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy .
npm --prefix frontend run build
.venv/bin/python -m evaluation.run --scenarios 100 --seeds 1 2 3 4 5 --strategies fcfs nearest greedy rescueai --output-dir evaluation/results/benchmark
.venv/bin/python -m evaluation.run_ablation --scenarios 100 --seeds 1 2 3 4 5 --strategies rescueai --output-dir evaluation/results/weight_ablation
```

The benchmark and ablation use their own frozen synthetic scenarios. They do
not evaluate the interactive demo, the phrase extractor, or real rescue
outcomes. Raw rows and configuration are saved beside each summary. See
[evaluation](../evaluation/README.md) for their exact scope.

## Boundaries

The phrase extractor recognizes only a disclosed English/Vietnamese demo
vocabulary. Unknown, hedged, and conflicting claims remain explicit; no LLM
or image model runs. The priority suggestion uses a small, unvalidated demo
rule rather than the full proposed priority policy. A dispatcher must enter
an accepted incident node, capabilities and dispatch weight before routing a
request. The service has no authentication, persistent storage, real map
data, live team positions, multi-wave simulation, or field dispatch action.
