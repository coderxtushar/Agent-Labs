import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useLeaderboard, useOverview, useRuns, useStats } from "../api/hooks";
import type { ExperimentState, Provenance, Rate, RunOutcome, Tally } from "../api/types";
import { OutcomeStack, RateBar } from "../components/charts";
import {
  EXCLUDED_OUTCOMES,
  OUTCOME_META,
  OutcomeBadge,
  ProvenanceBadge,
  SimulatedBadge,
  StateBadge,
  formatDuration,
  formatRelative,
  formatTime,
} from "../components/domain";
import {
  Badge,
  Card,
  CodeBlock,
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  Table,
  Td,
  Th,
  cx,
  type BadgeTone,
} from "../components/ui";

// --------------------------------------------------------------------------- //
// local helpers
// --------------------------------------------------------------------------- //
const ONBOARDING = `# 1. create the schema, import the bundled benchmark and demo agents
agentbench bootstrap

# 2. prove each task is executable AND falsifiable (builds real containers)
agentbench benchmark validate

# 3. run the bundled benchmark with the known-good reference agent
agentbench experiment create --name demo --agent reference-solution
agentbench experiment run`;

const BUTTON_LINK =
  "surface-2 hairline inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors duration-150 hover:border-[rgb(var(--border-strong))]";
const PRIMARY_LINK =
  "accent-bg inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-opacity duration-150 hover:opacity-90";

const EXPERIMENT_STATE_TONES: Record<ExperimentState, BadgeTone> = {
  draft: "neutral",
  queued: "info",
  running: "info",
  paused: "warn",
  completed: "accent",
  cancelled: "neutral",
};

function percentText(rate: Rate): string {
  if (rate.percent === null) return "—";
  return `${Number.isInteger(rate.percent) ? rate.percent : rate.percent.toFixed(1)}%`;
}

function ciText(rate: Rate): string | null {
  if (rate.denominator === 0 || rate.ci_low === null || rate.ci_high === null) return null;
  return `95% CI ${(rate.ci_low * 100).toFixed(0)}–${(rate.ci_high * 100).toFixed(0)}%`;
}

function count(tally: Tally, outcome: RunOutcome): number {
  return tally.outcomes[outcome] ?? 0;
}

function Kpi({
  label,
  value,
  hint,
  tone,
  scope,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "good" | "bad" | "warn";
  scope?: string;
}) {
  return (
    <div className="surface elevated hairline flex min-w-0 flex-col rounded-xl border p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="eyebrow truncate">{label}</p>
        {scope && <span className="subtle shrink-0 text-2xs">{scope}</span>}
      </div>
      <div
        className={cx(
          "tnum mt-2 min-w-0 text-[26px] font-semibold leading-none tracking-tight",
          tone === "good" && "text-good",
          tone === "bad" && "text-bad",
          tone === "warn" && "text-warn",
        )}
      >
        {value}
      </div>
      {hint && <div className="muted mt-2 space-y-0.5 text-xs">{hint}</div>}
    </div>
  );
}

function ExperimentStateBadge({ state }: { state: ExperimentState }) {
  return <Badge tone={EXPERIMENT_STATE_TONES[state] ?? "neutral"}>{state}</Badge>;
}

