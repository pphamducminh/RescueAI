# RescueAI — falsifiable evaluation protocol v1

**Status:** proposed, preregisterable competition experiment; 2026-09-28. All numerical settings below are **synthetic engineering choices**, not validated disaster frequencies, clinical thresholds or measured benefits. This protocol measures first response in simulation, **not rescues completed, lives saved, or field effectiveness**. A human approves real operational actions; scripted approvals in simulation are an explicit modeling assumption.

## 1. Claims and experimental unit

**Primary claim to test:** Against the preselected **priority-aware greedy** baseline, RescueAI's one-wave min-cost-flow optimizer reduces a prespecified, *all-request* priority-weighted response-time metric by at least **5%** on the stated synthetic scenario distribution, without unacceptable degradation in critical-case response, coverage or computation time. Five percent is an illustrative *engineering decision threshold*, not clinical validation; freeze it before looking at test results. Secondary comparisons with FCFS and globally nearest-team greedy are always reported, including cases where they win.

**Unit of replication:** one scenario instance `(factor_cell_id, replicate_seed)` containing a graph, a set of SOS arrivals, team starts, fixed task handling times, critical labels and exogenous road-event script. All **four** algorithms replay exactly this instance. An individual SOS within a scenario is **not** an independent experimental replicate. The question answered is conditional on the stated synthetic generator and modeled human-approval policy; other disasters need separate evidence.

**Hold two studies apart:**

1. **Dispatch-only:** provide the same reviewed SOS snapshot, incident anchors, priority policy, capabilities and routes to every method. This estimates the effect of the *choice of assignment algorithm*.
2. **End-to-end:** use a separately frozen SOS extraction test split with deterministic structured ground truth and a stated identical human-review script. Report dispatch effects under actual extraction/uncertainty versus structured-ground-truth/reviewed inputs. Never use LLM-generated paraphrases or an LLM judge as ground-truth labels. Keep all variants of one SOS case in one split.

The optimizer implements the documented *RescueAI Assignment Model v1*: first minimize approved weighted requests still pending in one wave; then minimize priority-weighted ETA among equally covered plans. FCFS uses server receipt order and nearest eligible free team; nearest-team greedily chooses the globally shortest feasible team–SOS pair; priority-aware greedy orders requests by approved dispatch weight and chooses the nearest feasible free team. Stable tie rules: `(server_received_at, sos_id, team_id)` where applicable. No policy receives a better graph, priority label or availability forecast than another.

## 2. Frozen core experiment matrix

Generate **120-node, initially connected** synthetic geometric road graphs. Add edges to reach the target mean undirected degree, and draw edge distances and travel times by one documented seeded procedure. Apply initial closures *after* generation, including cases where closures disconnect the graph. Use the same route algorithm and graph snapshot for all policies. The values below intentionally span contrasting conditions; they do **not** estimate real disaster prevalence.

| Factor (7 factors) | Low level | High level | Recorded realized value |
| --- | --- | --- | --- |
| Number of SOS `N` | 32 | 96 | `n_sos` |
| Available team fraction `M/N` | `1/8` | `1/4` | `n_teams` = 4, 8, 12 or 24 respectively |
| Road density, target mean degree | 2.5 | 4.5 | actual post-generation degree, before closures |
| Initially blocked edges | 0% | 20% | exact blocked count / total edges |
| Critical-case proportion | 10% | 40% | exact integer count, with rounding rule recorded |
| SOS spatial clustering | approximately uniform | 3 sampled centers | realized cluster assignment and spread |
| Road-update frequency during 2 h of arrivals | 0 | 6 scripted events | exact timestamps and edge open/closed states |

