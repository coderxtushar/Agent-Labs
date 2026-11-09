# AgentBench Lab

Reproducible evaluation, benchmarking and failure analysis for AI coding agents.

Give it a benchmark of real software-engineering tasks and an agent, and it will
run the agent in an isolated container, grade the result with tests the agent
never sees, record everything it observably did, and tell you *why* it failed —
distinguishing a wrong patch from a crashed harness from a dead Docker daemon.

The design premise is **evidence over assertion**. An agent never reports its
own success, every rate carries its denominator, and a number that cannot be
computed honestly is reported as missing rather than guessed.

---

## Quick start

Requirements: Python 3.11+, Docker, Node 20+.

```bash
# 1. Backend
cd backend
uv venv && uv pip install -e '.[dev]'      # or: python -m venv .venv && pip install -e '.[dev]'

# 2. Build the benchmark execution image (needs network; benchmark runs do not)
.venv/bin/python -m agentbench.cli.main images build

# 3. Create the schema, import the bundled benchmark and the demo agents
.venv/bin/python -m agentbench.cli.main bootstrap

# 4. Prove each task is executable AND falsifiable, in real containers
.venv/bin/python -m agentbench.cli.main benchmark validate

# 5. Run the benchmark with the deterministic reference agent
.venv/bin/python -m agentbench.cli.main experiment create \
    --name demo --agent reference-solution
.venv/bin/python -m agentbench.cli.main experiment run

# 6. Read the result
.venv/bin/python -m agentbench.cli.main experiment report <experiment-id>
```

Then the web UI:

```bash
cd backend && .venv/bin/python -m agentbench.cli.main serve   # API on :8000
cd frontend && npm install && npm run dev                      # UI on :5173
```

Point the UI at the API with `frontend/.env.development`:

