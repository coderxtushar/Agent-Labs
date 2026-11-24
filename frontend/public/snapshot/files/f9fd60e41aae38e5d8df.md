# Experiment report: live-ornith-1.5-9b-local

Generated 2026-11-24T18:44:59.524633+00:00 by AgentBench Lab.

> **Live model run.** Produced by real model inference. Scope is limited to the tasks in this benchmark; it does not measure general coding capability.

## Configuration

| Field | Value |
| --- | --- |
| Experiment ID | `7bf4df4967b44a9193bd5973527ac69f` |
| Result provenance | **Live model run** (`live_model`) |
| State | completed |
| Benchmark | textkit@1.0 |
| Agent | ornith-1.5-9b-local@1.0 (adapter `openai_tools`) |
| Model | ornith-1.5-9b-32k |
| Prompt / tools version | 1.0 / 1.0 |
| Grading version | 1.0 |
| Repetitions per task | 1 |
| Seed | None |
| Sandbox provider | docker |
| Hints given to agent | False |

## Completeness

- Runs: 3 finished of 3 (0 pending, 0 in flight)
- Complete: **True**

## Headline results

| Metric | Value | Numerator / denominator | 95% CI |
| --- | --- | --- | --- |
| Success rate (agent-attributable) | 33.33% | 1 / 3 | 6.2% – 79.2% |
| Success rate (graded only) | 100.0% | 1 / 1 | 20.6% – 100.0% |
| pass@1 | 33.33% | 1 / 3 | 6.2% – 79.2% |

Confidence intervals use the Wilson score interval, 95%. No significance claim is made.

### Outcome breakdown

| Outcome | Count | Counts toward success rate |
| --- | --- | --- |
| solved | 1 | yes |
| unsolved | 0 | yes |
| timeout | 0 | yes |
| agent_error | 2 | yes |
| infra_error | 0 | no |
| invalid_task | 0 | no |
| grading_error | 0 | no |
| cancelled | 0 | no |

### Reliability

- Regressions (pass_to_pass broken): 0
- Timeout rate: 0.0% (0/3)
- Infrastructure failure rate: 0.0% (0/3)
- Infrastructure retries: 0
- Runs excluded from success rates: 0

### Runtime and cost

- Total wall clock: 1911.64s (mean 637.213s, median 604.731s, p90 760.934s)
- Tokens: in 53275, out 7023
- Cost: None USD (sources: none reported)
- Runs with no reported usage: 2

### pass@k

not reported: the experiment ran 1 repetition(s) per task

## Per-task results

| Task | Category | Attempts | Solved | Unsolved | Timeout | Other | Regressions | Mean s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `textkit-slugify-collapse@1.0` | bug_fix | 1 | 0 | 0 | 0 | 1 | 0 | 604.731 |
| `textkit-truncate-boundary@1.0` | regression_fix | 1 | 0 | 0 | 0 | 1 | 0 | 760.934 |
| `textkit-wordwrap@1.0` | feature | 1 | 1 | 0 | 0 | 0 | 0 | 545.974 |

## By category

| Category | Tasks | Attempts | Solved | Success rate |
| --- | --- | --- | --- | --- |
| bug_fix | 1 | 1 | 0 | 0.0% (0/1) |
| feature | 1 | 1 | 1 | 100.0% (1/1) |
| regression_fix | 1 | 1 | 0 | 0.0% (0/1) |

## Failure categories

| Category | Count |
| --- | --- |
| tool_invocation_error | 2 |

## Caveats

- only 3 scored run(s): differences of a few percent are noise
- a single repetition per task cannot show run-to-run variance; raise repetitions to measure reliability

## Reproduction

```json
{
  "created_at": "2026-11-20T12:46:29.665818+00:00",
  "benchmark": {
    "id": "textkit",
    "version": "1.0",
    "tasks": [
      {
        "key": "textkit-slugify-collapse@1.0",
        "category": "bug_fix",
        "difficulty": "easy",
        "grading_version": "1.0",
        "validation_status": "valid",
        "image": "agentbench/benchmark-python:3.12"
      },
      {
        "key": "textkit-truncate-boundary@1.0",
        "category": "regression_fix",
        "difficulty": "medium",
        "grading_version": "1.0",
        "validation_status": "valid",
        "image": "agentbench/benchmark-python:3.12"
      },
      {
        "key": "textkit-wordwrap@1.0",
        "category": "feature",
        "difficulty": "medium",
        "grading_version": "1.0",
        "validation_status": "valid",
        "image": "agentbench/benchmark-python:3.12"
      }
    ]
  },
  "agent": {
    "agent_config_id": "f4ef33c1937146a3affb0d3bc1bf0ff0",
    "name": "ornith-1.5-9b-local",
    "version": "1.0",
    "adapter": "openai_tools",
    "provider": null,
    "model": "ornith-1.5-9b-32k",
    "prompt_version": "1.0",
    "tools_version": "1.0",
    "config": {
      "model": "ornith-1.5-9b-32k",
      "base_url": "http://127.0.0.1:11434/v1",
      "max_tokens": 8192,
      "request_retries": 3
    },
    "sampling": {},
    "is_simulated": false,
    "provenance": "live_model",
    "include_hints": false,
    "capabilities": {
      "key": "openai_tools",
      "display_name": "OpenAI-compatible tool-calling agent",
      "description": "Host-side agent loop; tools run inside the sandbox. Works with any endpoint exposing /chat/completions with tool calling.",
      "is_simulated": false,
      "requires_network": true,
      "requires_credentials": [
        "AGENTBENCH_OPENAI_API_KEY"
      ],
      "reports_token_usage": true,
      "reports_cost": true,
      "supports_cancellation": true,
      "streams_events": true,
      "contract_version": "1.0",
      "config_schema": {
        "model": {
          "type": "string",
          "required": true
        },
        "base_url": {
          "type": "string",
          "description": "Defaults to the configured provider"
        },
        "max_tokens": {
          "type": "integer",
          "default": 4096
        },
        "allow_run_command": {
          "type": "boolean",
          "default": true
        },
        "request_retries": {
          "type": "integer",
          "default": 3
        }
      }
    },
    "readiness_at_launch": {
      "ready": true,
      "detail": "credentials present for http://127.0.0.1:11434/v1",
      "missing": []
    }
  },
  "execution": {
    "repetitions": 1,
    "concurrency": 1,
    "seed": null,
    "include_hints": false,
    "limits_override": {},
    "sandbox_provider": "docker",
    "infra_retry_limit": 2,
    "hard_ceilings": {
      "max_agent_timeout_s": 3600,
      "max_memory_mb": 4096,
      "max_cpus": 4.0
    }
  },
  "platform": {
    "version": "0.1.0"
  }
}
```
