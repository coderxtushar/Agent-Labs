# Security and threat model

This platform's job is to run untrusted code. Read this before exposing it to
anything but your own machine.

## What is treated as untrusted

All of it:

* benchmark repositories and their contents;
* task `setup_commands` and `tests.command`;
* code the agent writes, and commands the agent runs;
* agent adapter output;
* benchmark files uploaded through the API.

Nothing above `sandbox/` calls `subprocess` on task-controlled input. Commands
reach a container through its stdin (`sh -s`), never as a host argv element and
never through a host shell, so shell metacharacters in a filename or a task
definition cannot change the structure of a command. There is a test for exactly
this (`test_shell_metacharacters_in_content_are_not_evaluated`).

## Isolation: what you actually get

The only real provider is `docker`. Every container is created with:

| Control | Setting |
| --- | --- |
| Network | `--network none` unless the task declares `limits.network` otherwise |
| Capabilities | `--cap-drop ALL` |
| Privilege escalation | `--security-opt no-new-privileges` |
| Memory | `--memory` and `--memory-swap` equal, so OOM fails instead of swapping |
| CPU | `--cpus` |
| Processes | `--pids-limit` |
| `/tmp` | size-capped `tmpfs`, `nosuid`, `nodev` |
| User | non-root (uid 10001) in the bundled image |
| Host mounts | **none** |
| Wall clock | enforced per command; a timed-out container is killed, not just detached |

Never mounted or forwarded: the Docker socket, SSH keys, cloud credential
files, the host environment, or any host directory. There is a test asserting
the socket is absent and that a path from the host filesystem is unreachable.

**This is container isolation, not a virtual machine boundary.** A container
escape or a Linux kernel vulnerability reaches the host. Concretely, that
means:

* Do not run untrusted third-party benchmarks on a machine holding secrets or
  production credentials.
* Do not expose this service to untrusted users on shared infrastructure.
* For multi-tenant or genuinely adversarial workloads you need VM-level
  isolation (Firecracker, gVisor, Kata) or a remote sandbox service.
  `sandbox/base.Sandbox` is the interface to implement, and
  `sandbox/factory.py` is where to register it. **No such backend ships
  today** — the extension point exists; the implementation does not.

### The `local_unsafe` provider

`local_unsafe` runs task commands as ordinary child processes on the host, in a
temporary directory. It provides **no isolation**: no filesystem confinement, no
network restriction, no resource limits beyond a wall-clock timeout. It exists
so the platform's own test suite can run where no Docker daemon is available,
and for repositories you wrote yourself.

It refuses to instantiate unless `AGENTBENCH_ALLOW_LOCAL_UNSAFE_SANDBOX=true`,
logs a warning on every start, and every run records
`sandbox_provider: "local_unsafe"` so its results are never mistaken for
sandboxed ones.

### Network policy

`limits.network` accepts `none` (default), `model_only` and `full`.
`model_only` and `full` currently both attach the default bridge network. The
distinction is **recorded for auditing, not enforced by per-destination
firewalling.** If you need an agent to reach exactly one endpoint and nothing
else, put an egress proxy or firewall in front of the Docker network; the
platform does not do it for you, and does not claim to. A test asserts the
observable behaviour so this stays honest.

The `openai_tools` adapter avoids the question entirely: the model call happens
on the host and only the tool calls execute in the sandbox, so tasks keep
`network: none` and the provider key never enters the container.

## Secrets

* Secrets come from the process environment (or a secret manager that populates
  it). Nothing else is supported.
* `.env.example` contains placeholders only. Never commit a filled-in `.env`.
* The task schema **rejects** an `environment.env` key containing `API_KEY`,
  `SECRET`, `TOKEN`, `PASSWORD` or `PRIVATE_KEY`, because task definitions are
  exportable artefacts.
* Agent configurations are stored in the database, so secrets do not belong
  there either. `shell_command` takes `secret_env`: a list of *names* of host
  environment variables, read at run time and forwarded to the container
  without ever being persisted or logged.
