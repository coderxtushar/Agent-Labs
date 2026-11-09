/**
 * Types for every API response this app consumes.
 *
 * Mirrors `backend/agentbench/api/schemas.py` and the metrics payloads in
 * `backend/agentbench/metrics/`. Hand-written rather than generated so the
 * honesty-critical shapes (`Rate`, `RunOutcome`) carry their meaning in the
 * type system: a `Rate` cannot be rendered without its denominator because the
 * denominator is not optional.
 */

export interface Pagination {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface Page<T> {
  items: T[];
  pagination: Pagination;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    fields?: { location: string; message: string; type: string }[];
    errors?: { location: string; message: string; type: string }[];
  };
}

/** A proportion that always carries what it was computed over. */
export interface Rate {
  value: number | null;
  percent: number | null;
  numerator: number;
  denominator: number;
  ci_low: number | null;
  ci_high: number | null;
  ci_method: string | null;
  small_sample: boolean;
  interpretation_warning: string | null;
}

export type RunOutcome =
  | "solved"
  | "unsolved"
  | "timeout"
  | "agent_error"
  | "infra_error"
  | "invalid_task"
  | "grading_error"
  | "cancelled";

export type RunState =
  | "pending"
  | "claimed"
  | "preparing"
  | "agent_running"
  | "collecting"
  | "grading"
  | "finalizing"
  | "completed"
  | "cancelled";

export type ExperimentState =
  | "draft"
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "cancelled";

export type ProvenanceValue =
  | "simulated"
  | "offline_adapter_test"
  | "live_model"
  | "external_agent";

/** Why a result is or is not a model benchmark number. */
export interface Provenance {
  value: ProvenanceValue;
  label: string;
  caveat: string;
  is_model_result: boolean;
}

export type ValidationStatus = "unvalidated" | "validating" | "valid" | "invalid" | "error";

export type LayerStatus = "passed" | "failed" | "skipped" | "errored";

// --------------------------------------------------------------------------- //
// system
// --------------------------------------------------------------------------- //
export interface Health {
  status: string;
  version: string;
  sandbox: { provider: string; available: boolean; detail: string; isolation: string };
  database: string;
}

export interface SystemStats {
  tasks: number;
  agents: number;
  experiments: number;
  runs: number;
  is_empty: boolean;
  needs_benchmark: boolean;
  needs_agent: boolean;
  has_results: boolean;
}

export interface SystemSettings {
  env: string;
  api: { host: string; port: number; auth_required: boolean; cors_origins: string[] };
  execution: {
    sandbox_provider: string;
    local_unsafe_sandbox_allowed: boolean;
    default_image: string;
    max_concurrent_runs: number;
    infra_retry_limit: number;
    run_lease_seconds: number;
    worker_in_api_process: boolean;
  };
  limits: {
    max_agent_timeout_s: number;
    max_setup_timeout_s: number;
    max_test_timeout_s: number;
    max_memory_mb: number;
    max_cpus: number;
  };
  retention: {
    event_payload_max_chars: number;
    log_tail_max_chars: number;
    event_retention_days: number;
  };
  credentials_present: Record<string, boolean>;
  versions: { platform: string; task_schema: string; grader_implementation: string };
}

export interface AdapterCapability {
  key: string;
  display_name: string;
  description: string;
  is_simulated: boolean;
  requires_network: boolean;
  requires_credentials: string[];
  reports_token_usage: boolean;
  reports_cost: boolean;
  supports_cancellation: boolean;
  streams_events: boolean;
  contract_version: string;
  config_schema: Record<string, unknown>;
}

export interface SystemCapabilities {
  adapters: AdapterCapability[];
  sandbox_providers: {
    name: string;
    available: boolean;
    detail: string;
    isolation: string;
  }[];
  default_image_present: boolean;
  vocabularies: {
    run_states: RunState[];
    run_outcomes: {
      value: RunOutcome;
      counts_toward_success_rate: boolean;
      attributable_to_agent: boolean;
    }[];
    failure_categories: string[];
  };
}