```
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

Copy `.env.example` to `.env` to configure anything else. It contains
placeholders only; never commit a filled-in copy.

---

## What it does

**Benchmark registry.** Versioned task specs with a validated schema: pinned
repository revision, environment image, setup commands, `fail_to_pass` /
`pass_to_pass` test node IDs, hidden acceptance tests, forbidden paths,
resource limits, hints (withheld from the agent by default). Import/export as
JSON or JSONL.

**Task validation.** A task description existing is not evidence that the task
works. `agentbench benchmark validate` builds the real environment and checks
four things reading the spec cannot: the image exists and the repo
materialises; every declared test node is actually collected; every
`fail_to_pass` test **fails** on the pristine repo (falsifiability — otherwise
any agent scores a free success); every `pass_to_pass` test **passes** on it
(otherwise "regression" is indistinguishable from "already broken").

**Isolated execution.** Every run gets a disposable container with
`--network none`, `--cap-drop ALL`, `no-new-privileges`, memory/CPU/pids
limits, a size-capped `tmpfs`, a non-root user, and **no host mounts**.
Timeouts are enforced by killing the container, not just detaching. Teardown
runs in a `finally` block, and orphaned containers are reclaimed at worker
startup.

**Agent adapters.** A provider-independent contract. Four ship:

| Adapter | Real? | What it is |
| --- | --- | --- |
| `openai_tools` | yes | Model-driven agent loop over any OpenAI-compatible chat API. The model call happens on the **host**; its tools execute **in the sandbox**, so tasks keep `network: none` and the provider key never enters the container. |
| `shell_command` | yes | Bridge to an external CLI agent (Aider, Codex CLI, Claude Code, your own script) running inside the sandbox. |
| `scripted` | no | Applies fixed files/commands per task. Pointed at the bundled reference solutions it is a known-good agent — which is what makes an end-to-end `SOLVED` meaningful. |
| `mock` | no | Behaviour-driven test double: solve, no-op, wrong patch, tamper with tests, hang, crash, sandbox fault. |

`scripted` and `mock` are flagged `is_simulated`, and that flag is carried
through the API, the metrics and the UI. Their results validate the pipeline;
they are never presented as model benchmark numbers.

**Layered grading, independent of the agent.**

| Layer | Question | Failure means |
| --- | --- | --- |
| `environment` | Did repository setup succeed? | `INVALID_TASK` — the task is broken |
| `patch` | Is there a diff, and does it avoid `forbidden_paths`? | `UNSOLVED` — nothing done, or tests tampered with |
| `fail_to_pass` | Do the tests defining the task now pass? | `UNSOLVED` — incorrect implementation |
| `pass_to_pass` | Does the rest of the suite still pass? | `UNSOLVED` — regression introduced |
| `static:*` | Optional lint/type gates | `UNSOLVED` if `required`, else advisory |
| `judge` | Optional model review | Never affects the verdict; stored separately |

The hidden acceptance tests are injected **after** the diff has been captured,
and their paths are automatically added to `forbidden_paths`, so an agent can
neither read nor edit the tests that grade it. Per-test results come from
pytest's JUnit XML, not from an exit code, so "ran nothing and exited 0" cannot
look like success.

**Runner configuration is protected independently of the task.** A patch
touching `pytest.ini`, `setup.cfg`, `tox.ini`, `pyproject.toml` or any
`conftest.py` is rejected at the patch layer, and a task cannot opt out. This is
not theoretical: adding `addopts = -p <module>` to any one of those files loads
an agent-authored pytest plugin that replaces every test body, which made a
`SOLVED` verdict obtainable without touching `tests/` at all. All four injection
points were confirmed against real pytest and are now regression-tested. Tasks
that genuinely need dependency changes use `environment.pip_packages` or
`environment.setup_commands`, which run before the agent starts.

**Failure attribution, not a single "failed" bucket.**

```
solved · unsolved · timeout · agent_error   → counted in the success rate
infra_error · invalid_task · grading_error · cancelled   → excluded, reported separately
```

`passed` is `None` when a layer could not conclude, which becomes
`GRADING_ERROR` rather than a false `UNSOLVED`.

**Trajectory recording.** An append-only event stream per run, ordered by a
monotonic `seq` (never by timestamp, which collides): sandbox lifecycle, setup
commands, the task context given to the agent, model request/response metadata,
tool calls and results, file reads/writes, commands and exit codes, harness
context compaction, retries, the patch, every grading layer, termination. Two
deliberate limits: hidden model reasoning is **not** recorded because it is not
observable, and a field that could not be observed is `null` with a sibling
`"<field>_available": false` so "unknown" is distinguishable from "empty".
Payloads pass through secret redaction before storage.

**Metrics with denominators.** Every rate ships `{value, numerator,
denominator, ci_low, ci_high, small_sample, interpretation_warning}`. A zero
denominator is `null`, never `0.0`. Two success rates, both labelled:
`success_rate_attributable` (timeouts and agent errors count as failures) and
`success_rate_graded` (only runs that reached a verdict). `pass@k` is omitted
unless the experiment ran at least `k` repetitions, and names its estimator.
Confidence intervals are Wilson score. A two-proportion z-test appears only
when both arms have ≥10 scored runs, always with
`claims_significance: false`.

**Resumability.** Runs are rows with a `UNIQUE (experiment_id, task_id,
task_version, attempt_index)` idempotency key and a worker **lease**. Launching,
relaunching or resuming only creates what does not exist. A crashed worker's
runs become reclaimable when the lease expires; `resume` will not steal a run
whose lease is still live unless you pass `force`. An infrastructure retry
appends to `run_attempt_history` and never deletes the original failure.

**Failure analysis.** Every failed run gets an evidence-backed classification
(14 categories) citing the grading layers and exit codes it was derived from.
Provenance is explicit: `rule`, `model` or `human`. Human corrections are
appended as superseding rows; raw evidence is never mutated.

---

## The bundled benchmark

`benchmarks/textkit` is a small MIT-licensed Python library **written for this
project**, with three deliberate defects and three tasks over them:

| Task | Category | What it tests |
| --- | --- | --- |
| `textkit-slugify-collapse` | bug_fix | Runs of punctuation produce repeated/dangling separators |
| `textkit-wordwrap` | feature | `wrap_text` is declared but raises `NotImplementedError` |
| `textkit-truncate-boundary` | regression_fix | `truncate` exceeds its limit; the obvious fix breaks an existing test |

All three are **validated**: 20 hidden acceptance assertions fail on the
pristine repository and all 30 tests pass with the bundled reference solution.

Five demo agents exercise each outcome path: `reference-solution` (solves all
three), `partial-solution` (solves one), `regression-introducing` (passes the
hidden tests while breaking a pre-existing one), `no-op`, and `test-tamperer`
(tries to overwrite the graded test file).

---

## Architecture

```
frontend/ (React + TS)  →  backend/agentbench/api/ (FastAPI)
                                 │
         ┌───────────────────────┼────────────────────────┐
     registry/              orchestrator/             metrics/
  schema, loader,      workspace, runner,        compute, compare,
  service, validator,  experiment, worker        failure, export
  agents                       │
                     ┌─────────┴──────────┐
                 adapters/            grading/
             base, mock, scripted,  engine, patch,
             shell_command,         reports
             openai_tools, pricing
                     └─────────┬──────────┘
                           sandbox/
                   factory → DockerSandbox | LocalUnsafeSandbox
                                 │
                   trajectory/ (recorder, redaction)
                   db/ (SQLAlchemy models, SQLite/PostgreSQL)
