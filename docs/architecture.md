# Architecture

## Why it is shaped this way

The platform exists to answer "did this agent actually solve the task, and if
not, whose fault was it?". Three design decisions follow from that, and
everything else is detail:

1. **Grading is independent of the agent.** The agent never reports its own
   success. The tests that decide the verdict are not in the repository the
   agent works in; they are injected after the agent's diff has already been
   captured.
2. **Failures are attributed, not merged.** A wrong patch, a crashed harness, an
   exhausted budget, a broken task definition and a dead Docker daemon produce
   five different outcomes. Collapsing them into "failed" would make every
   metric meaningless.
3. **State lives in the database, not in a process.** A run's position in its
   lifecycle, its event stream and its verdict are rows. Killing the worker
   mid-run loses no evidence and duplicates no work.

## Component map

```
                         ┌───────────────────────────────┐
                         │  React + TypeScript frontend  │
                         └───────────────┬───────────────┘
                                         │ HTTP + SSE
                         ┌───────────────▼───────────────┐
                         │      FastAPI  (api/)          │
                         │  tasks agents experiments      │
                         │  runs  metrics  system         │
                         └──┬──────────┬─────────────┬───┘
                            │          │             │
          ┌─────────────────▼──┐  ┌────▼─────────┐  ┌▼──────────────────┐
          │ registry/          │  │ orchestrator/│  │ metrics/          │
          │  TaskSpec schema   │  │  experiment  │  │  compute          │
          │  loader (JSON/L)   │  │  worker      │  │  compare          │
          │  service (CRUD)    │  │  runner      │  │  failure          │
          │  validator         │  │  workspace   │  │  export           │
          │  agents            │  └──┬────────┬──┘  └───────────────────┘
          └────────────────────┘     │        │
                                     │        │
                   ┌─────────────────▼──┐  ┌──▼──────────────────┐
                   │ adapters/          │  │ grading/            │
                   │  base (contract)   │  │  engine (layers)    │
                   │  mock, scripted    │  │  patch (diffing)    │
                   │  shell_command     │  │  reports (JUnit)    │
                   │  openai_tools      │  └──┬──────────────────┘
                   │  pricing           │     │
                   └─────────┬──────────┘     │
                             │                │
                   ┌─────────▼────────────────▼──────────┐
                   │ sandbox/   factory → Sandbox         │
                   │   DockerSandbox | LocalUnsafeSandbox │
                   └──────────────────┬───────────────────┘
                                      │
                   ┌──────────────────▼───────────────────┐
                   │ trajectory/ recorder + redaction     │
                   │ db/  models + session (SQLite/PG)    │
                   └──────────────────────────────────────┘
```

Dependencies point downward only. `grading` does not know which adapter ran;
`adapters` do not know how grading works; neither touches the database. The
orchestrator is the only module that wires them together, and
`sandbox/factory.py` is the only place that knows which isolation backend is in
use.

## Lifecycle of a benchmark run

```
     PENDING ──claim(lease)──> CLAIMED ──> PREPARING ──> AGENT_RUNNING
                                                              │
                                 COLLECTING <─────────────────┘
                                      │
                                   GRADING ──> FINALIZING ──> COMPLETED
                                                                  │
   (any active state) ──cancel──> CANCELLED                  outcome ∈
   (any active state) ──infra fault──> PENDING (retry)        solved
                                                              unsolved
                                                              timeout
                                                              agent_error
                                                              infra_error
                                                              invalid_task
                                                              grading_error
```

Step by step, with the honesty rule each step enforces:

1. **Materialise** the repository at its pinned revision into a per-run host
   directory, and snapshot it. *The snapshot is the diff baseline, so the agent
   cannot influence what "unchanged" means.*
2. **Create the sandbox** from the task's image and resource budget. Network is
   disabled unless the task opts in. *The agent's environment is defined by the
   task, not by whatever the host happens to have installed.*
3. **Upload** the pristine tree and run the task's setup commands. *A non-zero
   exit here is `INVALID_TASK`: the task author's problem, not the agent's.*
4. **Run the agent** through its adapter, with a deadline, a step cap and a
   cancellation flag it must poll. The hidden acceptance tests do not exist in
   the workspace at this point. *Hints are withheld unless the experiment opts
   in.*
5. **Capture the patch** by downloading the workspace and diffing it against the
   host snapshot. *Computed on the host, so no benchmark image needs `git` and
   added/deleted files are captured like edits.*
6. **Grade** in layers: environment → patch → `fail_to_pass` → `pass_to_pass` →
   optional static checks → optional model judge. The acceptance tests are
   injected at the start of this step, *after* the diff is final. *A layer that
   cannot conclude yields no verdict (`GRADING_ERROR`) rather than a false
   `UNSOLVED`.*
7. **Finalise**: write the outcome, classify the failure from stored evidence,
   and tear down the sandbox and workspace in a `finally` block.

## Grading layers

| Layer | Question | Failure means |
| --- | --- | --- |
| `environment` | Did repository setup succeed? | `INVALID_TASK` — the task is broken |
| `patch` | Is there a diff, and does it avoid `forbidden_paths`? | `UNSOLVED` — nothing done, or the tests were tampered with |
| `fail_to_pass` | Do the tests defining the task now pass? | `UNSOLVED` — incorrect implementation |
| `pass_to_pass` | Does the rest of the suite still pass? | `UNSOLVED` — regression introduced |
| `static:*` | Optional lint/type gates | `UNSOLVED` if `required`, advisory otherwise |
| `judge` | Optional model review | Never affects the verdict; stored separately |