// --------------------------------------------------------------------------- //
// tasks
// --------------------------------------------------------------------------- //
export interface TaskSummary {
  image: string | null;
  repo: Record<string, unknown> | null;
  fail_to_pass_count: number;
  pass_to_pass_count: number;
  hidden_acceptance_files: string[];
  forbidden_paths: string[];
  limits: Record<string, number | string> | null;
  hint_count: number;
  acceptance_criteria: string[];
  problem_statement: string;
  context_files: string[];
}

export interface ValidationCheck {
  name: string;
  ok: boolean;
  detail: string;
  data: Record<string, unknown>;
}

export interface ValidationReport {
  task_key: string;
  status: ValidationStatus;
  ok: boolean;
  checks: ValidationCheck[];
  warnings: string[];
  validated_at: string;
  sandbox_provider: string | null;
  failed_checks: string[];
}

export interface Task {
  id: string;
  task_id: string;
  version: string;
  key: string;
  title: string;
  description: string;
  category: string;
  difficulty: string;
  tags: string[];
  benchmark_id: string;
  benchmark_version: string;
  validation_status: ValidationStatus;
  validated_at: string | null;
  validation_report: ValidationReport | null;
  enabled: boolean;
  created_at: string | null;
  summary: TaskSummary;
  spec?: Record<string, unknown>;
}

export interface BenchmarkSummary {
  benchmark_id: string;
  benchmark_version: string;
  task_count: number;
  validated_count: number;
}

export interface ImportReport {
  benchmark_id: string;
  benchmark_version: string;
  created: string[];
  updated: string[];
  unchanged: string[];
  conflicts: { task_key: string; reason: string }[];
  ok: boolean;
}

export interface SpecValidationResult {
  valid: boolean;
  errors: { location?: string; message: string; type?: string }[];
  tasks?: string[];
  checks: ValidationCheck[];
  note?: string;
}

// --------------------------------------------------------------------------- //
// agents
// --------------------------------------------------------------------------- //
export interface Readiness {
  ready: boolean;
  detail: string;
  missing: string[];
}

export interface Agent {
  id: string;
  name: string;
  version: string;
  label: string;
  description: string;
  adapter: string;
  provider: string | null;
  model: string | null;
  prompt_version: string;
  tools_version: string;
  config: Record<string, unknown>;
  sampling: Record<string, unknown>;
  is_simulated: boolean;
  enabled: boolean;
  capabilities: AdapterCapability;
  readiness: Readiness;
  created_at: string | null;
}

// --------------------------------------------------------------------------- //
// experiments
// --------------------------------------------------------------------------- //
export interface Progress {
  total: number;
  pending: number;
  active: number;
  completed: number;
  cancelled: number;
  finished: number;
  percent: number;
  is_complete: boolean;
  skipped_live_lease: number;
  by_outcome: Partial<Record<RunOutcome, number>>;
  note?: string;
}

export interface Experiment {
  id: string;
  name: string;
  description: string;
  state: ExperimentState;
  benchmark_id: string;
  benchmark_version: string;
  agent: {
    config_id: string;
    label: string;
    adapter: string;
    model: string | null;
    provider: string | null;
    prompt_version: string;
    tools_version: string;
    is_simulated: boolean;
    readiness_at_launch: Readiness | null;
  };
  grading_version: string;
  repetitions: number;
  concurrency: number;
  seed: number | null;
  include_hints: boolean;
  limits_override: Record<string, unknown>;
  task_keys: string[];
  baseline_experiment_id: string | null;
  cancel_requested: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  config_snapshot: Record<string, unknown>;
  provenance: Provenance;
  progress?: Progress;
}

