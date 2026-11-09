import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { downloadUrl } from "../api/client";
import {
  useExperiment,
  useExperimentAction,
  useExperimentMetrics,
  useExperimentRuns,
} from "../api/hooks";
import type { ExperimentMetrics, RunOutcome } from "../api/types";
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
  StateBadge,
  WarningList,
  formatDuration,
  formatTime,
} from "../components/domain";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  JsonBlock,
  KeyValue,
  LinkButton,
  LoadingRows,
  LoadingTiles,
  Mono,
  ProgressBar,
  StatTile,
  Table,
  Tabs,
  Td,
  Th,
} from "../components/ui";

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
          <Td colSpan={3} className="surface-2 muted text-2xs font-semibold uppercase tracking-wide">
            Excluded from success rates
          </Td>
        </tr>
        {EXCLUDED_OUTCOMES.map(row)}
      </tbody>
    </Table>
  );
}

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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold">{data.name}</h1>
            <Badge tone={data.state === "completed" ? "accent" : "info"}>{data.state}</Badge>
            <ProvenanceBadge provenance={data.provenance} />
          </div>
          <p className="muted mt-1 text-2xs">
            <Mono>{data.id}</Mono> · created {formatTime(data.created_at)}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
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
          <LinkButton href={downloadUrl(`/experiments/${data.id}/report`, { format: "markdown" })}>
            Report .md
          </LinkButton>
          <LinkButton href={downloadUrl(`/experiments/${data.id}/report`, { format: "csv" })}>
            .csv
          </LinkButton>
          <LinkButton href={downloadUrl(`/experiments/${data.id}/report`, { format: "json" })}>
            .json
          </LinkButton>
        </div>
      </div>

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
            <div className="space-y-4">
              <WarningList warnings={metrics.data.warnings} />

              <div className="grid grid-cols-1 gap-3 lg:grid-cols-4">
                <div className="surface hairline rounded-lg border p-4">
                  <RateDisplay
                    rate={metrics.data.success_rate_attributable}
                    label="Success rate (agent-attributable)"
                    size="lg"
                  />
                </div>
                <div className="surface hairline rounded-lg border p-4">
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
                <Card title="Outcomes by attribution">
                  <OutcomeTable metrics={metrics.data} />
                </Card>

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
                            <OptionalNumber value={metrics.data.usage.input_tokens} />,
                          ],
                          [
                            "Output tokens",
                            <OptionalNumber value={metrics.data.usage.output_tokens} />,
                          ],
                          [
                            "Cost (USD)",
                            <OptionalNumber value={metrics.data.usage.cost_usd_total} />,
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

              <Card title="Per-task results">
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
                      <tr key={task.task_key} className="hover:surface-2">
                        <Td>
                          <Mono className="font-semibold">{task.task_key}</Mono>
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
          subtitle={runs.data ? `${runs.data.pagination.total} shown` : undefined}
          actions={
            <select
              className="surface-2 hairline rounded-md border px-2 py-1 text-xs"
              value={outcomeFilter ?? ""}
              onChange={(event) => setOutcomeFilter(event.target.value || undefined)}
            >
              <option value="">All outcomes</option>
              {[...COUNTED_OUTCOMES, ...EXCLUDED_OUTCOMES].map((outcome) => (
                <option key={outcome} value={outcome}>
                  {OUTCOME_META[outcome].label}
                </option>
              ))}
            </select>
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
                  <tr key={run.id} className="hover:surface-2">
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
          <Card title="Experiment configuration">
            <div className="p-4">
              <KeyValue
                rows={[
                  ["Benchmark", <Mono>{`${data.benchmark_id}@${data.benchmark_version}`}</Mono>],
                  ["Agent", <Mono>{data.agent.label}</Mono>],
                  ["Adapter", <Mono>{data.agent.adapter}</Mono>],
                  ["Model", <Mono>{data.agent.model ?? "—"}</Mono>],
                  ["Prompt version", data.agent.prompt_version],
                  ["Tools version", data.agent.tools_version],
                  ["Grading version", data.grading_version],
                  ["Repetitions", data.repetitions],
                  ["Concurrency", data.concurrency],
                  ["Seed", data.seed ?? "none"],
                  ["Hints given to agent", String(data.include_hints)],
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

          <Card
            title="Reproduction snapshot"
            subtitle="Frozen at launch. Later edits to the agent cannot change what this experiment measured."
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
