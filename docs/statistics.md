# Statistics and reproducibility

What every number in this platform means, how it is computed, and what it does
not support. The guiding rule: a figure that cannot be computed honestly is
reported as missing.

## Denominators

Each terminated run gets exactly one outcome. Which denominator it lands in is a
property of the outcome, not a judgement call:

| Outcome | In `success_rate_attributable`? | In `success_rate_graded`? | Why |
| --- | --- | --- | --- |
| `solved` | yes (numerator) | yes (numerator) | the agent solved it |
| `unsolved` | yes | yes | the agent was graded and failed |
| `timeout` | yes | no | the agent's own budget ran out: a capability failure, but no verdict |
| `agent_error` | yes | no | the agent or its harness crashed: attributable, but no verdict |
| `infra_error` | no | no | our Docker daemon, not the agent |
| `invalid_task` | no | no | the task's environment is broken |
| `grading_error` | no | no | grading could not conclude |
| `cancelled` | no | no | a human stopped it |

Two rates are therefore published, always labelled:

* **`success_rate_attributable`** = `solved / (solved + unsolved + timeout + agent_error)`.
  This is the figure comparable to how SWE-bench-style numbers are usually
  quoted, and the one shown as the headline.
* **`success_rate_graded`** = `solved / (solved + unsolved)`. Useful when
  timeouts are an artefact of a budget you intend to change.

Runs excluded from both are counted in `reliability.excluded_from_rates` so the
gap is always visible.

**Unfinished runs are in no denominator at all.** A `pending` or in-flight run
is not a failure; `completeness.run_completeness`, `pending_runs` and
`active_runs` report them, and `warnings` carries an explicit "incomplete
experiment" entry.

**A zero denominator yields `null`, never `0.0`.** "No data" and "0%" are
different claims.

## Confidence intervals

### Method

The **Wilson score interval** at 95% (`z = 1.959963985`), two-sided:

```
                  p + z²/2n  ±  z·√( p(1-p)/n + z²/4n² )
    bounds  =  ─────────────────────────────────────────────
                            1 + z²/n
```

implemented in `metrics.compute.wilson_interval`.

### Why Wilson and not the normal approximation

Benchmark proportions sit near 0 or 1 and samples are small. The Wald interval
`p ± z·√(p(1-p)/n)` produces bounds outside `[0, 1]` there, and collapses to
zero width at `p = 0` or `p = 1` — it would report a 3/3 result as "100%,
CI 100–100%". Wilson stays inside the unit interval and keeps non-zero width at
the extremes.

### Assumptions, and where they do not hold

The interval assumes each run is an independent Bernoulli trial with a common
success probability. For a benchmark that is **not true**:

* **Tasks are not exchangeable.** One task may be trivial and another
  impossible, so the pooled `p` describes this task mix, not a population.
* **Repetitions of one task are correlated.** The same model on the same task
  fails in correlated ways.
* **Tasks are not independent of each other.** Several may share a defect
  pattern or a repository.

So the interval answers a narrow question — "how precisely did we measure the
success rate *on this exact task set*?" — and not "what would this agent score
on software engineering?". Treat it as a lower bound on uncertainty. When
`denominator < 10`, `small_sample` is `true` and
`interpretation_warning` is populated.

### Worked example

The bundled demonstration scores 4 of 15:

```
n = 15, c = 4, p = 0.266667, z = 1.959963985
1 + z²/n            = 1.2560973
p + z²/2n           = 0.3947154
z·√(p(1-p)/n + z²/4n²) = 0.2578300
low  = (0.3947154 − 0.2578300) / 1.2560973 = 0.108975
high = (0.3947154 + 0.2578300) / 1.2560973 = 0.519504
```

→ **26.67% (4/15), 95% CI 10.9–52.0%.** A 41-point-wide interval on 15 runs is
the honest answer, and it is why no ranking claim is made from it.

`tests/integration/test_provenance_and_metrics.py` asserts these exact bounds
against an independent hand computation. That test caught a wrong interval
(9.7–51.1%) in an earlier README.

## pass@k

The unbiased estimator from Chen et al. (2021):

```
pass@k = 1 − C(n−c, k) / C(n, k)
```

for `n` samples of which `c` succeeded. Rules:

* **Omitted unless `n ≥ k`.** Estimating pass@k from fewer than k samples is
  extrapolation. `pass_at_k()` returns `None`, and the payload explains why.
* Reported only for `k ≤ repetitions`.
* The experiment-level figure is the **unweighted mean across tasks**, and is
  only emitted when every task has a value for that `k`.
* The estimator is named in the payload (`pass_at_k.estimator`) so a reader
  never has to guess which variant was used.

## Comparisons

`GET /compare` checks comparability instead of assuming it. These produce
**blockers**, and the comparison renders flagged as not directly comparable:

* different task sets, or different task versions;
* different grading versions (different rules produced the verdicts);
* different benchmark versions;
* different **result provenance** — comparing a simulated run against a live
  model run compares different kinds of thing.

Softer issues become **caveats**: an incomplete arm, one repetition per task,
or infrastructure failures in either arm.

Per-task movement (`newly_solved`, `newly_broken`, `improved`, `degraded`,
`unchanged`) is always reported, because a net-zero success rate can hide three
fixes and three breakages.

### Significance

A two-proportion z-test is computed **only** when both arms have at least 10
scored runs. It always carries `claims_significance: false` and a note that runs
are not i.i.d. across tasks. Use it to decide how much more testing to do, not
to declare a winner.

## Result provenance

`is_simulated` is a boolean, and a boolean cannot separate the four kinds of
evidence this platform produces. Every experiment records one of these at launch,
derived from the adapter's declared capabilities and whether its required
credentials were actually present:

| Provenance | Meaning | A model result? |
| --- | --- | --- |
| `simulated` | deterministic non-model adapter (`mock`, `scripted`) | no |
| `offline_adapter_test` | model-backed adapter ran without credentials, so no inference happened | no |
| `live_model` | real inference against a model provider | **yes** |
| `external_agent` | a real external agent process (`shell_command`) | no |

It is carried through `GET /experiments/{id}`, experiment metrics, the Markdown
report, a `provenance` column in the CSV export, and a badge plus caveat banner
in the UI. Only `live_model` is a statement about a model.

## What is recorded for reproduction

`ExperimentRecord.config_snapshot`, frozen at launch and included in every
exported report:

* **benchmark**: id and version, plus per task the key (`task_id@version`),
  category, difficulty, grading version, validation status and container image;
* **agent**: adapter, model, provider, prompt version, tools version, sampling
  parameters, the full non-secret config, provenance, and the readiness report
  observed at launch;
* **execution**: repetitions, concurrency, seed, whether hints were given,
  limit overrides, sandbox provider, infra retry limit, and the global hard
  ceilings in force;
* **platform**: the AgentBench Lab version.

Editing an agent or a task afterwards cannot change what a completed experiment
measured, because the snapshot is a copy rather than a reference.

## Claims this platform does not make

* That one model is better than another at programming. The benchmarks here are
  small and hand-written; a difference on them is a difference on them.
* That a success rate generalises beyond the exact task versions measured.
* That a confidence interval accounts for task-selection bias. It does not.
* That a p-value below 0.05 establishes an improvement.
* That a simulated or offline-adapter result says anything about a model.
