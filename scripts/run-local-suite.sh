#!/usr/bin/env bash
# Run the bundled benchmark against local Ollama agents, several repetitions
# each, one model at a time. Meant for overnight runs on a laptop.
#
#   scripts/run-local-suite.sh                       # defaults below
#   REPETITIONS=3 AGENTS="bonsai-2-27b-local@1.0" scripts/run-local-suite.sh
#
# What it does, per agent:
#   1. checks Docker and Ollama are up (stops early, with a reason, if not);
#   2. creates and launches an experiment over every enabled task of BENCHMARK;
#   3. runs it to completion in this process (each run also records host
#      memory/swap, so swapping shows up in the evidence);
#   4. unloads the model from Ollama before the next agent, so only one model
#      is ever in memory.
# The Mac is kept awake with `caffeinate` while it runs. Free: local models only.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO/backend"
CLI=("$BACKEND/.venv/bin/python" -m agentbench.cli.main)

AGENTS="${AGENTS:-bonsai-2-27b-local@1.0 ornith-1.5-9b-local@1.1}"
REPETITIONS="${REPETITIONS:-5}"
BENCHMARK="${BENCHMARK:-textkit}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
MAX_SECONDS_PER_AGENT="${MAX_SECONDS_PER_AGENT:-43200}"   # 12 h safety cap

STAMP="$(date +%Y%m%d-%H%M)"
LOG_DIR="$REPO/data/logs"
LOG="$LOG_DIR/local-suite-$STAMP.log"
mkdir -p "$LOG_DIR"

log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

# Ollama model name an agent uses, read from benchmarks/agents/live.json.
model_of() {
  "$BACKEND/.venv/bin/python" - "$1" "$REPO/benchmarks/agents/live.json" <<'PY'
import json, sys
name, _, version = sys.argv[1].partition("@")
for agent in json.load(open(sys.argv[2]))["agents"]:
    if agent["name"] == name and (not version or agent["version"] == version):
        print(agent["config"]["model"]); break
PY
}

preflight() {
  docker info >/dev/null 2>&1 || { log "Docker is not running. Start Docker Desktop and retry."; exit 1; }
  docker image inspect agentbench/benchmark-python:3.12 >/dev/null 2>&1 || {
    log "Benchmark image missing; building it (needs network once)."
    (cd "$BACKEND" && "${CLI[@]}" images build) | tee -a "$LOG"
  }
  curl -fsS -m 5 "$OLLAMA_URL/api/version" >/dev/null || {
    log "Ollama is not reachable at $OLLAMA_URL. Run 'brew services start ollama' and retry."; exit 1; }
}

run_agent() {
  local agent="$1" model experiment_id
  model="$(model_of "$agent")"
  [ -n "$model" ] || { log "Unknown agent '$agent' (not in benchmarks/agents/live.json); skipping."; return; }

  log "== $agent ($model): $REPETITIONS repetition(s) over benchmark:$BENCHMARK"
  experiment_id="$(cd "$BACKEND" && "${CLI[@]}" experiment create \
      --name "overnight-${agent%@*}-r$REPETITIONS-$STAMP" \
      --agent "$agent" --tasks "benchmark:$BENCHMARK" --repetitions "$REPETITIONS" \
    | "$BACKEND/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["experiment_id"])')"
  log "experiment $experiment_id created and queued"

  (cd "$BACKEND" && "${CLI[@]}" experiment run "$experiment_id" --max-seconds "$MAX_SECONDS_PER_AGENT") \
    >>"$LOG" 2>&1 || log "experiment run exited non-zero (see log); continuing"

  (cd "$BACKEND" && "${CLI[@]}" experiment report "$experiment_id") >>"$LOG" 2>&1 || true
  ollama stop "$model" >/dev/null 2>&1 || true
  log "done: $experiment_id — open http://localhost:5173/experiments/$experiment_id"
  echo "$experiment_id" >>"$LOG_DIR/local-suite-$STAMP.ids"
}

main() {
  log "Agent Labs local suite — agents: $AGENTS — log: $LOG"
  preflight
  for agent in $AGENTS; do
    preflight
    run_agent "$agent"
  done
  log "All agents finished. Experiment ids: $LOG_DIR/local-suite-$STAMP.ids"
  log "Compare them at http://localhost:5173/compare?ids=$(paste -sd, "$LOG_DIR/local-suite-$STAMP.ids")"
}

# Keep the Mac awake for the whole run when caffeinate is available.
if command -v caffeinate >/dev/null 2>&1 && [ -z "${AGENT_LABS_CAFFEINATED:-}" ]; then
  AGENT_LABS_CAFFEINATED=1 exec caffeinate -dims "$0" "$@"
fi
main "$@"
