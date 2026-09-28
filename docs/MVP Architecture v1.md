# RescueAI — MVP Architecture

## 1. Purpose and operating boundary

RescueAI is a decision-support system for disaster rescue coordination. It converts SOS reports into structured information, evaluates feasible rescue routes, and proposes team assignments. A human dispatcher reviews and approves every operational recommendation. The system does not make autonomous rescue decisions, provide medical diagnoses, or guarantee that a road is safe.

This document defines a small, reproducible competition MVP. Features described as planned must not be presented as implemented until they work and have been tested.

## 2. Scope

### MUST HAVE

- Accept SOS reports containing text, an optional image, a location, and a timestamp.
- Extract structured information from SOS text with an AI extractor. Preserve the original report, identify missing information, and show which fields require human verification.
- Compute an explainable *suggested* priority from the extracted information. The dispatcher can review or correct it.
- Represent roads as a weighted graph with travel time, distance, risk, and blocked/open status.
- Find feasible routes and recalculate affected routes when road conditions change.
- Propose assignments using team availability, capabilities, and reachable travel times.
- Implement three comparable dispatch policies: FCFS, nearest feasible team, and RescueAI priority-aware assignment.
- Provide a dashboard that displays reports, teams, roads, routes, explanations, and proposals awaiting approval.
- Run reproducible simulations and calculate metrics from executable experiments.
- Test important algorithms and record the source and limitations of every dataset.

### NICE TO HAVE

- AI analysis of attached images.
- An OpenStreetMap-based background map and import of a small real road network.
- More advanced optimization for larger scenarios.
- Uncertainty analysis and sensitivity analysis for priority and road-risk parameters.

### POST-COMPETITION

- Live emergency-service integrations, live traffic or flood feeds, production identity management, and deployment for operational use.
- Forecasting road closures or disaster severity.
- Multi-stop rescue missions and detailed vehicle scheduling.

## 3. Components and ownership

Use a modular repository so three students can work in parallel:

- `backend/`: FastAPI endpoints, validation, persistence, and approval workflow.
- `backend/extraction/`: AI extraction interface, structured output validation, and an explicitly labeled offline fallback.
- `backend/priority/`: configurable, explainable priority recommendations.
- `backend/routing/`: road graph, feasible paths, and road-event handling.
- `backend/dispatch/`: FCFS, nearest-team, and priority-aware assignment policies.
- `backend/simulation/`: scenario playback, deterministic event processing, and metrics.
- `frontend/`: React dashboard and map or graph visualization.
- `experiments/`: versioned scenario definitions, experiment runner, and generated results.
- `tests/`: unit and integration tests.
- `docs/`: technical documentation, AI usage disclosure, prompt log, and demo materials.

Initial choices are Python, FastAPI, NetworkX, SQLite, pytest, and React. Use OR-Tools when the assignment problem justifies it. Keep the optimization policy behind an interface so its implementation can change without changing the API or simulation.

## 4. Data contracts

An SOS report has an ID, original text, optional image reference, reported timestamp, supplied location, structured extracted fields, field-level evidence or uncertainty where available, review status, and dispatch status. Useful extracted fields may include the number of people, reported injuries, mobility constraints, immediate hazards, and requested assistance. Unknown values remain unknown. Reports without a usable location cannot be routed until a dispatcher resolves the location.

A rescue team has an ID, graph position, capabilities, operational status, and a clearly defined capacity. The meaning of capacity must be stated in the scenario; do not silently treat vehicle seats, concurrent assignments, and maximum daily workload as interchangeable.

A road edge has an ID, endpoints, travel time, distance, risk value, and blocked/open status. Road events have timestamps and update specified edges. A blocked edge is excluded from future route calculations. In the MVP simulation, a team already traversing an edge finishes that edge; a closure affects entry into the edge thereafter.

An assignment proposal records the scenario state, proposed team–SOS pairs, predicted arrival times, routes, reasons, policy name, and approval status. Approval and rejection are separate auditable events. Producing a proposal must not silently change a team's operational assignment.

## 5. Extraction and priority

The AI extractor receives report text and returns validated structured fields. Store the extractor implementation or model identifier with its output. If a configured AI service cannot run locally, an offline rule-based fallback may keep the demo usable, but its output must be labeled as rule-based; it must not be reported as AI extraction performance.

Priority is a configurable recommendation based on reported evidence and reviewed fields. Keep extraction, priority rules, and dispatch optimization separate so each can be evaluated independently. Do not equate an uncertain model prediction with a verified fact. Show the reason for a suggested priority in the dashboard.

## 6. Routing, assignment, and replanning

All three dispatch policies use the same teams, capability constraints, graph state, route calculation, and simulation clock. A team–SOS pair is feasible only when the team is available, meets required capabilities, and has a reachable route.

- **FCFS:** Consider pending SOS reports in timestamp order; for each report, select its fastest feasible available team.
- **Nearest feasible team:** Repeatedly select the currently feasible team–SOS pair with the shortest travel time.
- **RescueAI:** Evaluate feasible assignments as a batch, prioritize coverage of higher-priority SOS reports, and then reduce priority-weighted predicted response time. Define the exact objective, tie-breaking rules, and optimizer status in code and experiment documentation.

On a road event, update the graph, identify routes affected by the change, and generate revised routes or proposals. The dispatcher must approve operational changes. If an SOS becomes unreachable, display that explicitly; do not fabricate a route or arrival time.

## 7. Simulation and evaluation

Start with small, clearly labeled **synthetic scenarios**. Fix random seeds and record scenario parameters. Run each dispatch policy on identical SOS arrivals, teams, road events, handling durations, and simulation horizons. The only intended difference in a baseline comparison is the dispatch policy. State any additional difference explicitly.

Measure response time from SOS creation to team arrival. Count an SOS as successfully served only according to a documented completion rule. Report at least:

- Mean response time among served SOS reports.
- Mean response time among served critical SOS reports.
- Priority-weighted response time among served SOS reports, with the weighting formula stated.
- Total travel distance.
- Number and proportion of SOS reports successfully served, including critical coverage.
- Replanning computation latency, measured separately from simulated travel time.
- Extraction Precision, Recall, and F1 only when a labeled evaluation set and scoring method exist.

Never report an average of zero when no relevant SOS was served; use `N/A` and report the coverage count. Preserve raw per-scenario outputs alongside aggregate results. Do not invent measurements, benchmark improvements, citations, or claims about real-world rescue outcomes.

## 8. Dashboard and human control

The dashboard should show the original SOS report beside extracted fields and uncertainty indicators. It should distinguish current road state, proposed routes, approved assignments, and unreachable reports. Every proposal needs an explicit approve or reject action and a readable explanation. A simulation may emulate approvals for experiments, but such approvals must be labeled as a simulation assumption.

## 9. Tests and completion criteria

Test extraction schema validation, unknown fields, priority explanations, team eligibility, shortest paths, blocked edges, unreachable locations, assignment uniqueness, road-event replanning, approval transitions, deterministic simulation replay, and metric calculations. Include a scenario in which the priority-aware policy makes a different assignment from a baseline.

The MVP is complete when a new contributor can install dependencies, run tests, start the backend and frontend, load a labeled synthetic scenario, inspect and approve a proposal, trigger a road closure, observe a revised recommendation, and reproduce the reported experimental metrics using documented commands.

## 10. Open decisions

The team must still provide the competition deadline and submission rules, demo area, source and license of any real road data, definitions of team capacity and SOS priority, available AI model credentials or local model, and a reviewed labeled dataset for extraction evaluation. Until then, use documented synthetic assumptions and mark real-world validation as pending.