```

Dependencies point downward only: grading does not know which adapter ran,
adapters do not know how grading works, and neither touches the database.

`docs/architecture.md` has the full run lifecycle, the grading rules, the
concurrency model and the extension points. `docs/security.md` has the threat
model. `docs/statistics.md` documents the denominators, the Wilson interval and
its assumptions, the `pass@k` estimator, and the provenance classification.

---

## CLI

```
agentbench bootstrap                     # schema + bundled benchmark + demo agents
agentbench images build                  # build the benchmark execution image
agentbench db init | db status
agentbench benchmark import <file> | list | validate [task] | export
agentbench agent import <file> | list | adapters
agentbench experiment create --name N --agent A [--tasks all|k1,k2] [--repetitions N]
agentbench experiment run | status <id> | cancel <id> | resume <id> [--force]
agentbench experiment report <id> --format markdown|json|csv
agentbench experiment compare <baseline> <candidate>
agentbench serve [--host --port]         # API (+ in-process worker)
agentbench worker                        # standalone worker
```

## API

`http://127.0.0.1:8000/docs` for interactive docs; 40 endpoints under
`/api/v1`. Highlights: `GET /overview`, `GET /compare`, `GET /failures`,
`POST /tasks/import`, `POST /tasks/{key}/validate`, `POST /experiments`,
`POST /experiments/{id}/{launch,cancel,resume}`,
`GET /experiments/{id}/{metrics,runs,report}`, `GET /runs/{id}`,
`GET /runs/{id}/events` and `GET /runs/{id}/events/stream` (SSE),
`GET /runs/{id}/patch`, `POST /runs/{id}/annotations`.

Set `AGENTBENCH_API_TOKEN` for any non-loopback deployment; `agentbench serve`
refuses to bind a public address without one.

---

## Running a genuine model-backed benchmark

The `openai_tools` adapter drives a real model. The model call happens on the
**host** and only its tool calls execute in the sandbox, so the task keeps
`network: none` and the provider key never enters the container.

### 1. Configure credentials in the environment

Never in a task file, never in an agent config, never on the command line in a
shell that records history.

```bash
export AGENTBENCH_OPENAI_API_KEY="sk-..."          # or put it in .env
export AGENTBENCH_OPENAI_BASE_URL="https://api.openai.com/v1"   # optional
```

Any OpenAI-compatible endpoint works: OpenAI, OpenRouter, Together, Groq, vLLM,
LM Studio, Ollama. Point `base_url` at it.