`passed` is `True` only when every required deterministic layer passed, `False`
when one definitively failed, and `None` when any layer could not reach a
conclusion.

## Why tests cannot be gamed

* The `fail_to_pass` tests live in `grading.acceptance_tests` inside the task
  spec, not in the repository. They are written into the workspace only in step
  6.
* Their paths are automatically added to `grading.forbidden_paths` by the task
  schema, so a patch touching them fails the `patch` layer before any test runs.
* Per-test results come from pytest's JUnit XML, not from the exit code, so
  "ran nothing and exited 0" cannot look like success.
* `declared_tests_collected` in the validator rejects a task whose node IDs the
  runner never reports, which is what would otherwise make a task silently
  unpassable.

## Task validation

A task description existing is not evidence the task works. `validate_task`
builds the real environment and checks four things that reading the spec cannot:

* the image is present and the repository materialises;
* every declared test node is actually collected;
* every `fail_to_pass` test **fails** on the pristine repository (falsifiability
  — otherwise any agent scores a free success);
* every `pass_to_pass` test **passes** on the pristine repository (otherwise
  "regression" is indistinguishable from "already broken").

The validation report is stored on the task and carried onto every run created
from it, so a result always records whether its task had been validated.

## Concurrency and recovery

Durability comes from two columns on `runs`:

* `claimed_by` / `claim_expires_at` — a worker claims a run with a conditional
  `UPDATE ... WHERE state = 'pending'`. Two workers racing cannot both win.
* A claim is a **lease**. A worker refreshes it while the agent runs; if the
  worker dies, the lease expires and the run becomes reclaimable. `agentbench
  worker` reclaims expired runs and removes orphaned containers at startup.

Idempotency comes from `UNIQUE (experiment_id, task_id, task_version,
attempt_index)`. Launching, relaunching or resuming an experiment only creates
rows that do not exist, so completed work is never repeated or duplicated.

Resuming does not steal a run whose lease is still live, because that worker may
still be alive; `force=true` overrides this when you know it is gone.

Redis and an external broker are deliberately absent. The lease table already
gives at-most-once execution and crash recovery for a single host.
`claim_next_run` is the seam to replace if multi-host scheduling is ever needed.

## Trajectory recording

Every run has an append-only event stream ordered by a monotonic per-run `seq`
(never by timestamp, which collides). Events cover sandbox lifecycle, setup
commands, the task context handed to the agent, model request/response metadata,
tool calls and results, file reads and writes, commands and exit codes, context
compaction performed by the harness, retries, the generated patch, every grading
layer, and termination.

Two deliberate limits:

* **Hidden model reasoning is not recorded, because it is not observable.**
  `model.response` carries the finish reason, token counts and the assistant
  text the provider returned. Nothing claims to be chain-of-thought.
* **Unavailable is distinguished from empty.** A field that could not be
  observed is `null` with a sibling `"<field>_available": false`; a truncated
  payload sets `truncated` on the event row.

Payloads pass through two redaction layers before storage: exact known secrets
from settings, and credential-shaped values (provider key prefixes, bearer
tokens, PEM blocks, sensitive-looking `KEY=value` pairs). Over-masking a log
line is cheap; leaking a key is not.

## Metrics

Everything is computed from stored rows at read time; nothing is cached.

* Every rate carries its numerator and denominator. A zero denominator is
  reported as `null`, never `0.0`.
* **Two success rates, both labelled.** `success_rate_attributable` counts
  timeouts and agent errors as failures. `success_rate_graded` counts only runs
  that reached a verdict. Infrastructure failures, invalid tasks and
  cancellations are excluded from both and reported in `excluded_from_rates`.
* `pass@k` is omitted unless the experiment ran at least `k` repetitions per
  task, and the estimator used is stated in the payload.
* Confidence intervals use the Wilson score interval (the normal approximation
  produces bounds outside `[0, 1]` for the near-0 and near-1 proportions typical
  of benchmarks). Small samples carry an explicit warning.
* The comparison endpoint checks comparability rather than assuming it:
  differing task sets, task versions, grading versions or
  simulated-vs-real agents produce explicit blockers.
* A two-proportion z-test is reported only when both arms have at least 10
  scored runs, and always with `claims_significance: false`.

## Extension points

| To add | Implement | Register in |
| --- | --- | --- |
| A new agent harness | `adapters.base.AgentAdapter` | `adapters/registry.py` |
| A stronger sandbox (microVM, remote) | `sandbox.base.Sandbox` | `sandbox/factory.py` |
| A new grading layer | a `LayerResult` producer | `grading/engine.py` |
| A new test runner | `grading/reports.py` parser + `TestRunner` enum | `schemas/task.py` |
| A model-based failure classifier | return `ClassificationSource.MODEL` | `metrics/failure.py` |

The interfaces for training-data generation are intentionally *not* built. What
exists is the substrate they would need: a complete per-run event stream, the
captured patch, deterministic verdicts with per-layer evidence, and
`ExperimentRecord.config_snapshot` describing exactly what produced each result.