export interface TaskMetrics {
  task_key: string;
  task_id: string;
  title: string;
  category: string;
  difficulty: string;
  attempts: number;
  solved: number;
  unsolved: number;
  timeout: number;
  agent_error: number;
  infra_error: number;
  invalid_task: number;
  grading_error: number;
  cancelled: number;
  regressions: number;
  mean_duration_s: number | null;
  pass_at_1: number | null;
  pass_at_k: Record<string, number>;
  success_rate_attributable: Rate;
  success_rate_graded: Rate;
}

export interface CategoryStats {
  tasks: number;
  attempts: number;
  solved: number;
  success_rate_attributable: Rate;
  success_rate_graded: Rate;
  regressions: number;
}

export interface ExperimentMetrics {
  experiment_id: string;
  name: string;
  state: ExperimentState;
  agent: { label: string; adapter: string; model: string | null; is_simulated: boolean };
  benchmark: string;
  grading_version: string;
  repetitions: number;
  computed_at: string;
  provenance: Provenance;
  completeness: {
    total_runs: number;
    finished_runs: number;
    pending_runs: number;
    active_runs: number;
    run_completeness: number | null;
    is_complete: boolean;
  };
  outcomes: Record<RunOutcome, number>;
  success_rate_attributable: Rate;
  success_rate_graded: Rate;
  pass_at_1: Rate;
  pass_at_k: { values: Record<string, number>; estimator: string | null; note: string };
  reliability: {
    regression_count: number;
    regression_rate: Rate;
    timeout_rate: Rate;
    infra_failure_rate: Rate;
    infra_retry_total: number;
    excluded_from_rates: number;
  };
  runtime: {
    total_s: number;
    mean_s: number | null;
    median_s: number | null;
    p90_s: number | null;
  };
  usage: {
    input_tokens: number | null;
    output_tokens: number | null;
    cost_usd_total: number | null;
    cost_sources: Record<string, number>;
    runs_without_reported_usage: number;
    note: string;
  };
  by_category: Record<string, CategoryStats>;
  by_difficulty: Record<string, CategoryStats>;
  failure_categories: Record<string, number>;
  tasks: TaskMetrics[];
  warnings: string[];
}

// --------------------------------------------------------------------------- //
// runs
// --------------------------------------------------------------------------- //
export interface PatchStats {
  files_changed: number;
  insertions: number;
  deletions: number;
  binary_files: string[];
  paths: { path: string; change: string; "+": number; "-": number }[];
  diff_sha256: string | null;
  truncated: boolean;
}

export interface Usage {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
  requests: number;
  reported_by_provider: boolean;
  steps?: number;
  agent_status?: string;
}

export interface GradingLayer {
  name: string;
  status: LayerStatus;
  required: boolean;
  summary: string;
  exit_code: number | null;
  duration_s: number;
  evidence: Record<string, unknown>;
}

export interface Grading {
  grader_version: string;
  passed: boolean | null;
  score: number | null;
  tests: { passed: number; failed: number; errored: number; skipped: number };
  layers: GradingLayer[];
  evidence: Record<string, unknown>;
  judge: Record<string, unknown> | null;
  judge_note: string;
  created_at: string | null;
}

export interface FailureAnnotation {
  id: string;
  run_id: string;
  category: string;
  source: "rule" | "model" | "human";
  model_generated: boolean;
  confidence: number | null;
  rationale: string;
  evidence_refs: Record<string, unknown>;
  author: string | null;
  supersedes_id: string | null;
  superseded: boolean;
  created_at: string | null;
}

