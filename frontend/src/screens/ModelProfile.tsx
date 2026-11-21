import { useState, type CSSProperties, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ApiError } from "../api/client";
import { useAgentProfile } from "../api/hooks";
import type { AgentProfile, ExperimentState, Provenance, ProvenanceValue, Rate } from "../api/types";
import { AXIS_TICK, OutcomeStack, RateBar, binDurations, useChartTheme } from "../components/charts";
import {
  COUNTED_OUTCOMES,
  OUTCOME_META,
  ProvenanceBadge,
  ProvenanceNotice,
  RateDisplay,
  formatDuration,
  formatRelative,
  formatTime,
} from "../components/domain";
import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  Segmented,
  Table,
  Td,
  Th,
  cx,
  type BadgeTone,
} from "../components/ui";

// --------------------------------------------------------------------------- //
// local helpers
// --------------------------------------------------------------------------- //
const PROVENANCE_LABELS: Record<ProvenanceValue, string> = {
  simulated: "Simulated agent",
  offline_adapter_test: "Offline adapter test",
  live_model: "Live model run",
  external_agent: "External agent",
};

const EXPERIMENT_STATE_TONES: Record<ExperimentState, BadgeTone> = {
  draft: "neutral",
  queued: "info",
  running: "info",
  paused: "warn",
  completed: "accent",
  cancelled: "neutral",
};

/** Resolve a bare provenance value to the full description the API sent in `summary`. */
function provenanceLookup(profile: AgentProfile) {
  const known = new Map(profile.summary.map((entry) => [entry.provenance.value, entry.provenance]));
  return (value: ProvenanceValue): Provenance =>
    known.get(value) ?? {
      value,
      label: PROVENANCE_LABELS[value] ?? value,
      caveat: "",
      is_model_result: value === "live_model",
    };
}

function percentText(rate: Rate): string {
  if (rate.percent === null) return "—";
  return `${Number.isInteger(rate.percent) ? rate.percent : rate.percent.toFixed(1)}%`;
}

function RateCell({ rate }: { rate: Rate }) {
  if (rate.denominator === 0) return <span className="subtle">—</span>;
  return (
    <span className="tnum whitespace-nowrap">
      {percentText(rate)}
      <span className="subtle ml-1">
        ({rate.numerator}/{rate.denominator})
      </span>
    </span>
  );
}