// --------------------------------------------------------------------------- //
// sections
// --------------------------------------------------------------------------- //
function ModelPerformance() {
  const board = useLeaderboard({});

  if (board.isError) {
    return (
      <div className="p-4">
        <ErrorState error={board.error} onRetry={() => void board.refetch()} />
      </div>
    );
  }
  if (board.isLoading || !board.data) return <LoadingRows rows={4} label="Loading model results" />;

  const { rows, hidden_non_model_experiments: hidden } = board.data;
  if (rows.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          title="No model results yet"
          description={
            hidden > 0
              ? `${hidden} simulated or offline experiment(s) exist but are not model results, so they are not charted here.`
              : "Run an experiment with a live model agent to see results here."
          }
        />
      </div>
    );
  }

  const shown = rows.slice(0, 8);
  return (
    <div className="space-y-4 p-4">
      <div className="space-y-3">
        {shown.map((row) => (
          <div
            key={`${row.agent_config_id}:${row.provenance.value}`}
            className="grid grid-cols-[1.25rem_minmax(0,1fr)] items-center gap-x-3 gap-y-1 sm:grid-cols-[1.25rem_minmax(0,14rem)_minmax(0,1fr)_7.5rem]"
          >
            <span className="subtle tnum text-2xs">{row.position}</span>
            <div className="min-w-0">
              <Link
                to={`/leaderboard/${row.agent_config_id}`}
                className="link block truncate text-xs font-medium"
              >
                {row.label}
              </Link>
              <Mono className="subtle block truncate">{row.model ?? "model not recorded"}</Mono>
            </div>
            <div className="col-start-2 sm:col-start-auto">
              <RateBar rate={row.success_rate} />
            </div>
            <div className="col-start-2 sm:col-start-auto sm:text-right">
              <p className="tnum text-xs font-medium">
                {percentText(row.success_rate)}
                <span className="muted ml-1 font-normal">
                  ({row.success_rate.numerator}/{row.success_rate.denominator})
                </span>
              </p>
              <p className="subtle tnum text-2xs">
                {ciText(row.success_rate) ?? "no interval"}
                {row.ci_overlaps_leader === true && " · ≈ leader"}
              </p>
            </div>
          </div>
        ))}
      </div>
      <div className="hairline flex flex-wrap items-center justify-between gap-2 border-t pt-3">
        <span className="subtle text-2xs">
          Bars show observed success; whiskers show the 95% interval. n = attributable runs.
        </span>
        <Link to="/leaderboard" className="accent-text text-2xs font-medium">
          Full leaderboard →
        </Link>
      </div>
      <Note>{board.data.ordering_note}</Note>
      {board.data.warnings.map((warning) => (
        <Note key={warning} tone="warn">
          {warning}
        </Note>
      ))}
    </div>
  );
}

function ProvenanceSplit({ rows }: { rows: (Tally & { provenance: Provenance })[] }) {
  if (rows.length === 0) return <p className="muted p-4 text-xs">No finished runs yet.</p>;
  return (
    <div className="space-y-4 p-4">
      <ul className="space-y-4">
        {rows.map((row) => (
          <li key={row.provenance.value} className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <ProvenanceBadge provenance={row.provenance} />
              <span className="muted tnum text-2xs">
                {row.finished_runs} finished · {row.excluded_runs} excluded
              </span>
            </div>
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-3">
              <OutcomeStack outcomes={row.outcomes} />
              <div className="text-right">
                <p className="tnum text-xs font-medium">
                  {percentText(row.success_rate)}
                  <span className="muted ml-1 font-normal">
                    ({row.success_rate.numerator}/{row.success_rate.denominator})
                  </span>
                </p>
                <p className="subtle text-2xs">
                  {row.provenance.is_model_result ? "success · model result" : "pipeline check only"}
                </p>
              </div>
            </div>
          </li>
        ))}
      </ul>
      <Note>
        Headline numbers on this page use live model runs only. Simulated and offline adapter results
        validate the harness and are never counted as model performance.
      </Note>
    </div>
  );
}

function FailureDistribution({ categories }: { categories: Record<string, number> }) {
  const entries = Object.entries(categories).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <p className="muted p-4 text-xs">No classified failures yet.</p>;
  const max = Math.max(...entries.map(([, n]) => n));
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  return (
    <div className="space-y-1 p-3">
      {entries.map(([category, n]) => (
        <Link
          key={category}
          to={`/failures?category=${encodeURIComponent(category)}`}
          className="row-hover group block rounded-md px-2 py-1.5 transition-colors"
        >
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="truncate group-hover:underline">{category.replace(/_/g, " ")}</span>
            <span className="tnum muted shrink-0">
              {n}
              <span className="subtle ml-1">/ {total}</span>
            </span>
          </div>
          <div className="surface-3 mt-1 h-1 w-full overflow-hidden rounded-full">
            <div
              className="h-full rounded-full"
              style={{ width: `${(n / max) * 100}%`, backgroundColor: "rgb(var(--accent) / 0.7)" }}
            />
          </div>
        </Link>
      ))}
    </div>
  );
}