The full `2^7=128` factorial cells allow all prespecified main-factor comparisons and two-factor interactions without confounding factors by construction. Use **8 independent replicate seeds per cell**: `128 × 8 = 1,024` distinct scenarios, replayed for four policies: **4,096 primary policy runs**. Pilot/debug seeds are disjoint from these eight held-out seeds. If runtime makes the target impossible, reduce the design **before** running held-out seeds, publish the revised matrix and avoid selective deletion of difficult cells. Factorial methods and interactions are standard experimental-design tools; see the [NIST design-of-experiments handbook](https://www.itl.nist.gov/div898/handbook/pri/section4/pri43.htm).

**One fully specified synthetic generator example:** place graph nodes uniformly in a `10 km × 10 km` square; join them by a Euclidean minimum spanning tree, then add shortest missing geometric edges until there are 150 or 270 undirected edges (`mean_degree=2E/120 = 2.5` or `4.5`). Edge distance is straight-line distance times `Uniform(1.0,1.4)`; edge time is distance divided by `Uniform(15,35)` km/h plus `Uniform(0,1)` minutes, rounded to a positive integer second. Sample initial blocked edges uniformly without replacement. For the clustered level choose three random graph nodes as centers and, for each SOS, sample a center then a graph node with probability proportional to `exp(-distance² / (2*(1 km)²))`; otherwise sample SOS nodes uniformly. Sample team start nodes uniformly. Draw receipt times `Uniform(0,120 minutes)` and SOS service durations as integer minutes `Uniform{15,...,45}`. When updates are enabled, sample six distinct times uniformly in `(0,120 minutes)`, sort them, choose an existing edge uniformly at each time and **toggle** its open/blocked state. All sampling/rounding and tie rules belong in the versioned generator code; these values are controlled tests, not geographic or epidemiological estimates.

Choose exactly `round(p_critical*N)` critical synthetic labels with a frozen rounding rule; sample a **structured SOS first**. For an illustrative report-fact generator, give half the critical cases an explicitly reported urgent sign (`trapped=false`, hazard `Bernoulli(0.25)`) and the other half explicitly reported trapped status plus active hazard (`urgent_sign=false`). For noncritical cases set `urgent_sign=false`, `trapped=false`, and hazard `Bernoulli(0.25)`. Apply reported injury `Bernoulli(0.15)`, vulnerable-group mention `Bernoulli(0.15)` and reported people counts from `{1,2,3,5,10}` with probabilities `{0.30,0.30,0.20,0.15,0.05}` to all cases. Every false/absent fact in this controlled dispatch-only arm is *explicitly documented as such*; the end-to-end arm introduces unknowns and conflicting reports. Draw capabilities explicitly (for example, one medically equipped team guaranteed per scenario and other teams equipped with probability `0.30`); required capability for a reported urgent-sign task must be defined in the generator. Treat facts and incident anchors as reviewed in the **dispatch-only** study, so all policies get identical accepted inputs. In the end-to-end arm, independently inject or extract uncertainty and use the same review gate for *all* policies. These artificial conditional probabilities must be frozen before testing and subjected to sensitivity analysis.

**Simulation contract:** A team becomes available at the incident node after its pre-sampled service duration; no unmodeled transport to hospitals. Run until minute `120 + H`, with `H=120 minutes` of per-request follow-up. Pre-sample team positions, edge times, capabilities and service times using separate named random streams keyed by `(cell, seed, component)`. Exogenous road events never draw new randomness based on a policy's actions. Team positions and therefore later ETA matrices **may legitimately differ** after policies choose different earlier assignments; the graph/event script and routing algorithm remain shared. On closure, an already-entered edge is completed; a closed edge cannot be entered afterwards. Teams on interrupted routes stop/replan under the same rule in every policy. Preserve genuine unreachable cases; never silently resample them away.

**Approval assumption:** all generated proposals receive the same scripted instantaneous approval in the dispatch-only study, solely to isolate policies. Repeat on a prespecified subset with a common fixed approval delay (e.g. two simulated minutes). This does not measure actual dispatcher behavior. The optimizer's real CPU wall time is reported separately rather than silently altering simulated arrival times.

**Solver failure rule:** a failed or timed-out decision creates no new assignment in that decision event; requests remain pending until the next event and the failure is logged. Do not silently substitute a greedy assignment or omit that scenario from the outcome table. Report compute time and simulated dispatch delay separately; if solver wall time becomes material relative to response time, run an explicitly labeled analysis that adds computation delay to every algorithm's simulated proposal time.

## 3. Outcome definitions and denominators

Let `r_i` be SOS server receipt time, `f_i` first **team arrival at the incident**, `R_i=f_i-r_i`, and `H=120 minutes`. If no team has arrived by `r_i+H`, define a **reporting value** `R_i^H=H`; otherwise `R_i^H=min(R_i,H)`. The synthetic scenario samples evaluation weight `v_i = 1+4*critical_label_i` **once before running policies**. This is a disclosed synthetic weighting rule, not an LLM score, clinical value or survival estimate. The dispatch algorithm may use a separate `w_i(now)` that changes under its published aging rule; `v_i` never depends on the outcome of a policy.

| Metric | Definition and missingness |
| --- | --- |
| **Primary, weighted capped response** | `L = sum_i(v_i * R_i^H)/sum_i v_i`, **over all SOS** in one scenario. Smaller is better; every unreached SOS counts as `H` by a declared censoring convention, never as an observed arrival. |
| Mean response time | Mean `R_i` among SOS actually reached within `H`; report reached count beside it. If count 0: blank/`NA`, **never 0**. |
| Critical mean response | Mean `R_i` among **reached critical** SOS, with critical reached count; `NA` when count 0. Also report `critical_mean_capped = mean(R_i^H | critical)` to prevent hiding unserved cases. |
| p90 response time | Nearest-rank 90th percentile of **all-request capped** `R_i^H` per scenario. Optionally add served-only p90 with count, clearly labeled. |
| Priority-weighted response, served-only | `sum_{reached} v_i R_i / sum_{reached} v_i`, `NA` if none; **secondary**, since deferring difficult cases can make it look better. |
| Distance | Sum of actually traversed road-edge distances by teams; log repositioning separately if added later. No invented straight-line distance for unreachable routes. |
| Served/unserved | Count `f_i <= r_i+H`; unserved is `N-served`, with reason (`no_anchor`, `unreachable`, `no_capable_team`, `capacity_busy`, `solver_failure`, `still_pending`, etc.). Arrival after `H` remains `unserved_by_H` and is separately flagged. |
| Critical delay | Critical served fraction by `H`, critical capped mean, and number exceeding a predeclared diagnostic delay threshold; **no clinical meaning** assigned to the threshold. |
| Optimizer runtime | Wall-clock milliseconds spent choosing a dispatch policy at a decision event; **exclude** graph routing, UI and simulation. Measure for *every* policy; report median, p95, max, timeouts and total. |
| Replanning latency | Wall-clock milliseconds from receipt of a graph update to a ready revised proposal, **including route recomputation and policy execution**; `NA` if no update, never a fabricated zero. Report median, p95, max and affected routes. |

For a fair algorithm comparison, the optimizer is permitted to prioritize coverage differently, but **all-request** primary `L`, coverage and critical capped time must be reported together. Increasing the cap `H` changes the estimand: fix `H=120` on the held-out suite and run a *labeled sensitivity analysis* at 60 and 180 minutes. Any metric with zero eligible observations is `NA` with a displayed denominator.

## 4. Paired analysis, uncertainty and no cherry picking

For metric `L`, let `L_{c,s,a}` denote the result in factorial cell `c`, seed `s`, algorithm `a`. Macro-average each cell equally so cells with `N=96` do not silently count three times as much as cells with `N=32`:

```text
mean_L(a) = (1/128) * sum_c [(1/8) * sum_s L[c,s,a]]
delta[c,s] = L[c,s,priority_greedy] - L[c,s,optimizer]
relative_improvement = (mean_L(priority_greedy) - mean_L(optimizer))
                       / mean_L(priority_greedy)
```

Report **mean and sample SD across paired scenario differences**, each algorithm's cell-equal macro-mean, and 95% paired uncertainty intervals. Resample **the complete four-policy result vector together**, stratified within each of the 128 fixed cells: resample eight seed indices with replacement per cell, compute each cell mean and then the equal-cell macro-mean; repeat e.g. **5,000 bootstrap replicates** with a recorded bootstrap seed. Report percentile 95% intervals of `relative_improvement` and paired absolute differences. This expresses uncertainty from the *specified synthetic generator and its seeds conditional on the 128 chosen factor cells*; it is not a confidence interval about all real disasters. Also show per-cell means/SD, sample counts and negative results. Paired intervals and bootstrap are established tools; see [NIST paired CI](https://itl.nist.gov/div898/handbook/prc/section3/prc312.htm) and [NIST bootstrap](https://itl.nist.gov/div898/handbook/eda/section3/bootplot.htm).

Do not calculate CIs by treating individual SOS from the same scenario as independent. For secondary comparisons to FCFS and nearest greedy, print paired estimates and intervals for both even when negative. If making three formal significance claims against three baselines, use a documented multiple-comparison adjustment (e.g. Holm); otherwise label secondary intervals descriptive. Use a frozen manifest listing factors, generator/solver versions, scenario hashes, seeds, outcome definitions, sample size and inference code. After any bug fix, version the change and rerun **every** algorithm on **every** held-out scenario; never delete an inconvenient seed or report only the best map. One illustrative demo map is allowed **in addition** to full results, not as evidence on its own.

## 5. Ablations, stress and failure cases

**Preselected ablations:** use the same scenarios and evaluation weights. On the first **four** held-out seeds per core cell (`128×4=512` scenario instances), additionally run (i) uniform dispatch weights `w_i=1`, (ii) no aging term in dispatch priorities, and (iii) periodic route replanning every 10 minutes rather than event-triggered replanning (on 64 cells with road updates: `64×4=256` instances). That is **1,280 extra policy runs** (`512+512+256`), in addition to the full and greedy algorithms already run. The periodic variant still checks closures before entering an edge. Secondary input-quality arm: compare reviewed/structured-ground-truth SOS against extraction with frozen missingness/errors and identical review scripts; never train or tune on its held-out SOS cases. Report effects as paired differences, including ablations that improve outcomes.

**Stress tests:** four predeclared configurations combining `N∈{200,500}` with diffuse arrivals versus a burst containing 70% of SOS in 10 simulated minutes; use `M≈N/8`, sparse graph, 40% blocked edges, 40% critical SOS and 12 road updates/hour on a larger documented graph. With **10 seeds per configuration**, that is **40 scenarios × four policies = 160 runs**. Always report solver timeouts, infeasibility, memory and loss of coverage. The numbers are stress settings, not real-world incident frequencies.

**Targeted failures:** six predeclared families with **five seeds each** (`30 scenarios × four policies = 120 runs`): (1) a disconnected critical SOS; (2) reporter-only GPS/missing incident anchor; (3) only capable team initially busy; (4) a bridge closure while a team is en route; (5) contradictory/uncertain SOS fields triggering review; (6) critical SOS clustered in the hardest-to-reach area. Show raw case traces and whether every method correctly surfaced pending/unreachable requests. Add deterministic unit tests for ties, zero critical cases, no available teams, overflow checks and solver errors; those are correctness checks, **not** replicate seeds.

**Total budget:** core 4,096 + ablations 1,280 + stress 160 + targeted failures 120 = **5,656 policy simulations**, plus disjoint pilot/debug work and separately stated end-to-end experiments. Timing repeats on a preregistered subset can be added without treating them as new independent scenarios.

## 6. Output files (CSV; UTF-8, one header row)

`scenarios.csv` — one row per `(cell_id, seed)`; record planned and realized factor levels:

```csv
scenario_id,cell_id,seed,scenario_hash,generator_version,n_sos,n_teams,road_nodes,mean_degree_target,mean_degree_actual,blocked_target_frac,blocked_actual_frac,critical_target_frac,critical_actual_count,cluster_mode,road_update_count,arrival_window_min,followup_h_min,event_script_hash,priority_policy_version
```

`runs.csv` — one row per `(scenario_id, algorithm, variant)`; numeric blanks mean `NA` and the reason is in `run_status`:

```csv
scenario_id,algorithm,variant,algorithm_version,run_status,solver_status,weighted_capped_min,mean_reached_min,critical_mean_reached_min,critical_mean_capped_min,p90_capped_min,weighted_reached_min,served_by_h,unserved_by_h,critical_served_by_h,critical_total,total_distance_km,decision_count,replan_count,optimizer_ms_total,optimizer_ms_p95,replan_ms_p95,solver_timeout_count,hardware_id,code_commit
```

`sos_results.csv` — one row per `(scenario_id, algorithm, variant, sos_id)` so every aggregate can be recomputed:

```csv
scenario_id,algorithm,variant,sos_id,received_min,first_arrival_min,response_min,capped_response_min,served_by_h,critical_ground_truth,eval_weight,unserved_reason,assigned_team_id
```

`event_log.csv` and `decision_log.csv` additionally record road events, route graph version, approved simulated assignments, capability checks, dispatch weights, solver status and per-event timing. Store missing first arrival as blank, not 0, infinity or `H`; only `capped_response_min` uses `H`. Record *both* `w_i(now)` in decisions and fixed `v_i` in per-SOS results. Link files using IDs and hashes, and provide a `manifest.json` with exact code commit, environment, seeds and predeclared settings.

## 7. Figures to publish, even when unfavorable

1. **Paired scatterplot:** priority greedy `L` on the horizontal axis versus optimizer `L` on the vertical axis for all 1,024 scenarios, equality diagonal; color by blocked level. Points **below** the diagonal favor the optimizer. Include the fraction of wins and losses.
2. **Factor-stratified forest plot:** paired mean improvement with 95% intervals for team fraction, blockage, critical share, clustering and update frequency, plus the prespecified high-blockage/high-critical stratum. Show denominators and negative bars.
3. **Heatmap:** `N` and team fraction by blocked level, cell-average paired `L` difference; small multiples for update frequency. Avoid a single pooled number concealing adverse interactions.
4. **Distribution + coverage:** empirical distribution/ECDF of *capped* request-level response times with a visible mass at `H`, adjacent to served-by-`H` bars. Annotate that SOS within a scenario are clustered observations.
5. **Trade-off plot:** critical capped mean versus primary `L`, marker size = unserved critical count; separate plot of distance versus `L` across policies.
6. **Scalability:** optimizer and full replan p50/p95/max wall time versus `N`; show timeouts and the exact hardware/software setup.

All plots use every intended cell and seed. Scenario examples shown in a live demo are explicitly marked illustrative.

## 8. Predeclared decision rule and falsification

On the **held-out core suite**, the broad synthetic-coordination claim passes only if all conditions hold:

1. The **lower 95% paired bootstrap bound** for optimizer's relative improvement in primary `L` versus priority greedy exceeds the illustrative **5%** threshold.
2. The **upper 95% bound** for optimizer minus priority greedy in *critical capped mean* is **below +5 minutes**, and the **lower 95% bound** for its served-by-`H` rate difference is **above −2 percentage points**. These are prototype guardrails, not clinically validated tolerances.
3. On the specified hardware, p95 per-decision optimizer time is **under 2 seconds**, and the core suite has **zero solver failures/timeouts**. Every failure in stress or other arms and any fallback behavior must be reported. No silent algorithm switch is allowed.
4. FCFS and nearest-team comparisons and all prespecified strata are published. If either baseline wins overall, or a high-risk stratum has a clear degradation, narrow the claim accordingly; do not describe the optimizer as uniformly better.

**Evidence against the hypothesis:** if the 95% interval's **upper** bound for relative improvement is at or below 5%, the data do not support the prespecified *meaningful* improvement; if at or below 0%, they support no mean improvement or a loss. If an interval crosses the 5% threshold, label the primary result **inconclusive**, not a win. A critical-delay or coverage guardrail failure invalidates the broad coordination claim even if `L` improves. Frequent solver timeouts, worse outcomes under blocked roads, or end-to-end benefit disappearing under extraction errors invalidate the corresponding deployability/robustness claims. These outcomes remain valid findings, not discarded runs. A positive synthetic result establishes performance only under this generator and scripted approval, never emergency-field safety.

## 9. Minimal reproducible execution sketch

```python
freeze_config_and_code_manifest()
for cell in all_128_factor_combinations():
    for seed in HELD_OUT_SEEDS:                    # e.g. 1000..1007
        scenario = generate_once(cell, seed)        # graph + arrivals + events
        validate_scenario_and_hash(scenario)
        for policy in [FCFS, NEAREST_GLOBAL, PRIORITY_GREEDY, OPTIMIZER]:
            result = replay(deepcopy(scenario), policy, scripted_approval=True)
            assert_invariants(result)               # no double booking/closed entry
            write_raw_logs_and_csv(result)
run_predeclared_ablations_stress_and_failure_suite()
verify_equal_scenario_hashes_across_policies()
compute_cell_equal_paired_estimates_and_stratified_CIs()
render_all_predeclared_tables_and_plots()
```

For small hand-constructed snapshots, independently enumerate all feasible matchings and confirm the optimizer's lexicographic objective; use this as a correctness gate. For the larger simulation, archive the four methods' decisions, team trajectories and event transitions so negative cases can be explained without changing them after the fact.