function Metric({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="eyebrow">{label}</p>
      <div className="mt-1 text-xs">{children}</div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// charts
// --------------------------------------------------------------------------- //
interface HistoryPoint {
  key: string;
  name: string;
  percent: number | null;
  band: [number, number] | null;
  numerator: number;
  denominator: number;
  provenance: string;
  created: string;
}

function HistoryTooltip({
  active,
  payload,
  style,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: unknown }>;
  style: CSSProperties;
}) {
  const point = payload?.[0]?.payload as HistoryPoint | undefined;
  if (!active || !point) return null;
  return (
    <div style={style} className="px-2.5 py-2">
      <p className="font-medium">{point.name}</p>
      <p className="tnum mt-0.5">
        {point.percent === null ? "no attributable runs" : `${point.percent}% (${point.numerator}/${point.denominator})`}
      </p>
      {point.band && (
        <p className="tnum opacity-70">
          95% CI {point.band[0].toFixed(0)}–{point.band[1].toFixed(0)}%
        </p>
      )}
      <p className="opacity-70">
        {point.provenance} · {point.created}
      </p>
    </div>
  );
}

function HistoryChart({ history }: { history: AgentProfile["history"] }) {
  const theme = useChartTheme();
  const data: HistoryPoint[] = history.map((entry, index) => ({
    key: `${index + 1}`,
    name: entry.name,
    percent: entry.success_rate.percent,
    band:
      entry.success_rate.denominator > 0 && entry.success_rate.ci_low !== null && entry.success_rate.ci_high !== null
        ? [entry.success_rate.ci_low * 100, entry.success_rate.ci_high * 100]
        : null,
    numerator: entry.success_rate.numerator,
    denominator: entry.success_rate.denominator,
    provenance: entry.provenance.replace(/_/g, " "),
    created: entry.created_at ? new Date(entry.created_at).toLocaleDateString() : "—",
  }));
  if (data.every((point) => point.percent === null)) {
    return <p className="muted p-4 text-xs">No experiment has attributable runs yet.</p>;
  }
  return (
    <div className="px-2 pb-2 pt-4">
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ left: 0, right: 16, top: 4, bottom: 4 }}>
          <CartesianGrid stroke={theme.grid} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="key"
            tick={{ ...AXIS_TICK, fill: theme.axis }}
            stroke={theme.grid}
            tickLine={false}
            label={{ value: "experiment (oldest → newest)", position: "insideBottom", offset: -2, fontSize: 10, fill: theme.axis }}
            height={32}
          />
          <YAxis
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            unit="%"
            width={44}
            tick={{ ...AXIS_TICK, fill: theme.axis }}
            stroke={theme.grid}
            tickLine={false}
          />
          <Tooltip
            cursor={{ stroke: theme.border }}
            content={(props) => <HistoryTooltip active={props.active} payload={props.payload} style={theme.tooltip} />}
          />
          <Area
            dataKey="band"
            stroke="none"
            fill={theme.accent}
            fillOpacity={0.12}
            isAnimationActive={false}
            connectNulls={false}
            activeDot={false}
          />
          <Line
            dataKey="percent"
            stroke={theme.accent}
            strokeWidth={1.5}
            dot={{ r: 3, fill: theme.accent, strokeWidth: 0 }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
            connectNulls={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="subtle px-2 text-2xs">
        Line: observed success rate per experiment. Shaded band: 95% interval. Points without attributable runs are
        omitted.
      </p>
    </div>
  );
}

function DurationHistogram({ durations }: { durations: AgentProfile["durations"] }) {
  const theme = useChartTheme();
  const values = durations.map((entry) => entry.total_s);
  const bins = binDurations(values);
  if (bins.length === 0) return <p className="muted p-4 text-xs">No timed attributable runs.</p>;

  const data = bins.map((bin) => ({
    label: bin.label,
    ...Object.fromEntries(COUNTED_OUTCOMES.map((outcome) => [outcome, 0])),
  })) as ({ label: string } & Record<string, number | string>)[];
  for (const entry of durations) {
    let index = bins.findIndex((bin, i) => entry.total_s >= bin.from && (entry.total_s < bin.to || i === bins.length - 1));
    if (index < 0) index = 0;
    data[index][entry.outcome] = (Number(data[index][entry.outcome]) || 0) + 1;
  }
  const present = COUNTED_OUTCOMES.filter((outcome) => durations.some((entry) => entry.outcome === outcome));

  return (
    <div className="px-2 pb-2 pt-4">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ left: 0, right: 16, top: 4, bottom: 4 }}>
          <CartesianGrid stroke={theme.grid} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={{ ...AXIS_TICK, fill: theme.axis }} stroke={theme.grid} tickLine={false} />
          <YAxis
            allowDecimals={false}
            width={32}
            tick={{ ...AXIS_TICK, fill: theme.axis }}
            stroke={theme.grid}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: theme.surface }}
            contentStyle={theme.tooltip}
            labelStyle={{ color: theme.text }}
            formatter={(value, name) => [`${String(value)} run(s)`, OUTCOME_META[name as keyof typeof OUTCOME_META]?.label ?? String(name)]}
          />
          {present.map((outcome) => (
            <Bar key={outcome} dataKey={outcome} stackId="outcome" fill={OUTCOME_META[outcome].color} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-2">
        {present.map((outcome) => (
          <span key={outcome} className="muted inline-flex items-center gap-1.5 text-2xs">
            <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: OUTCOME_META[outcome].color }} />
            {OUTCOME_META[outcome].label}
          </span>
        ))}
        <span className="subtle tnum ml-auto text-2xs">n = {values.length} run(s)</span>
      </div>
    </div>
  );
}

