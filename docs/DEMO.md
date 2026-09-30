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

Open **http://127.0.0.1:8000/demo**. The interactive API reference is at
**http://127.0.0.1:8000/docs**; its OpenAPI JSON is at `/openapi.json`. The
health endpoint is `/health`. Build the frontend again after editing its source
files. There is no separate frontend server or `npm install` step.

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

## FastAPI integration endpoints

These root endpoints operate on the same in-memory demo session as the browser.
They reuse the SOS extraction, priority, graph, and dispatch modules. POST
responses below are JSON. Route edge IDs are directed and can be read from
`proposal.plan.assignments[*].route.edge_ids`.

| Endpoint | Request body | Response and effect |
| --- | --- | --- |
| `POST /sos` | `{"text":"...","suggested_node_id":"north"}` | Full `DemoState`, including the new report with extraction and provisional priority. The suggested node does not count as dispatcher review. |
| `GET /sos` | None | Array of `DemoReport` records. |
| `GET /teams` | None | Array of `DispatchTeam` records. |
| `POST /sos/{id}/review` | `{"incident_node_id":"north","dispatch_weight":5,"required_capabilities":["basic","medical"],"reviewer_id":"operator-demo"}` | Full `DemoState`; accepts the routing node, dispatch weight, and capabilities for that report. `basic` is required. |
| `POST /dispatch/plan` | None | Current `DemoProposal`, with assignments, routes, predicted response times, objective, and unserved reasons. Recomputes from the current graph and reviewed requests. Returns HTTP 409 if no SOS exists. |
| `POST /dispatch/proposals/{proposal_id}/approve` | `{"dispatcher_id":"operator-demo"}` | Full `DemoState`; records human approval of the current proposal. A stale or empty proposal cannot be approved. |
| `POST /roads/{id}/block` or `/unblock` | `{"actor_id":"operator-demo"}` | Full `DemoState`; updates one directed edge and automatically replans. |
| `POST /simulation/reset` | None | Full reset `DemoState`; clears reports, road events, approvals, and simulation steps. |
| `POST /simulation/step` | None | Full `DemoState`; increments the demo step counter and recomputes a planning proposal. It does not move teams. |
| `GET /system/state` | None | Full `DemoState`, including simulation step, graph revision, road statuses, reports, proposal, and approval record. |

Request schemas reject unexpected fields. Invalid JSON/schema values return
HTTP 422; invalid demo values return 400; unknown report or road IDs return
404; stale proposals and incompatible state changes return 409. The existing
`/api/demo/*` paths remain available for the browser. Planning does not itself
approve or execute assignments.

With the server running, this standard-library example submits and reviews an
SOS, blocks an edge in its first route, then confirms the recomputed plan omits
that edge:

```sh
.venv/bin/python - <<'PY'
import json
from urllib.request import Request, urlopen

base = "http://127.0.0.1:8000"

def request(method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if body is None else {"Content-Type": "application/json"}
    with urlopen(Request(base + path, data=body, headers=headers, method=method)) as response:
        return json.load(response)

request("POST", "/simulation/reset")
state = request("POST", "/sos", {
    "text": "2 people are trapped. One is unconscious.",
    "suggested_node_id": "north",
})
sos_id = state["reports"][-1]["sos_id"]
request("POST", f"/sos/{sos_id}/review", {
    "incident_node_id": "north",
    "dispatch_weight": 5,
    "required_capabilities": ["basic", "medical"],
    "reviewer_id": "operator-demo",
})
original = request("POST", "/dispatch/plan")
edge_id = original["plan"]["assignments"][0]["route"]["edge_ids"][0]
request("POST", f"/roads/{edge_id}/block", {"actor_id": "operator-demo"})
updated = request("POST", "/dispatch/plan")
assert updated["proposal_id"] != original["proposal_id"]
assert all(
    edge_id not in assignment["route"]["edge_ids"]
    for assignment in updated["plan"]["assignments"]
)
approved = request(
    "POST", f"/dispatch/proposals/{updated['proposal_id']}/approve",
    {"dispatcher_id": "operator-demo"},
)
assert approved["approved"]["proposal_id"] == updated["proposal_id"]
print(json.dumps({"blocked_edge": edge_id, "new_proposal": updated["proposal_id"]}))
PY
```

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