* Everything written to the event stream or the structured log passes through
  `trajectory/redaction.py`, which masks both exact known secrets and
  credential-shaped values (`sk-`, `sk-ant-`, `ghp_`, `xox*-`, `AKIA…`, bearer
  headers, PEM blocks, sensitive-looking assignments). API responses redact
  agent config the same way.
* `GET /system/settings` reports credential **presence** as a boolean. It never
  returns a value.

## API exposure

The API can create and launch experiments, which means it can cause arbitrary
code to execute in containers. Treat it as a privileged control plane.

* Set `AGENTBENCH_API_TOKEN` for any deployment that is not loopback-only.
  Every mutating endpoint then requires `Authorization: Bearer <token>`.
* `agentbench serve` **refuses to start** on a non-loopback address without a
  token, unless you set `AGENTBENCH_ALLOW_INSECURE_BIND=true` to assert that
  something else authenticates in front of it.
* CORS is an allowlist (`AGENTBENCH_CORS_ORIGINS`), and credentials are not
  allowed on cross-origin requests.
* The unhandled-exception handler returns a stable error code only. Messages and
  tracebacks go to the redacted structured log, so host paths and configuration
  are not exposed through the API.
* Path traversal is rejected at the schema layer: `repo.path`,
  `grading.acceptance_tests` keys and `$file` targets may not be absolute or
  contain `..`, and `$file` targets must resolve inside the benchmarks
  directory. The `openai_tools` file tools refuse `..` and clamp a leading `/`
  into the repository.
* Request bodies are strict pydantic models (`extra="forbid"`), listings are
  paginated with bounded limits, and task listings omit hidden acceptance-test
  content.

## Resource exhaustion

* Per-task limits are clamped by global ceilings (`AGENTBENCH_MAX_*`). A task
  may request less, never more.
* `AGENTBENCH_MAX_CONCURRENT_RUNS` bounds the worker's thread pool, so the
  number of live containers is bounded.
* Captured stdout/stderr is truncated to `AGENTBENCH_LOG_TAIL_MAX_CHARS`, event
  payloads to `AGENTBENCH_EVENT_PAYLOAD_MAX_CHARS`, and diffs to a size budget.
  Truncation is always flagged rather than silent.
* Images are **never pulled implicitly** during a run. A missing image is an
  error telling you to pull or build it. This keeps runs offline-safe and stops
  a benchmark result from depending on what a registry served that day.

## Data retention

* Event payloads are redacted and truncated before storage.
* `AGENTBENCH_EVENT_RETENTION_DAYS` is available for pruning; `0` (the default)
  keeps everything, which is the right choice while debugging and the wrong one
  for a long-lived shared deployment.
* Patches and reports are written under `AGENTBENCH_DATA_DIR`. They contain
  agent-generated code; treat that directory with the same care as the
  repositories you benchmark.

## Known gaps

Stated plainly, because an undocumented gap is worse than a known one:

* **No VM-level isolation.** Container escape reaches the host.
* **No per-destination egress control.** `model_only` is advisory.
* **No multi-tenancy.** A single bearer token is all-or-nothing; there are no
  users, roles or per-experiment permissions.
* **No rate limiting.** Add it at the proxy.
* **No audit log of API actions** beyond the structured application log.
* **Host-side `git clone`** is used for `kind: "git"` tasks. Cloning does not
  execute repository code and hooks are not installed for a fresh clone, but the
  clone itself runs on the host rather than in a sandbox.
* **PostgreSQL is supported but not exercised** by the test suite; only SQLite
  is covered.

## Before a shared or public deployment

1. Put VM-level isolation behind `sandbox/factory.py`, or run the whole platform
   inside a disposable VM.
2. Set `AGENTBENCH_API_TOKEN`, terminate TLS, and rate-limit at the proxy.
3. Add real authentication and authorisation if more than one person uses it.
4. Restrict egress at the network layer rather than relying on
   `limits.network`.
5. Set `AGENTBENCH_EVENT_RETENTION_DAYS` and back up the database.
6. Run the API and the worker as separate processes
   (`AGENTBENCH_WORKER_IN_API_PROCESS=false`) so a worker crash cannot take the
   control plane with it.