function CategoryBars({ categories }: { categories: Record<string, number> }) {
  const entries = Object.entries(categories).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <p className="muted p-4 text-xs">No classified failures for this agent.</p>;
  const max = Math.max(...entries.map(([, n]) => n));
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
            <span className="tnum muted shrink-0">{n}</span>
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

// --------------------------------------------------------------------------- //
export default function ModelProfile() {
  const { agentId } = useParams();
  const profile = useAgentProfile(agentId);
  const [durationScope, setDurationScope] = useState<string | null>(null);

  const back = (
    <Link to="/leaderboard" className="hover:text-[rgb(var(--text))]">
      Leaderboard
    </Link>
  );

  if (profile.isError) {
    const notFound = profile.error instanceof ApiError && profile.error.status === 404;
    return (
      <div className="space-y-6">
        <PageHeader eyebrow={back} title={notFound ? "Agent not found" : "Agent profile"} />
        {notFound ? (
          <EmptyState
            title="No agent configuration with this id"
            description={
              <>
                <Mono>{agentId}</Mono> is not registered. It may have been deleted, or the link is out of date.
              </>
            }
            action={
              <Link to="/leaderboard" className="accent-text text-xs font-medium">
                Back to leaderboard →
              </Link>
            }
          />
        ) : (
          <ErrorState error={profile.error} onRetry={() => void profile.refetch()} />
        )}
      </div>
    );
  }

  if (profile.isLoading || !profile.data) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow={back} title="Agent profile" />
        <LoadingTiles count={4} />
        <LoadingRows rows={6} />
      </div>
    );
  }

  const data = profile.data;
  const { agent } = data;
  const resolve = provenanceLookup(data);
  const hasModel = data.summary.some((entry) => entry.provenance.is_model_result);
  const nonModel = data.summary.filter((entry) => !entry.provenance.is_model_result);

  const durationScopes = Array.from(new Set(data.durations.map((entry) => entry.provenance)));
  const defaultScope =
    durationScopes.find((value) => value === "live_model") ?? durationScopes[0] ?? "live_model";
  const scope = durationScope && durationScopes.includes(durationScope as ProvenanceValue) ? durationScope : defaultScope;
  const scopedDurations = data.durations.filter((entry) => entry.provenance === scope);

  return (
    <div className="fade-in space-y-6">
      <PageHeader
        eyebrow={
          <span className="flex items-center gap-1.5">
            {back}
            <span aria-hidden>/</span>
            <span>Agent profile</span>
          </span>
        }
        title={agent.label}
        description={agent.description || undefined}
        meta={
          <>
            <Badge>
              model <Mono>{agent.model ?? "not recorded"}</Mono>
            </Badge>
            <Badge>
              adapter <Mono>{agent.adapter}</Mono>
            </Badge>
            {agent.provider && (
              <Badge>
                provider <Mono>{agent.provider}</Mono>
              </Badge>
            )}
            {data.summary.map((entry) => (
              <ProvenanceBadge key={entry.provenance.value} provenance={entry.provenance} />
            ))}
            {!agent.enabled && <Badge tone="warn">disabled</Badge>}
          </>
        }
        actions={
          <>
            {agent.adapter === "openai_tools" && (
              <Link
                to="/models"
                className="surface-2 hairline inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors hover:border-[rgb(var(--border-strong))]"
              >
                Local models
              </Link>
            )}
            <Link
              to="/agents"
              className="surface-2 hairline inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors hover:border-[rgb(var(--border-strong))]"
            >
              Agent configs
            </Link>
          </>
        }
      />

      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
        <p className="eyebrow text-warn">Evidence</p>
        <p className="mt-1">{data.evidence_note}</p>
      </div>

      {!hasModel && nonModel.length > 0 && <ProvenanceNotice provenance={nonModel[0].provenance} />}

      {data.summary.length === 0 ? (
        <EmptyState
          title="No finished runs for this agent"
          description="Run an experiment with this agent configuration to build its profile."
          action={
            <Link to="/experiments" className="accent-text text-xs font-medium">
              Go to experiments →
            </Link>
          }
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {data.summary.map((entry) => (
            <Card
              key={entry.provenance.value}
              title={<ProvenanceBadge provenance={entry.provenance} />}
              subtitle={`${entry.finished_runs} finished run(s) · last ${formatRelative(entry.last_finished_at)}`}
            >
              <div className="space-y-4 p-4">
                <div className="space-y-2">
                  <RateDisplay rate={entry.success_rate} label="Success rate" size="lg" />
                  <RateBar rate={entry.success_rate} />
                </div>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Metric label="Attributable">
                    <span className="tnum">{entry.attributable_runs}</span>
                    <span className="subtle ml-1">· {entry.excluded_runs} excluded</span>
                  </Metric>
                  <Metric label="Timeout rate">
                    <RateCell rate={entry.timeout_rate} />
                  </Metric>
                  <Metric label="Agent error rate">
                    <RateCell rate={entry.error_rate} />
                  </Metric>
                  <Metric label="Median runtime">
                    <span className="tnum">{formatDuration(entry.runtime.median_s)}</span>
                    <span className="subtle ml-1">n={entry.runtime.n}</span>
                  </Metric>
                </div>
                <OutcomeStack outcomes={entry.outcomes} showLegend />
              </div>
            </Card>
          ))}
        </div>
      )}

      {data.history.length > 0 && (
        <Card title="Performance history" subtitle={`${data.history.length} experiment(s), oldest first`}>
          <HistoryChart history={data.history} />
          <Table>
            <thead>
              <tr>
                <Th>#</Th>
                <Th>Experiment</Th>
                <Th>State</Th>
                <Th>Provenance</Th>
                <Th>Benchmark</Th>
                <Th>Success rate</Th>
                <Th className="text-right">Reps</Th>
                <Th className="text-right">Median</Th>
                <Th className="text-right">Created</Th>
              </tr>
            </thead>
            <tbody>
              {data.history.map((entry, index) => (
                <tr key={entry.experiment_id} className="row-hover transition-colors">
                  <Td className="tnum subtle">{index + 1}</Td>
                  <Td>
                    <Link to={`/experiments/${entry.experiment_id}`} className="link font-medium">
                      {entry.name}
                    </Link>
                  </Td>
                  <Td>
                    <Badge tone={EXPERIMENT_STATE_TONES[entry.state] ?? "neutral"}>{entry.state}</Badge>
                  </Td>
                  <Td>
                    <ProvenanceBadge provenance={resolve(entry.provenance)} compact />
                  </Td>
                  <Td>
                    <Mono>{entry.benchmark}</Mono>
                  </Td>
                  <Td className="min-w-[10rem]">
                    <RateCell rate={entry.success_rate} />
                    {entry.success_rate.denominator > 0 && <RateBar rate={entry.success_rate} className="mt-1" />}
                  </Td>
                  <Td className="tnum text-right">{entry.repetitions}</Td>
                  <Td className="tnum text-right">{formatDuration(entry.runtime.median_s)}</Td>
                  <Td className="muted tnum whitespace-nowrap text-right" title={formatTime(entry.created_at)}>
                    {formatRelative(entry.created_at)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Strengths" subtitle="Tasks solved in at least one attributable attempt">
          <EvidenceList items={data.strengths} tone="good" empty="No task solved yet." />
        </Card>
        <Card title="Weaknesses" subtitle="Tasks with attributable attempts and no solve">
          <EvidenceList items={data.weaknesses} tone="bad" empty="No unsolved task with attributable attempts." />
        </Card>
      </div>

      {data.tasks.length > 0 && (
        <Card title="Task-level results" subtitle={`${data.tasks.length} task × provenance row(s)`}>
          <Table>
            <thead>
              <tr>
                <Th>Task</Th>
                <Th>Category</Th>
                <Th>Provenance</Th>
                <Th className="text-right">Attempts</Th>
                <Th className="text-right">Solved</Th>
                <Th>Outcomes</Th>
                <Th>Success rate</Th>
                <Th className="text-right">Median</Th>
              </tr>
            </thead>
            <tbody>
              {data.tasks.map((task) => (
                <tr key={`${task.provenance}|${task.task_key}`} className="row-hover transition-colors">
                  <Td>
                    <Link to={`/benchmarks/tasks/${encodeURIComponent(task.task_key)}`} className="link font-mono text-2xs">
                      {task.task_key}
                    </Link>
                  </Td>
                  <Td className="muted">{task.category || "—"}</Td>
                  <Td>
                    <ProvenanceBadge provenance={resolve(task.provenance)} compact />
                  </Td>
                  <Td className="tnum text-right" title={`${task.attributable_runs} attributable · ${task.excluded_runs} excluded`}>
                    {task.finished_runs}
                  </Td>
                  <Td className="tnum text-right">{task.outcomes.solved ?? 0}</Td>
                  <Td className="min-w-[8rem]">
                    <OutcomeStack outcomes={task.outcomes} />
                  </Td>
                  <Td>
                    <RateCell rate={task.success_rate} />
                  </Td>
                  <Td className="tnum text-right">{formatDuration(task.runtime.median_s)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card
          title="Execution time distribution"
          subtitle="Attributable runs with a recorded duration, by outcome"
          actions={
            durationScopes.length > 1 ? (
              <Segmented
                label="Provenance"
                value={scope}
                onChange={setDurationScope}
                options={durationScopes.map((value) => ({ value, label: value.replace(/_/g, " ") }))}
              />
            ) : undefined
          }
        >
          <DurationHistogram durations={scopedDurations} />
          <div className="px-4 pb-4">
            <Note>{data.runtime_note}</Note>
          </div>
        </Card>
        <Card
          title="Failure categories"
          subtitle={hasModel ? "Model-result runs only" : "All runs for this agent"}
          actions={
            <Link to="/failures" className="accent-text text-2xs font-medium">
              Inspect →
            </Link>
          }
        >
          <CategoryBars categories={data.failure_categories} />
        </Card>
      </div>

      {data.by_category.length > 0 && (
        <Card title="By task category">
          <Table>
            <thead>
              <tr>
                <Th>Category</Th>
                <Th>Provenance</Th>
                <Th className="text-right">Tasks</Th>
                <Th className="text-right">Runs</Th>
                <Th>Outcomes</Th>
                <Th>Success rate</Th>
              </tr>
            </thead>
            <tbody>
              {data.by_category.map((entry) => (
                <tr key={`${entry.provenance}|${entry.category}`} className="row-hover transition-colors">
                  <Td className="font-medium">{entry.category || "uncategorised"}</Td>
                  <Td>
                    <ProvenanceBadge provenance={resolve(entry.provenance)} compact />
                  </Td>
                  <Td className="tnum text-right">{entry.tasks_attempted}</Td>
                  <Td className="tnum text-right">{entry.finished_runs}</Td>
                  <Td className="min-w-[8rem]">
                    <OutcomeStack outcomes={entry.outcomes} />
                  </Td>
                  <Td className="min-w-[10rem]">
                    <RateCell rate={entry.success_rate} />
                    {entry.success_rate.denominator > 0 && <RateBar rate={entry.success_rate} className="mt-1" />}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <p className="subtle text-2xs">
        Computed {formatRelative(data.computed_at)}. No statistical significance is claimed on this page.
      </p>
    </div>
  );
}

function EvidenceList({
  items,
  tone,
  empty,
}: {
  items: AgentProfile["strengths"];
  tone: "good" | "bad";
  empty: string;
}) {
  if (items.length === 0) return <p className="muted p-4 text-xs">{empty}</p>;
  return (
    <ul className="hairline divide-y divide-[rgb(var(--border))]">
      {items.map((item) => (
        <li key={item.task_key} className="flex items-center justify-between gap-3 px-4 py-2.5 text-xs">
          <Link to={`/benchmarks/tasks/${encodeURIComponent(item.task_key)}`} className="link truncate font-mono text-2xs">
            {item.task_key}
          </Link>
          <span className={cx("tnum shrink-0", tone === "good" ? "text-good" : "text-bad")}>{item.evidence}</span>
        </li>
      ))}
    </ul>
  );
}
