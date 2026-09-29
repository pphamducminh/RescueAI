# RescueAI competition demo frontend

This is a dependency-free browser interface for the in-memory, synthetic
RescueAI demo. It calls the FastAPI `/api/demo` endpoints and is served at
`/demo` by the backend. Extraction and priority are rule-based suggestions;
the dispatcher must review each incident location and dispatch weight, then
approve a proposal. This demo is decision support, not a live emergency system.

## Build

```sh
cd frontend
npm run build
```

The build needs Node.js and no `npm install` or network access. It syntax-checks
`src/app.js` and writes `dist/index.html`, `dist/app.js`, and `dist/app.css`.
Run the FastAPI launch command in the repository README, then open `/demo`.
The generated `dist/` directory is ignored by Git and must be rebuilt after a
source edit.

## Demo flow

1. Submit SOS text and select a suggested incident node.
2. Inspect the evidence-backed extraction and priority suggestion.
3. Review the incident node, required team capabilities, and explicitly approve
   a dispatch weight. Basic capability is required for every reviewed request.
4. Inspect the proposed team assignment, route, ETA, and unserved reports.
5. Block or unblock a road to trigger a new proposal; the route map updates.
6. Explicitly approve the current proposal as dispatcher.

The map is a synthetic graph diagram, not geographic coordinates. Graph changes
invalidate the old proposal; only the current graph revision can be approved.
Use **Reset demo** to restore the initial synthetic state.
