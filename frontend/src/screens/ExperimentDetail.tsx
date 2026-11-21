import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { downloadUrl } from "../api/client";
import {
  useExperiment,
  useExperimentAction,
  useExperimentMetrics,
  useExperimentRuns,
} from "../api/hooks";
import type { Experiment, ExperimentMetrics, RunOutcome } from "../api/types";
import {
  COUNTED_OUTCOMES,
  EXCLUDED_OUTCOMES,
  OUTCOME_META,
  OptionalNumber,
  OutcomeBadge,
  PartialBanner,
  ProvenanceBadge,
  ProvenanceNotice,
  RateDisplay,
  SimulatedBadge,
  StateBadge,
  WarningList,
  formatDuration,
  formatTime,
} from "../components/domain";
import {
  Badge,
  Button,
  Card,
  CodeBlock,
  ErrorState,
  JsonBlock,
  KeyValue,
  LinkButton,
  LoadingRows,
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  ProgressBar,
  Select,
  StatTile,
  Table,
  Tabs,
  Td,
  Th,
  cx,
} from "../components/ui";

const linkButtonClass =
  "surface-2 hairline inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition hover:border-[rgb(var(--border-strong))]";

// --------------------------------------------------------------------------- //
// local helpers

function shellQuote(value: string): string {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(value) ? value : `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * The real `agentbench experiment create` invocation (backend/agentbench/cli/main.py)
 * that re-creates this experiment's design. Only options the CLI actually accepts.
 */
function rerunCommand(data: Experiment): string[] {
  const parts = [
    "agentbench experiment create",
    `--name ${shellQuote(`${data.name} (rerun)`)}`,
    `--agent ${shellQuote(data.agent.label)}`,
    `--tasks ${shellQuote(data.task_keys.join(","))}`,
    `--repetitions ${data.repetitions}`,
    `--concurrency ${data.concurrency}`,
  ];
  if (data.seed !== null) parts.push(`--seed ${data.seed}`);
  if (data.include_hints) parts.push("--include-hints");
  return parts;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
      setStatus("copied");
    } catch {
      setStatus("failed");
    }
    window.setTimeout(() => setStatus("idle"), 2000);
  };
  return (
    <Button variant="ghost" onClick={() => void copy()} title={label}>
      <span aria-live="polite" className={cx(status === "copied" && "text-good", status === "failed" && "text-warn")}>
        {status === "copied" ? "Copied" : status === "failed" ? "Copy unavailable — select the text" : "Copy"}
      </span>
      <span className="sr-only">{label}</span>
    </Button>
  );
}

function taskHref(taskKey: string): string {
  return `/benchmarks/tasks/${encodeURIComponent(taskKey)}`;
}

// --------------------------------------------------------------------------- //
function OutcomeTable({ metrics }: { metrics: ExperimentMetrics }) {
  const row = (outcome: RunOutcome) => {
    const meta = OUTCOME_META[outcome];
    return (
      <tr key={outcome}>
        <Td>
          <OutcomeBadge outcome={outcome} />
        </Td>
        <Td className="tnum text-right font-medium">{metrics.outcomes[outcome]}</Td>
        <Td className="muted text-2xs">
          {meta.counted ? "counts toward success rate" : "excluded — not attributable to the agent"}
        </Td>
      </tr>
    );
  };

  return (
    <Table>
      <thead>
        <tr>
          <Th>Outcome</Th>
          <Th className="text-right">Runs</Th>
          <Th>Attribution</Th>
        </tr>
      </thead>
      <tbody>
        {COUNTED_OUTCOMES.map(row)}
        <tr>
          <Td colSpan={3} className="surface-2 subtle text-2xs font-medium uppercase tracking-[0.06em]">
            Excluded from success rates
          </Td>
        </tr>
        {EXCLUDED_OUTCOMES.map(row)}
      </tbody>
    </Table>
  );
}

function FailureSummary({ metrics, experimentId }: { metrics: ExperimentMetrics; experimentId: string }) {
  const entries = Object.entries(metrics.failure_categories).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  return (
    <Card
      title="Failure analysis"
      subtitle="Classified failures for this experiment; human annotations supersede automatic ones"
      actions={
        <Link to={`/failures?experiment_id=${encodeURIComponent(experimentId)}`} className={linkButtonClass}>
          Open failure analysis
        </Link>
      }
    >
      {entries.length === 0 ? (
        <p className="muted p-4 text-xs">No classified failures recorded for this experiment.</p>
      ) : (
        <ul className="space-y-2 p-4">
          {entries.map(([category, count]) => (
            <li key={category} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1">
              <span className="truncate text-xs">{category}</span>
              <span className="tnum muted text-2xs">
                {count}/{total}
              </span>
              <div className="col-span-2">
                <ProgressBar percent={(count / total) * 100} muted />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// --------------------------------------------------------------------------- //
export default function ExperimentDetail() {
  const { experimentId } = useParams<{ experimentId: string }>();
  const experiment = useExperiment(experimentId);
  const metrics = useExperimentMetrics(experimentId);
  const [outcomeFilter, setOutcomeFilter] = useState<string | undefined>(undefined);
  const runs = useExperimentRuns(experimentId, outcomeFilter);
  const [tab, setTab] = useState("results");

  const launch = useExperimentAction("launch");
  const cancel = useExperimentAction("cancel");
  const resume = useExperimentAction("resume");

  if (experiment.isError) {
    return <ErrorState error={experiment.error} onRetry={() => void experiment.refetch()} />;
  }
  if (experiment.isLoading || !experiment.data) return <LoadingTiles count={4} />;

  const data = experiment.data;
  const progress = data.progress;
  const actionError = launch.error ?? cancel.error ?? resume.error;
  const resumeNote = resume.data?.skipped_live_lease ? resume.data.note : null;
  const command = rerunCommand(data);
  const hasLimitsOverride = Object.keys(data.limits_override ?? {}).length > 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={
          <Link to="/experiments" className="hover:text-[rgb(var(--text))]">
            Experiments
          </Link>
        }
        title={data.name}
        description={data.description || undefined}
        meta={
          <>
            <Badge tone={data.state === "completed" ? "accent" : data.state === "cancelled" ? "neutral" : "info"}>
              {data.state}
            </Badge>
            <ProvenanceBadge provenance={data.provenance} />
            <Mono className="subtle">{data.id}</Mono>
            <span className="subtle text-2xs">created {formatTime(data.created_at)}</span>
          </>
        }
        actions={
          <>
            <Button
              onClick={() => launch.mutate({ id: data.id })}
              disabled={launch.isPending || data.state === "cancelled"}
            >
              Launch
            </Button>
            <Button
              variant="danger"
              onClick={() => cancel.mutate({ id: data.id })}
              disabled={cancel.isPending || data.state === "cancelled"}
            >
              Cancel
            </Button>
            <Button
              onClick={() => resume.mutate({ id: data.id })}
              disabled={resume.isPending || data.state === "cancelled"}
              title="Re-queue runs abandoned by a crashed worker"
            >
              Resume
            </Button>
            <Button
              variant="ghost"
              onClick={() => resume.mutate({ id: data.id, force: true })}
              disabled={resume.isPending || data.state === "cancelled"}
              title="Also reset runs whose worker lease is still live — only when you know the worker is gone"
            >
              Force resume
            </Button>
            <Link to={`/compare?ids=${encodeURIComponent(data.id)}`} className={linkButtonClass}>
              Compare with…
            </Link>
            <LinkButton href={downloadUrl(`/experiments/${data.id}/report`, { format: "markdown" })}>
              Report .md
            </LinkButton>
            <LinkButton href={downloadUrl(`/experiments/${data.id}/report`, { format: "csv" })}>
              .csv
            </LinkButton>
            <LinkButton href={downloadUrl(`/experiments/${data.id}/report`, { format: "json" })}>
              .json
            </LinkButton>
          </>
        }
      />

      <ProvenanceNotice provenance={data.provenance} />

      {actionError && <ErrorState error={actionError} />}
      {resumeNote && (
        <div className="rounded-lg border border-sky-500/30 bg-sky-500/5 p-3 text-xs text-sky-200/90">
          {resumeNote}
        </div>
      )}
      {progress && (
        <PartialBanner pending={progress.pending} active={progress.active} total={progress.total} />
      )}

      <Tabs
        tabs={[
          { id: "results", label: "Results" },
          { id: "runs", label: "Runs", count: runs.data?.pagination.total },
          { id: "config", label: "Configuration" },
          { id: "repro", label: "Reproducibility" },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === "results" && (
        <>
          {metrics.isError && (
            <ErrorState error={metrics.error} onRetry={() => void metrics.refetch()} />
          )}
          {metrics.isLoading && <LoadingTiles count={4} />}
          {metrics.data && (
            <div className="space-y-6">
              <WarningList warnings={metrics.data.warnings} />

              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <div className="surface elevated hairline rounded-xl border p-4">
                  <RateDisplay
                    rate={metrics.data.success_rate_attributable}
                    label="Success rate (agent-attributable)"
                    size="lg"
                  />
                </div>
                <div className="surface elevated hairline rounded-xl border p-4">
                  <RateDisplay
                    rate={metrics.data.success_rate_graded}
                    label="Success rate (graded runs only)"
                    size="lg"
                  />
                </div>
                <StatTile
                  label="Run completeness"
                  value={`${metrics.data.completeness.finished_runs}/${metrics.data.completeness.total_runs}`}
                  hint={
                    metrics.data.completeness.is_complete
                      ? "complete"
                      : `${metrics.data.completeness.pending_runs} pending · ${metrics.data.completeness.active_runs} in flight`
                  }
                  tone={metrics.data.completeness.is_complete ? undefined : "warn"}
                />
                <StatTile
                  label="Regressions caught"
                  value={metrics.data.reliability.regression_count}
                  hint="pass_to_pass tests broken by the patch"
                  tone={metrics.data.reliability.regression_count > 0 ? "bad" : undefined}
                />
              </div>

              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <div className="space-y-4">
                  <Card title="Outcomes by attribution">
                    <OutcomeTable metrics={metrics.data} />
                  </Card>
                  <FailureSummary metrics={metrics.data} experimentId={data.id} />
                </div>

                <div className="space-y-4">
                  <Card title="Reliability">
                    <div className="space-y-3 p-4">
                      <RateDisplay rate={metrics.data.reliability.timeout_rate} label="Timeout rate" />
                      <RateDisplay
                        rate={metrics.data.reliability.infra_failure_rate}
                        label="Infrastructure failure rate"
                      />
                      <RateDisplay
                        rate={metrics.data.reliability.regression_rate}
                        label="Regression rate"
                      />
                      <KeyValue
                        rows={[
                          ["Infra retries", metrics.data.reliability.infra_retry_total],
                          ["Excluded from rates", metrics.data.reliability.excluded_from_rates],
                        ]}
                      />
                    </div>
                  </Card>

                  <Card title="pass@k" subtitle={metrics.data.pass_at_k.estimator ?? undefined}>
                    <div className="p-4">
                      {Object.keys(metrics.data.pass_at_k.values).length === 0 ? (
                        <p className="muted text-xs">{metrics.data.pass_at_k.note}</p>
                      ) : (
                        <KeyValue
                          rows={Object.entries(metrics.data.pass_at_k.values).map(([k, value]) => [
                            k,
                            value.toFixed(4),
                          ])}
                        />
                      )}
                    </div>
                  </Card>

                  <Card title="Runtime and cost">
                    <div className="p-4">
                      <KeyValue
                        rows={[
                          ["Total wall clock", formatDuration(metrics.data.runtime.total_s)],
                          ["Mean per run", formatDuration(metrics.data.runtime.mean_s)],
                          ["Median", formatDuration(metrics.data.runtime.median_s)],
                          ["p90", formatDuration(metrics.data.runtime.p90_s)],
                          [
                            "Input tokens",
                            <OptionalNumber key="input" value={metrics.data.usage.input_tokens} />,
                          ],
                          [
                            "Output tokens",
                            <OptionalNumber key="output" value={metrics.data.usage.output_tokens} />,
                          ],
                          [
                            "Cost (USD)",
                            <OptionalNumber key="cost" value={metrics.data.usage.cost_usd_total} />,
                          ],
                          [
                            "Cost sources",
                            Object.keys(metrics.data.usage.cost_sources).length > 0
                              ? Object.entries(metrics.data.usage.cost_sources)
                                  .map(([source, count]) => `${source} (${count})`)
                                  .join(", ")
                              : "none reported",
                          ],
                        ]}
                      />
                      <p className="muted mt-2 text-2xs">{metrics.data.usage.note}</p>
                    </div>
                  </Card>
                </div>
              </div>

              <Card title="Per-task results" subtitle="Open a task to see its history across experiments">
                <Table>
                  <thead>
                    <tr>
                      <Th>Task</Th>
                      <Th>Category</Th>
                      <Th className="text-right">Attempts</Th>
                      <Th className="text-right">Solved</Th>
                      <Th>Success rate</Th>
                      <Th className="text-right">Regressions</Th>
                      <Th className="text-right">Mean</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.data.tasks.map((task) => (
                      <tr key={task.task_key} className="row-hover">
                        <Td>
                          <Link to={taskHref(task.task_key)} className="link font-mono text-2xs font-semibold">
                            {task.task_key}
                          </Link>
                          <p className="muted mt-0.5 text-2xs">{task.title}</p>
                        </Td>
                        <Td>
                          <Badge tone="neutral">{task.category}</Badge>
                        </Td>
                        <Td className="tnum text-right">{task.attempts}</Td>
                        <Td className="tnum text-right">{task.solved}</Td>
                        <Td>
                          <RateDisplay
                            rate={task.success_rate_attributable}
                            size="sm"
                            showCi={false}
                          />
                        </Td>
                        <Td className="tnum text-right">{task.regressions}</Td>
                        <Td className="tnum text-right">{formatDuration(task.mean_duration_s)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Card>

              {Object.keys(metrics.data.by_category).length > 0 && (
                <Card title="By category">
                  <Table>
                    <thead>
                      <tr>
                        <Th>Category</Th>
                        <Th className="text-right">Tasks</Th>
                        <Th className="text-right">Attempts</Th>
                        <Th>Success rate</Th>
                        <Th className="text-right">Regressions</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(metrics.data.by_category).map(([category, stats]) => (
                        <tr key={category}>
                          <Td>{category}</Td>
                          <Td className="tnum text-right">{stats.tasks}</Td>
                          <Td className="tnum text-right">{stats.attempts}</Td>
                          <Td>
                            <RateDisplay
                              rate={stats.success_rate_attributable}
                              size="sm"
                              showCi={false}
                            />
                          </Td>
                          <Td className="tnum text-right">{stats.regressions}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </Card>
              )}
            </div>
          )}
        </>
      )}

      {tab === "runs" && (
        <Card
          title="Runs"
          subtitle={
            runs.data
              ? `${runs.data.pagination.total} shown · open a run for its trajectory, logs, patch and grading`
              : undefined
          }
          actions={
            <Select
              label="Filter runs by outcome"
              value={outcomeFilter ?? ""}
              onChange={(value) => setOutcomeFilter(value || undefined)}
              options={[
                { value: "", label: "All outcomes" },
                ...[...COUNTED_OUTCOMES, ...EXCLUDED_OUTCOMES].map((outcome) => ({
                  value: outcome,
                  label: OUTCOME_META[outcome].label,
                })),
              ]}
            />
          }
        >
          {runs.isError && (
            <div className="p-4">
              <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
            </div>
          )}
          {runs.isLoading && <LoadingRows rows={5} label="Loading runs" />}
          {runs.data && runs.data.items.length === 0 && (
            <p className="muted p-4 text-xs">
              {outcomeFilter
                ? "No runs with that outcome. Clear the filter to see all runs."
                : "No runs yet."}
            </p>
          )}
          {runs.data && runs.data.items.length > 0 && (
            <Table>
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>Task</Th>
                  <Th>Attempt</Th>
                  <Th>State</Th>
                  <Th>Outcome</Th>
                  <Th className="text-right">Files</Th>
                  <Th className="text-right">Duration</Th>
                  <Th>Reason</Th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((run) => (
                  <tr key={run.id} className="row-hover">
                    <Td>
                      <Link to={`/runs/${run.id}`} className="accent-text font-mono text-2xs hover:underline">
                        {run.id.slice(0, 10)}
                      </Link>
                    </Td>
                    <Td>
                      <Mono>{run.task_key}</Mono>
                    </Td>
                    <Td className="tnum">{run.attempt_index}</Td>
                    <Td>
                      <StateBadge state={run.state} />
                    </Td>
                    <Td>
                      <OutcomeBadge outcome={run.outcome} />
                    </Td>
                    <Td className="tnum text-right">{run.patch_stats?.files_changed ?? "—"}</Td>
                    <Td className="tnum text-right">{formatDuration(run.timings.total_s)}</Td>
                    <Td className="muted max-w-md truncate text-2xs" title={run.termination_reason ?? ""}>
                      {run.termination_reason ?? "—"}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      )}

      {tab === "config" && (
        <div className="space-y-4">
          <Card title="Experiment">
            <div className="p-4">
              <KeyValue
                rows={[
                  ["Name", data.name],
                  ["ID", <Mono key="id">{data.id}</Mono>],
                  ["State", data.cancel_requested && data.state !== "cancelled" ? `${data.state} (cancel requested)` : data.state],
                  [
                    "Baseline experiment",
                    data.baseline_experiment_id ? (
                      <Link key="baseline" to={`/experiments/${data.baseline_experiment_id}`} className="link font-mono text-2xs">
                        {data.baseline_experiment_id}
                      </Link>
                    ) : (
                      "none"
                    ),
                  ],
                  ["Created", formatTime(data.created_at)],
                  ["Started", formatTime(data.started_at)],
                  ["Finished", formatTime(data.finished_at)],
                ]}
              />
              {progress && (
                <div className="mt-4">
                  <ProgressBar percent={progress.percent} />
                  <p className="tnum muted mt-1 text-2xs">
                    {progress.finished}/{progress.total} finished ({progress.percent}%)
                  </p>
                </div>
              )}
            </div>
          </Card>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="Benchmark">
              <div className="space-y-4 p-4">
                <KeyValue
                  rows={[
                    ["Benchmark", <Mono key="benchmark">{`${data.benchmark_id}@${data.benchmark_version}`}</Mono>],
                    ["Grading version", data.grading_version],
                    ["Tasks", data.task_keys.length],
                    ["Repetitions", data.repetitions],
                    ["Concurrency", data.concurrency],
                    ["Hints given to agent", String(data.include_hints)],
                  ]}
                />
                <div>
                  <p className="eyebrow">Task keys</p>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {data.task_keys.map((key) => (
                      <Link
                        key={key}
                        to={taskHref(key)}
                        className="surface-2 hairline rounded-md border px-1.5 py-0.5 font-mono text-2xs transition-colors hover:border-[rgb(var(--border-strong))]"
                      >
                        {key}
                      </Link>
                    ))}
                  </div>
                </div>
              </div>
            </Card>

            <Card
              title="Agent and model"
              actions={
                <Link to={`/leaderboard/${data.agent.config_id}`} className="link text-2xs">
                  Agent profile
                </Link>
              }
            >
              <div className="space-y-3 p-4">
                {data.agent.is_simulated && <SimulatedBadge />}
                <KeyValue
                  rows={[
                    ["Agent", <Mono key="agent">{data.agent.label}</Mono>],
                    ["Adapter", <Mono key="adapter">{data.agent.adapter}</Mono>],
                    ["Model", <Mono key="model">{data.agent.model ?? "—"}</Mono>],
                    ["Provider", data.agent.provider ?? "—"],
                    ["Prompt version", data.agent.prompt_version],
                    ["Tools version", data.agent.tools_version],
                    [
                      "Readiness at launch",
                      data.agent.readiness_at_launch ? (
                        <span key="readiness">
                          <Badge tone={data.agent.readiness_at_launch.ready ? "good" : "warn"}>
                            {data.agent.readiness_at_launch.ready ? "ready" : "not ready"}
                          </Badge>{" "}
                          <span className="muted">{data.agent.readiness_at_launch.detail}</span>
                        </span>
                      ) : (
                        "not recorded"
                      ),
                    ],
                  ]}
                />
              </div>
            </Card>
          </div>
        </div>
      )}

      {tab === "repro" && (
        <div className="space-y-4">
          <Card
            title="Reproducibility"
            subtitle="Everything that determined what this experiment measured"
          >
            <div className="p-4">
              <KeyValue
                rows={[
                  ["Seed", data.seed ?? "none"],
                  ["Repetitions", data.repetitions],
                  ["Concurrency", data.concurrency],
                  ["Include hints", String(data.include_hints)],
                  ["Grading version", data.grading_version],
                  ["Benchmark version", <Mono key="bv">{`${data.benchmark_id}@${data.benchmark_version}`}</Mono>],
                  ["Agent config", <Mono key="ac">{data.agent.label}</Mono>],
                  ["Prompt version", data.agent.prompt_version],
                  ["Tools version", data.agent.tools_version],
                  [
                    "Limits override",
                    hasLimitsOverride ? (
                      <Mono key="limits">{JSON.stringify(data.limits_override)}</Mono>
                    ) : (
                      "none (task limits apply)"
                    ),
                  ],
                ]}
              />
            </div>
          </Card>

          <Card
            title="Re-create from the CLI"
            subtitle="Run from backend/ with the virtualenv active. Creates and queues a new experiment with the same design."
            actions={<CopyButton text={command.join(" ")} label="Copy CLI command" />}
          >
            <div className="space-y-3 p-4">
              <CodeBlock>{command.join(" \\\n  ")}</CodeBlock>
              <Note>
                A rerun is a new sample, not a replay: model outputs can differ even with the same seed.
                {hasLimitsOverride && " limits_override cannot be set from the CLI, so the rerun uses task limits."}
              </Note>
              <p className="muted text-2xs">
                Export this experiment&apos;s report with{" "}
                <Mono>{`agentbench experiment report ${data.id} --format markdown`}</Mono>.
              </p>
            </div>
          </Card>

          <Card
            title="Reproduction snapshot"
            subtitle="Frozen at launch. Later edits to the agent cannot change what this experiment measured."
            actions={<CopyButton text={JSON.stringify(data.config_snapshot, null, 2)} label="Copy config snapshot JSON" />}
          >
            <div className="p-4">
              <JsonBlock value={data.config_snapshot} maxHeight="max-h-[32rem]" />
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
