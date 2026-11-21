import { useMemo, useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useAnalytics } from "../api/hooks";
import type { Analytics as AnalyticsData, ProvenanceValue, RunOutcome, Tally } from "../api/types";
import { AXIS_TICK, OutcomeStack, RateBar, useChartTheme } from "../components/charts";
import {
  COUNTED_OUTCOMES,
  EXCLUDED_OUTCOMES,
  OUTCOME_META,
  OptionalNumber,
  ProvenanceBadge,
  RateDisplay,
  formatDuration,
  formatTime,
} from "../components/domain";
import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  Segmented,
  StatTile,
  Table,
  Td,
  Th,
  type BadgeTone,
} from "../components/ui";

const PROVENANCE_TONE: Record<ProvenanceValue, BadgeTone> = {
  simulated: "warn",
  offline_adapter_test: "warn",
  live_model: "good",
  external_agent: "info",
};

const ALL_OUTCOMES: RunOutcome[] = [...COUNTED_OUTCOMES, ...EXCLUDED_OUTCOMES];

function dayLabel(date: string): string {
  const parsed = Date.parse(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed)) return date;
  return new Date(parsed).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

// --------------------------------------------------------------------------- //
function ActivityChart({ daily }: { daily: AnalyticsData["daily"] }) {
  const theme = useChartTheme();
  const [mode, setMode] = useState<"provenance" | "outcomes">("provenance");
  const data = useMemo(
    () =>
      daily.map((day) => ({
        label: dayLabel(day.date),
        model: day.model_runs,
        nonModel: day.non_model_runs,
        ...Object.fromEntries(ALL_OUTCOMES.map((outcome) => [outcome, day.outcomes[outcome] ?? 0])),
      })),
    [daily],
  );

  return (
    <Card
      title="Activity over time"
      subtitle="Finished runs per day (by finish date)"
      actions={
        <Segmented
          label="Split activity by"
          value={mode}
          onChange={setMode}
          options={[
            { value: "provenance", label: "Model vs non-model" },
            { value: "outcomes", label: "Outcomes" },
          ]}
        />
      }
    >
      <div className="p-4">
        {data.length === 0 ? (
          <p className="muted text-xs">No finished runs yet.</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data} margin={{ left: -16, right: 8, top: 8, bottom: 0 }}>
              <CartesianGrid stroke={theme.grid} vertical={false} />
              <XAxis dataKey="label" tick={{ ...AXIS_TICK, fill: theme.axis }} stroke={theme.grid} tickLine={false} />
              <YAxis allowDecimals={false} tick={{ ...AXIS_TICK, fill: theme.axis }} stroke={theme.grid} tickLine={false} />
              <Tooltip contentStyle={theme.tooltip} cursor={{ fill: theme.surface }} />
              <Legend wrapperStyle={{ fontSize: 11, color: theme.axis }} iconSize={8} />
              {mode === "provenance" ? (
                <>
                  <Bar dataKey="model" name="model results" stackId="runs" fill={theme.accent} isAnimationActive={false} />
                  <Bar
                    dataKey="nonModel"
                    name="simulated / offline"
                    stackId="runs"
                    fill={theme.border}
                    radius={[3, 3, 0, 0]}
                    isAnimationActive={false}
                  />
                </>
              ) : (
                ALL_OUTCOMES.map((outcome) => (
                  <Bar
                    key={outcome}
                    dataKey={outcome}
                    name={`${OUTCOME_META[outcome].label}${OUTCOME_META[outcome].counted ? "" : " (excluded)"}`}
                    stackId="runs"
                    fill={OUTCOME_META[outcome].color}
                    isAnimationActive={false}
                  />
                ))
              )}
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}

function TallyTable({
  rows,
  nameLabel,
  empty,
}: {
  rows: { key: string; name: ReactNode; tally: Tally }[];
  nameLabel: string;
  empty: string;
}) {
  if (rows.length === 0) return <p className="muted p-4 text-xs">{empty}</p>;
  return (
    <Table>
      <thead>
        <tr>
          <Th>{nameLabel}</Th>
          <Th>Success rate</Th>
          <Th className="hidden sm:table-cell">Interval</Th>
          <Th className="text-right">Excluded</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            <Td>{row.name}</Td>
            <Td>
              <RateDisplay rate={row.tally.success_rate} size="sm" />
            </Td>
            <Td className="hidden w-40 sm:table-cell">
              <RateBar rate={row.tally.success_rate} />
            </Td>
            <Td className="tnum text-right">{row.tally.excluded_runs}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function DurationChart({ rows }: { rows: AnalyticsData["duration_by_outcome"] }) {
  const theme = useChartTheme();
  const data = rows
    .filter((row) => row.n > 0)
    .map((row) => ({
      outcome: row.outcome,
      label: `${OUTCOME_META[row.outcome]?.label ?? row.outcome} (n=${row.n})`,
      median: row.median_s,
      p90: row.p90_s,
      n: row.n,
    }));
  if (data.length === 0) return <p className="muted p-4 text-xs">No timed model runs.</p>;
  const color = (outcome: RunOutcome) => OUTCOME_META[outcome]?.color ?? theme.accent;
  return (
    <div className="p-4">
      <ResponsiveContainer width="100%" height={Math.max(160, data.length * 44)}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 0 }} barGap={2}>
          <CartesianGrid stroke={theme.grid} horizontal={false} />
          <XAxis
            type="number"
            tick={{ ...AXIS_TICK, fill: theme.axis }}
            stroke={theme.grid}
            tickLine={false}
            tickFormatter={(value: number) => formatDuration(value)}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={130}
            tick={{ ...AXIS_TICK, fill: theme.axis }}
            stroke={theme.grid}
            tickLine={false}
          />
          <Tooltip
            contentStyle={theme.tooltip}
            cursor={{ fill: theme.surface }}
            formatter={(value, name) => [typeof value === "number" ? formatDuration(value) : "—", name]}
          />
          <Bar dataKey="median" name="median" barSize={8} radius={[0, 3, 3, 0]} isAnimationActive={false}>
            {data.map((row) => (
              <Cell key={row.outcome} fill={color(row.outcome)} />
            ))}
          </Bar>
          <Bar dataKey="p90" name="p90" barSize={8} radius={[0, 3, 3, 0]} isAnimationActive={false}>
            {data.map((row) => (
              <Cell key={row.outcome} fill={color(row.outcome)} fillOpacity={0.4} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <p className="subtle mt-1 text-2xs">Solid bar: median. Faded bar: 90th percentile. Small n makes both unstable.</p>
    </div>
  );
}

// --------------------------------------------------------------------------- //
export default function Analytics() {
  const analytics = useAnalytics();

  if (analytics.isError) return <ErrorState error={analytics.error} onRetry={() => void analytics.refetch()} />;
  if (analytics.isLoading || !analytics.data) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow="Analysis" title="Analytics" />
        <LoadingTiles count={4} />
      </div>
    );
  }

  const data = analytics.data;
  const tools = data.tools;
  const totalCalls = tools.by_tool.reduce((sum, row) => sum + row.calls, 0);
  const totalFailed = tools.by_tool.reduce((sum, row) => sum + row.failed_results, 0);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Analytics"
        description="Platform-wide activity and agent behaviour. Rates carry their numerator, denominator and 95% interval; excluded outcomes never count against an agent."
        meta={<span className="subtle text-2xs">computed {formatTime(data.computed_at)}</span>}
      />

      <div className="space-y-2">
        <Note>{data.scope_note}</Note>
        <Note>
          Descriptive only — no statistical significance is claimed. Overlapping intervals mean the data cannot tell the
          groups apart.
        </Note>
      </div>

      {data.finished_run_count === 0 ? (
        <EmptyState title="No finished runs" description="Run an experiment to populate analytics." />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Finished runs" value={data.finished_run_count.toLocaleString()} hint="all provenance" />
            <StatTile
              label="Model results"
              value={data.model_run_count.toLocaleString()}
              hint="live model or external agent"
            />
            <StatTile
              label="Tool calls"
              value={totalCalls.toLocaleString()}
              hint={`${tools.runs_with_tool_events} model run(s) with tool events`}
            />
            <StatTile
              label="Failed tool results"
              value={totalFailed.toLocaleString()}
              tone={totalFailed > 0 ? "warn" : undefined}
              hint={totalCalls > 0 ? `of ${totalCalls.toLocaleString()} calls` : "no tool calls"}
            />
          </div>

          <ActivityChart daily={data.daily} />

          <Card
            title="Success by provenance"
            subtitle="Simulated and offline results validate the pipeline; they are not model performance"
          >
            {data.by_provenance.length === 0 ? (
              <p className="muted p-4 text-xs">No finished runs.</p>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Provenance</Th>
                    <Th>Success rate</Th>
                    <Th className="hidden sm:table-cell">Interval</Th>
                    <Th>Outcomes</Th>
                    <Th className="text-right">Finished</Th>
                    <Th className="text-right">Excluded</Th>
                    <Th className="text-right">Median</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_provenance.map((row) => (
                    <tr key={row.provenance.value}>
                      <Td>
                        <ProvenanceBadge provenance={row.provenance} />
                      </Td>
                      <Td>
                        <RateDisplay rate={row.success_rate} size="sm" />
                      </Td>
                      <Td className="hidden w-40 sm:table-cell">
                        <RateBar rate={row.success_rate} />
                      </Td>
                      <Td className="w-40">
                        <OutcomeStack outcomes={row.outcomes} />
                      </Td>
                      <Td className="tnum text-right">{row.finished_runs}</Td>
                      <Td className="tnum text-right">{row.excluded_runs}</Td>
                      <Td className="tnum text-right">{formatDuration(row.runtime.median_s)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card title="Success by category" subtitle="Model results only">
              <TallyTable
                nameLabel="Category"
                empty="No model results yet."
                rows={data.by_category.map((row) => ({ key: row.category, name: <Mono>{row.category}</Mono>, tally: row }))}
              />
            </Card>
            <Card title="Success by difficulty" subtitle="Model results only">
              <TallyTable
                nameLabel="Difficulty"
                empty="No model results yet."
                rows={data.by_difficulty.map((row) => ({
                  key: row.difficulty,
                  name: <Badge tone="neutral">{row.difficulty}</Badge>,
                  tally: row,
                }))}
              />
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card title="Duration by outcome" subtitle="Model results only · wall time per run">
              <DurationChart rows={data.duration_by_outcome} />
            </Card>

            <Card title="Token usage by agent" subtitle="Summed over runs whose provider reported usage">
              {data.tokens_by_agent.length === 0 ? (
                <p className="muted p-4 text-xs">No runs.</p>
              ) : (
                <Table>
                  <thead>
                    <tr>
                      <Th>Agent</Th>
                      <Th className="text-right">Input</Th>
                      <Th className="text-right">Output</Th>
                      <Th className="text-right">Reported</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.tokens_by_agent.map((row) => (
                      <tr key={`${row.agent}-${row.provenance}`}>
                        <Td>
                          <Mono>{row.agent}</Mono>
                          <div className="mt-0.5">
                            <Badge tone={PROVENANCE_TONE[row.provenance] ?? "neutral"}>
                              {row.provenance.replace(/_/g, " ")}
                            </Badge>
                          </div>
                        </Td>
                        <Td className="text-right">
                          <OptionalNumber value={row.input_tokens} />
                        </Td>
                        <Td className="text-right">
                          <OptionalNumber value={row.output_tokens} />
                        </Td>
                        <Td className="tnum text-right">
                          {row.runs_reported}
                          <span className="subtle"> / {row.runs_reported + row.runs_unreported}</span>
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
              <p className="subtle hairline border-t px-4 py-2 text-2xs">
                "not reported" means no run of that agent reported usage — unknown, not zero.
              </p>
            </Card>
          </div>

          <Card
            title="Tool usage"
            subtitle="Model results only · a failed result is a tool.result event with ok = false"
          >
            <div className="hairline grid grid-cols-2 gap-3 border-b p-4 sm:grid-cols-4">
              {[
                { label: "Runs with tool events", value: tools.calls_per_run.n },
                { label: "Mean calls / run", value: tools.calls_per_run.mean ?? null },
                { label: "Median calls / run", value: tools.calls_per_run.median ?? null },
                { label: "Max calls / run", value: tools.calls_per_run.max ?? null },
              ].map((entry) => (
                <div key={entry.label}>
                  <p className="eyebrow">{entry.label}</p>
                  <p className="tnum mt-1 text-sm font-semibold">
                    {entry.value === null || entry.value === undefined ? (
                      <span className="muted font-normal">—</span>
                    ) : (
                      Number(entry.value.toFixed(2)).toLocaleString()
                    )}
                  </p>
                </div>
              ))}
            </div>
            {tools.by_tool.length === 0 ? (
              <p className="muted p-4 text-xs">No tool events recorded for model runs.</p>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Tool</Th>
                    <Th className="text-right">Calls</Th>
                    <Th className="text-right">Failed results</Th>
                    <Th>Failure rate</Th>
                    <Th className="hidden sm:table-cell">Interval</Th>
                  </tr>
                </thead>
                <tbody>
                  {tools.by_tool.map((row) => (
                    <tr key={row.tool}>
                      <Td>
                        <Mono>{row.tool}</Mono>
                      </Td>
                      <Td className="tnum text-right">{row.calls.toLocaleString()}</Td>
                      <Td className={row.failed_results > 0 ? "tnum text-warn text-right" : "tnum text-right"}>
                        {row.failed_results}
                      </Td>
                      <Td>
                        <RateDisplay rate={row.failure_rate} size="sm" />
                      </Td>
                      <Td className="hidden w-40 sm:table-cell">
                        <RateBar rate={row.failure_rate} />
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