function RecentRuns() {
  const runs = useRuns({ limit: 12 });
  if (runs.isError) {
    return (
      <div className="p-4">
        <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
      </div>
    );
  }
  if (runs.isLoading || !runs.data) return <LoadingRows rows={5} label="Loading runs" />;
  if (runs.data.items.length === 0) return <p className="muted p-4 text-xs">No runs yet.</p>;
  return (
    <Table>
      <thead>
        <tr>
          <Th>Run</Th>
          <Th>Result</Th>
          <Th>Agent</Th>
          <Th>Task</Th>
          <Th className="text-right">Duration</Th>
          <Th className="text-right">When</Th>
        </tr>
      </thead>
      <tbody>
        {runs.data.items.map((run) => {
          const when = run.finished_at ?? run.started_at ?? run.created_at;
          return (
            <tr key={run.id} className="row-hover transition-colors">
              <Td>
                <Link to={`/runs/${run.id}`} className="link font-mono text-2xs">
                  {run.id.slice(0, 8)}
                </Link>
              </Td>
              <Td>{run.is_terminal ? <OutcomeBadge outcome={run.outcome} /> : <StateBadge state={run.state} />}</Td>
              <Td>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Mono>{run.agent.label}</Mono>
                  {run.agent.is_simulated && <SimulatedBadge compact />}
                </div>
              </Td>
              <Td>
                <Mono>{run.task_key}</Mono>
              </Td>
              <Td className="tnum text-right">{formatDuration(run.timings.total_s)}</Td>
              <Td className="muted tnum whitespace-nowrap text-right" title={formatTime(when)}>
                {formatRelative(when)}
              </Td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

// --------------------------------------------------------------------------- //
export default function Overview() {
  const stats = useStats();
  const overview = useOverview();

  const header = (meta?: ReactNode) => (
    <PageHeader
      eyebrow="Agent Labs · Prove. Measure. Improve."
      title="Overview"
      description="Evaluation activity across every experiment. Headline rates cover live model results only and always show the runs they were computed from."
      meta={meta}
      actions={
        <>
          <Link to="/leaderboard" className={BUTTON_LINK}>
            Leaderboard
          </Link>
          <Link to="/experiments" className={PRIMARY_LINK}>
            Experiments
          </Link>
        </>
      }
    />
  );

  if (stats.isError) {
    return (
      <div className="space-y-6">
        {header()}
        <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
      </div>
    );
  }
  if (stats.isLoading || !stats.data) {
    return (
      <div className="space-y-6">
        {header()}
        <LoadingTiles count={8} />
      </div>
    );
  }

  if (stats.data.is_empty) {
    return (
      <div className="space-y-6">
        {header()}
        <EmptyState
          title="No benchmarks or experiments yet"
          description={
            <div className="space-y-3 text-left">
              <p>
                Nothing has been imported or executed. Run these commands from the repository root to
                import the bundled <Mono>textkit</Mono> benchmark and execute it with the deterministic
                reference agent.
              </p>
              <CodeBlock maxHeight="max-h-80">{ONBOARDING}</CodeBlock>
              <p className="muted">
                The benchmark image must exist first: <Mono>agentbench images build</Mono>. Images are
                never pulled during a run, so benchmarks stay offline-safe.
              </p>
            </div>
          }
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Link to="/benchmarks" className={BUTTON_LINK}>
                Benchmarks
              </Link>
              <Link to="/experiments" className={PRIMARY_LINK}>
                Experiments
              </Link>
            </div>
          }
        />
      </div>
    );
  }

  if (overview.isError) {
    return (
      <div className="space-y-6">
        {header()}
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      </div>
    );
  }
  if (overview.isLoading || !overview.data) {
    return (
      <div className="space-y-6">
        {header()}
        <LoadingTiles count={8} />
      </div>
    );
  }

  const data = overview.data;
  const meta = (
    <>
      <span className="subtle text-2xs">computed {formatRelative(data.computed_at)}</span>
      {data.runs.active > 0 && (
        <Badge tone="info">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" aria-hidden />
          {data.runs.active} run(s) in flight
        </Badge>
      )}
    </>
  );

  if (data.experiments.total === 0) {
    return (
      <div className="space-y-6">
        {header(meta)}
        <EmptyState
          title="No experiments yet"
          description={
            <>
              {stats.data.tasks} task(s) and {stats.data.agents} agent(s) are registered.
              {stats.data.needs_benchmark && " Import a benchmark first."}
              {stats.data.needs_agent && " Register an agent first."} Create an experiment to run an
              agent against a benchmark.
            </>
          }
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Link to="/benchmarks" className={BUTTON_LINK}>
                Benchmarks
              </Link>
              <Link to="/experiments" className={PRIMARY_LINK}>
                Create an experiment
              </Link>
            </div>
          }
        />
      </div>
    );
  }

  const model = data.model_results;
  const hasModel = model.finished_runs > 0;
  const finishedAll = Object.values(data.runs.by_outcome).reduce((sum, n) => sum + (n ?? 0), 0);
  const failed = count(model, "unsolved") + count(model, "timeout") + count(model, "agent_error");
  const excludedBreakdown = EXCLUDED_OUTCOMES.filter((o) => count(model, o) > 0)
    .map((o) => `${OUTCOME_META[o].label} ${count(model, o)}`)
    .join(" · ");
  const best = data.best_observed;
  const noModel = "no model results yet";

  return (
    <div className="fade-in space-y-6">
      {header(meta)}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="Experiments"
          value={data.experiments.total}
          hint={Object.entries(data.experiments.by_state)
            .map(([state, n]) => `${n} ${state}`)
            .join(" · ")}
        />
        <Kpi
          label="Runs"
          value={data.runs.total}
          hint={`${finishedAll} finished · ${data.runs.active} in flight · ${stats.data.tasks} task(s)`}
        />
        <Kpi
          label="Task success rate"
          scope="model results"
          value={hasModel ? percentText(model.success_rate) : "—"}
          hint={
            model.success_rate.denominator > 0 ? (
              <>
                <p className="tnum">
                  {model.success_rate.numerator}/{model.success_rate.denominator} attributable runs solved
                </p>
                {ciText(model.success_rate) && <p className="tnum subtle">{ciText(model.success_rate)}</p>}
                {model.success_rate.small_sample && <p className="text-warn">small sample · indicative only</p>}
              </>
            ) : (
              "no attributable model runs yet"
            )
          }
        />
        <Kpi
          label="Best performing model"
          scope="highest observed"
          value={
            best ? (
              <Link
                to={`/leaderboard/${best.agent_config_id}`}
                className="link block truncate text-[15px] leading-tight"
                title={best.label}
              >
                {best.label}
              </Link>
            ) : (
              <span className="muted text-[15px] font-medium">—</span>
            )
          }
          hint={
            best ? (
              <>
                <p className="tnum">
                  {percentText(best.success_rate)} ({best.success_rate.numerator}/
                  {best.success_rate.denominator})
                </p>
                <p className={cx(best.distinguishable_from_runner_up === true ? "subtle" : "text-warn")}>
                  {best.distinguishable_from_runner_up === true
                    ? "95% interval does not overlap the runner-up's"
                    : best.distinguishable_from_runner_up === null
                      ? "no runner-up to compare against"
                      : "not statistically distinguishable from runner-up"}
                </p>
                <p className="subtle" title={data.best_observed_note}>
                  Not a significance claim
                </p>
              </>
            ) : (
              noModel
            )
          }
        />
        <Kpi
          label="Avg execution time"
          scope="model results"
          value={formatDuration(model.runtime.mean_s)}
          hint={
            model.runtime.n > 0
              ? `median ${formatDuration(model.runtime.median_s)} · n = ${model.runtime.n} timed run(s)`
              : "no timed model runs yet"
          }
        />
        <Kpi
          label="Solved runs"
          scope="model results"
          tone={hasModel && count(model, "solved") > 0 ? "good" : undefined}
          value={hasModel ? count(model, "solved") : "—"}
          hint={
            hasModel
              ? `${model.tasks_passed} of ${model.tasks_attempted} distinct task(s) solved at least once`
              : noModel
          }
        />
        <Kpi
          label="Failed runs"
          scope="model results"
          tone={hasModel && failed > 0 ? "bad" : undefined}
          value={hasModel ? failed : "—"}
          hint={
            hasModel
              ? `unsolved ${count(model, "unsolved")} · timeout ${count(model, "timeout")} · agent error ${count(model, "agent_error")}`
              : noModel
          }
        />
        <Kpi
          label="Excluded runs"
          scope="model results"
          value={hasModel ? model.excluded_runs : "—"}
          hint={
            hasModel ? (
              <>
                <p>{excludedBreakdown || "none"}</p>
                <p className="subtle">Not failures: infra, invalid task, grading error and cancelled runs stay out of every rate</p>
              </>
            ) : (
              noModel
            )
          }
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Model performance"
          subtitle="Success rate per agent · live model results only"
        >
          <ModelPerformance />
        </Card>
        <Card title="Results by provenance" subtitle="Where each finished run came from">
          <ProvenanceSplit rows={data.by_provenance} />
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Recent run activity"
          subtitle="Latest 12 runs, all provenance"
          actions={
            <Link to="/trajectories" className="accent-text text-2xs font-medium">
              Trajectories →
            </Link>
          }
        >
          <RecentRuns />
        </Card>
        <Card
          title="Failure distribution"
          subtitle="Effective classification per non-solved run · model results only"
          actions={
            <Link to="/failures" className="accent-text text-2xs font-medium">
              Inspect →
            </Link>
          }
        >
          <FailureDistribution categories={data.model_failure_categories} />
        </Card>
      </div>

      <Card
        title="Recent experiments"
        subtitle={`${data.experiments.recent.length} most recent`}
        actions={
          <Link to="/experiments" className="accent-text text-2xs font-medium">
            All experiments →
          </Link>
        }
      >
        {data.experiments.recent.length === 0 ? (
          <p className="muted p-4 text-xs">No experiments yet.</p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>State</Th>
                <Th>Agent</Th>
                <Th>Benchmark</Th>
                <Th className="text-right">Created</Th>
              </tr>
            </thead>
            <tbody>
              {data.experiments.recent.map((experiment) => (
                <tr key={experiment.id} className="row-hover transition-colors">
                  <Td>
                    <Link to={`/experiments/${experiment.id}`} className="link font-medium">
                      {experiment.name}
                    </Link>
                    <Mono className="subtle ml-2">{experiment.id.slice(0, 8)}</Mono>
                  </Td>
                  <Td>
                    <ExperimentStateBadge state={experiment.state} />
                  </Td>
                  <Td>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Mono>{experiment.agent}</Mono>
                      {experiment.is_simulated && <SimulatedBadge compact />}
                    </div>
                  </Td>
                  <Td>
                    <Mono>{experiment.benchmark}</Mono>
                  </Td>
                  <Td className="muted tnum whitespace-nowrap text-right" title={formatTime(experiment.created_at)}>
                    {formatRelative(experiment.created_at)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