Confirm the platform can see the key without printing it:

```bash
.venv/bin/python -m agentbench.cli.main agent adapters \
  | python3 -c "import json,sys; print([a['requires_credentials'] for a in json.load(sys.stdin) if a['key']=='openai_tools'])"

curl -s localhost:8000/api/v1/system/settings | python3 -m json.tool | grep -A3 credentials_present
```

`GET /system/settings` reports presence as a boolean and never the value.

### 2. Register the agent

```bash
cat > /tmp/live-agent.json <<'JSON'
{"agents": [{
  "name": "gpt-4o-mini-tools",
  "version": "1.0",
  "adapter": "openai_tools",
  "model": "gpt-4o-mini",
  "description": "Model-driven agent loop; tools execute in the sandbox.",
  "config": {"model": "gpt-4o-mini", "max_tokens": 4096, "request_retries": 3}
}]}
JSON

.venv/bin/python -m agentbench.cli.main agent import /tmp/live-agent.json
.venv/bin/python -m agentbench.cli.main agent list
```

`agent list` prints `ready` per agent. If it says not ready, it names the
missing variable — fix that before spending tokens.

### 3. Run it

```bash
.venv/bin/python -m agentbench.cli.main experiment create \
    --name live-gpt4o-mini --agent gpt-4o-mini-tools --tasks all --repetitions 1
.venv/bin/python -m agentbench.cli.main experiment run
.venv/bin/python -m agentbench.cli.main experiment report <experiment-id>
```

The experiment records `provenance: live_model`, and the report, the CSV export
and the UI all label it as a model result. Token usage comes from the provider's
`usage` object; cost is computed from the dated in-repo price table and labelled
`estimated:<table-id>`, or left `null` when the model is unknown.

### Reproducing the bundled sample result

The published figure — 26.67% (4/15) — is produced by deterministic agents, so
it reproduces exactly:

```bash
rm -f data/agentbench.db*
cd backend
.venv/bin/python -m agentbench.cli.main images build
.venv/bin/python -m agentbench.cli.main bootstrap
.venv/bin/python -m agentbench.cli.main benchmark validate
for a in reference-solution partial-solution regression-introducing no-op test-tamperer; do
  .venv/bin/python -m agentbench.cli.main experiment create --name "$a-run" --agent "$a" --tasks all
done
.venv/bin/python -m agentbench.cli.main experiment run --max-seconds 900
```

Every one of those experiments is labelled `simulated`. They validate the
pipeline; they are not a model benchmark.

---

## Verified results

Everything below was executed on this machine (macOS 15 arm64, Docker 29.8.0,
Python 3.12.15, Node 24.22.0). Commands and counts are exact.

### Test suite

```
cd backend && .venv/bin/python -m pytest -q
→ 446 passed, 4 skipped in 591.51s
```

The 4 skips are the live-model tests and one network-dependent git clone; each
reports its reason with `-rs`.

```
.venv/bin/ruff check agentbench tests   → All checks passed!
.venv/bin/ruff format --check           → 80 files already formatted
.venv/bin/mypy agentbench               → Success: no issues found in 57 source files
```

Coverage by area:

| Area | Tests | Notes |
| --- | --- | --- |
| API | 53 | Validation, auth, pagination, redaction, error shape |
| Adapter contract, pricing | 44 | All 4 adapters, config validation, cost provenance |
| JUnit parsing, redaction, recorder | 41 | Exit-code classification, 5 secret shapes, seq ordering |
| **`openai_tools` provider contract** | **38** | **Real local HTTP server: retries, 429 + Retry-After, malformed JSON, error-on-200, bad tool args, path escapes, step cap, budget cut-off** |
| Experiment lifecycle | 37 | Idempotency, lease races, crash recovery, resume |
| Task schema, loader, import/export | 35 | Path traversal, credential rejection, `$file` escapes, round-trip |
| State machine, rates, pass@k | 33 | Illegal transitions, Wilson interval, estimator by hand |
| **Provenance and metric verification** | **32** | **Hand-computed Wilson/pass@k, denominator treatment, provenance in metrics/report/CSV/compare** |
| Docker sandbox isolation | 27 | Network, mounts, capabilities, timeout, cleanup |
| Run execution and attribution | 21 | 5 distinct failure attributions, hidden-test protection |
| Non-simulated adapters | 21 | Real external shell agent solves a task in Docker |
| **Backing services** | **20** | **Real PostgreSQL 16 container, real git clone, worker process kill/recovery** |
| **Grading integrity** | **18** | **Four proven runner-config injection vectors, rejected at the patch layer** |
| Patch diffing, forbidden paths | 17 | Added/deleted/binary, determinism, ignore rules |
| End-to-end in Docker | 11 | Bundled benchmark, comparison, report export |

