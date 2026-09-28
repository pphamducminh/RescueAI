# Simulation scenarios

This directory is reserved for versioned scenario inputs used in reproducible
dispatch experiments. Scenario data and playback are **not implemented**.

The first scenarios should be clearly labeled **synthetic** and record the SOS
arrivals, team state, road graph, road events, handling durations, simulation
horizon, and random seed. Each dispatch policy must receive the same input
scenario and seed. Keep executable simulation logic in `backend/simulation/`.

The meaning of team capacity and the completion rule for a served SOS remain
open decisions in `docs/MVP Architecture v1.md`; define them before creating
scenarios or reporting metrics.