export interface Run {
  id: string;
  experiment_id: string;
  task_key: string;
  task_id: string;
  task_version: string;
  task_title: string | null;
  task_category: string | null;
  task_difficulty: string | null;
  task_validation_status: ValidationStatus;
  attempt_index: number;
  state: RunState;
  is_terminal: boolean;
  is_active: boolean;
  outcome: RunOutcome | null;
  counts_toward_success_rate: boolean | null;
  attributable_to_agent: boolean | null;
  termination_reason: string | null;
  error_message: string | null;
  infra_attempts: number;
  seed: number | null;
  limits: Record<string, number | string>;
  patch_stats: PatchStats | null;
  has_patch: boolean;
  usage: Usage | null;
  cost_usd: number | null;
  cost_source: string | null;
  timings: Record<string, number>;
  sandbox_provider: string | null;
  agent: { label: string; adapter: string; model: string | null; is_simulated: boolean };
  claimed_by: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunDetail extends Run {
  grading: Grading | null;
  failure: FailureAnnotation | null;
  failure_history: FailureAnnotation[];
  attempt_history: {
    attempt_no: number;
    outcome: string;
    reason: string;
    detail: Record<string, unknown>;
    recorded_at: string | null;
  }[];
  event_count: number;
  task_snapshot: Record<string, unknown>;
  agent_snapshot: Record<string, unknown>;
}

export interface RunEvent {
  seq: number;
  ts: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  truncated: boolean;
}

export interface EventPage {
  run_id: string;
  events: RunEvent[];
  total: number;
  last_seq: number;
  has_more: boolean;
  run_state: RunState;
  is_terminal: boolean;
  ordering_note: string;
}

// --------------------------------------------------------------------------- //
// metrics
// --------------------------------------------------------------------------- //
export interface Overview {
  experiments: {
    total: number;
    by_state: Record<string, number>;
    recent: {
      id: string;
      name: string;
      state: ExperimentState;
      benchmark: string;
      agent: string;
      is_simulated: boolean;
      created_at: string | null;
    }[];
  };
  runs: {
    total: number;
    by_state: Record<string, number>;
    by_outcome: Partial<Record<RunOutcome, number>>;
    active: number;
    overall_success_rate_attributable: Rate;
  };
  failure_categories: Record<string, number>;
  computed_at: string;
}

export interface TaskDelta {
  task_key: string;
  title: string;
  category: string;
  baseline: { solved: number; attempts: number; rate: number | null };
  candidate: { solved: number; attempts: number; rate: number | null };
  movement:
    | "newly_solved"
    | "newly_broken"
    | "improved"
    | "degraded"
    | "unchanged"
    | "not_comparable";
  delta: number | null;
}

export interface ComparisonArm {
  experiment_id: string;
  name: string;
  agent: string;
  model: string | null;
  is_simulated: boolean;
  provenance: Provenance;
  success_rate_attributable: Rate;
  outcomes: Record<RunOutcome, number>;
  runtime: ExperimentMetrics["runtime"];
  usage: ExperimentMetrics["usage"];
  failure_categories: Record<string, number>;
}

export interface Comparison {
  comparable: boolean;
  blockers: string[];
  caveats: string[];
  baseline: ComparisonArm;
  candidate: ComparisonArm;
  delta: {
    success_rate: number | null;
    solved: number;
    regressions: number;
    timeouts: number;
    agent_errors: number;
    runtime_total_s: number;
    cost_usd: number | null;
  };
  movement: Record<string, string[]>;
  task_deltas: TaskDelta[];
  by_category: Record<
    string,
    { baseline: CategoryStats | null; candidate: CategoryStats | null; delta: number | null }
  >;
  statistical_note: {
    test: string | null;
    reason?: string;
    note?: string;
    z?: number;
    p_value?: number;
    below_alpha?: boolean;
    claims_significance: boolean;
  };
  summary: string;
}

export interface FailureRow {
  annotation: FailureAnnotation;
  run: Run;
  grading_summary: {
    passed: boolean | null;
    score: number | null;
    blocking_layers: string[];
    errored_layers: string[];
    layers: { name: string; status: LayerStatus; summary: string }[];
    tests: { passed: number; failed: number; errored: number };
  } | null;
}

export interface FailurePage extends Page<FailureRow> {
  distribution: Record<string, number>;
}