### Real benchmark runs

```
agentbench benchmark validate
→ textkit-slugify-collapse@1.0   valid
  textkit-wordwrap@1.0           valid
  textkit-truncate-boundary@1.0  valid
```

Five experiments, 15 runs, all in Docker containers:

| Agent | Outcome | Failure category |
| --- | --- | --- |
| `reference-solution` | 3 solved / 3 | — |
| `partial-solution` | 1 solved / 3 | `no_patch_produced` ×2 |
| `regression-introducing` | 0 solved / 3 | `existing_regression` ×1, `no_patch_produced` ×2 |
| `no-op` | 0 solved / 3 | `no_patch_produced` ×3 |
| `test-tamperer` | 0 solved / 3 | `forbidden_files_modified` ×3 |

Overall: **26.67% (4/15)**, 95% Wilson CI 10.9–52.0%.

Three load-bearing behaviours this demonstrates:

- `test-tamperer` overwrote the graded test file with a trivially passing one
  and was **rejected at the patch layer before any test ran** — the task was not
  gameable.
- `regression-introducing` made the hidden acceptance tests pass while breaking
  a pre-existing test; the `pass_to_pass` layer caught it and it was classified
  `existing_regression`, not credited.
- A deliberately mis-specified task (a `fail_to_pass` node that no runner
  collects) produced `GRADING_ERROR`, not a false `UNSOLVED`.

### Frontend

```
cd frontend
npx tsc --noEmit   → clean
npm run build      → built in 716ms (792 kB js / 14 kB css)
```

Driven in a real Chromium via Playwright: all 8 screens plus
`/experiments/:id` and `/runs/:id`, every run-detail tab, the task drawer, the
import modal, the comparison selectors, light/dark toggle, and a 1024px
viewport. **Zero console errors.** Screenshots in `frontend/screenshots/`.

Three bugs were found by that browser pass and fixed: a 500 on
`GET /tasks/benchmarks` (`func.case` instead of `sqlalchemy.case` — now covered
by a regression test), a React duplicate-key warning on the failures table, and
a score rendered without its denominator.

### Not verified

Stated plainly, because an undocumented gap is worse than a known one.

| Capability | Status |
| --- | --- |
| **`openai_tools` against a live model** | **Still not run. No provider credentials exist in this environment, and none were fabricated.** The adapter's provider contract is now exercised end to end against a real local HTTP server (38 tests): retries, 429 with `Retry-After`, non-retryable 401, malformed JSON on a 200, error payloads served with HTTP 200, JSON that is not an object, empty `choices`, malformed tool arguments, unknown tool names, path-escape refusal, the step cap, and budget cut-off. What remains unverified is *model inference itself*. The live test is `tests/integration/test_real_adapters.py::TestOpenAIToolsAdapterLive` and **skips** without `AGENTBENCH_OPENAI_API_KEY`. |
| Multi-host / multi-worker at scale | **Not exercised.** Single-host crash recovery is now tested by killing a real worker process; lease races are unit-tested. Nothing has been run across two machines. |
| VM-level sandbox isolation | **Not implemented.** `sandbox/base.Sandbox` is the extension point; only `docker` and the explicitly unsafe `local_unsafe` exist. |
| Model-assisted judging | **Not implemented.** The structure exists and reports `available: false`; it can never affect the deterministic verdict. |
| Fully graded run over a `kind: "git"` task | **Not exercised.** The clone, revision pinning, cache and `.git` stripping are tested against a real repository, and an HTTPS clone at a pinned SHA is tested against a real remote. A *graded* run needs a remote actually hosting the fixture repository, which does not exist. |
| Training-data generation / RL environments | **Not implemented, by choice.** What exists is the substrate: complete event streams, captured patches, deterministic verdicts with per-layer evidence, and a reproduction snapshot per experiment. |

### Newly verified in this phase

| Capability | Evidence |
| --- | --- |
| **PostgreSQL persistence** | 6 tests against a real `postgres:16-alpine` container: migrations apply, `alembic check` reports no drift, nested JSON columns round-trip, the idempotency constraint is enforced by the database, relaunch does not duplicate runs, only one worker wins a claim, and a full run executes and grades with correct metrics. |
| **Git-backed task sources** | 12 tests: a real local repository is cloned at a pinned SHA, `.git` is stripped from the agent's workspace, the clone is cached by revision, an unknown revision is an `INVALID_TASK`, the `file://` and non-HTTP(S)/SSH allowlist is enforced at both import and run materialisation, and a real HTTPS clone at a pinned SHA succeeds against a live remote. |
| **Worker restart recovery** | A real `agentbench worker` process is started, observed claiming a run, then killed. The run is left non-terminal, becomes reclaimable once its lease expires, and a second worker drives it to a terminal outcome. A completed run is never re-run. |
| **Grading integrity** | The four proven runner-config injection vectors are rejected at the patch layer before any test executes, and a task cannot opt out. |
| **Metric correctness** | Wilson bounds and `pass@k` asserted against independent hand computation; denominator treatment of incomplete and non-attributable runs asserted explicitly. |
| **Result provenance** | Every experiment records `simulated` / `offline_adapter_test` / `live_model` / `external_agent` at launch, surfaced in metrics, the Markdown report, a CSV column, and the UI. |

---

## Security

This platform runs untrusted code. Read `docs/security.md` before exposing it
beyond your own machine. The short version:

- Container isolation is **not** a VM boundary. A container escape reaches the
  host. Do not run untrusted third-party benchmarks on a machine holding
  secrets.
- Set `AGENTBENCH_API_TOKEN` for any non-loopback bind. The API can cause
  arbitrary code execution in containers; treat it as a privileged control
  plane.
- `limits.network: "model_only"` is **recorded, not enforced** by
  per-destination firewalling. Use a real egress proxy if you need that.
- Secrets live in the environment only. Task specs reject credential-shaped
  `env` keys; agent configs never store secrets; logs and events are redacted;
  `GET /system/settings` reports credential *presence* as a boolean.
- The `local_unsafe` provider has **no isolation** and refuses to start without
  an explicit opt-in.

Known gaps: no VM isolation, no per-destination egress control, no
multi-tenancy or roles, no rate limiting, no API audit log.

---

## Layout

```
backend/
  agentbench/
    config.py logging_setup.py
    db/ schemas/ registry/ sandbox/ adapters/
    trajectory/ grading/ orchestrator/ metrics/ api/ cli/
  alembic/            # migrations (applies clean, no drift, reversible)
  tests/              # 337 tests; fixtures/minirepo is a self-contained repo
benchmarks/
  textkit/            # sample repository (MIT, written for this project)
  acceptance/textkit/ # hidden grading tests — outside the repo by design
  solutions/textkit/  # reference + deliberately-regressing solutions
  tasks/textkit.json  # the benchmark
  agents/builtin.json # demo agent configurations
docker/               # benchmark execution image
docs/                 # architecture.md, security.md
frontend/             # React + TypeScript UI, screenshots/
.env.example
```

## License

MIT. The bundled `textkit` benchmark repository was written for this project
and carries no third-party code.
