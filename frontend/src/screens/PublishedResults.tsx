/**
 * Published third-party benchmark results.
 *
 * These scores were measured by someone else and are never mixed with Agent
 * Labs results: the page leads with the source, compares rows only within one
 * harness, and draws every score with an interval so statistical ties are
 * visible rather than hidden by a ranked bar chart.
 */

import { useMemo, useState } from "react";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { useSearchParams } from "react-router-dom";

import { usePublishedResults } from "../api/hooks";
import type { PublishedDataset, PublishedRow } from "../api/types";
import { AXIS_TICK, useChartTheme } from "../components/charts";
import { Badge, Card, EmptyState, ErrorState, LoadingRows, Note, PageHeader, SearchInput, Segmented, Select, Table, Td, Th, cx } from "../components/ui";

const TOP_N = 20;

// --------------------------------------------------------------------------- //
/** Score with its interval for each row, on a shared axis that says where it starts. */
function IntervalPlot({ rows }: { rows: PublishedRow[] }) {
  const lows = rows.map((row) => row.interval.ci_low * 100);
  const min = Math.max(0, Math.floor((Math.min(...lows) - 2) / 5) * 5);
  const max = 100;
  const span = max - min;
  const ticks = Array.from({ length: Math.floor(span / 5) + 1 }, (_, i) => min + i * 5).filter((t) => t <= max);
  const x = (value: number) => ((value - min) / span) * 100;
  const leader = rows.find((row) => row.harness_leader);
  const tieBand = leader ? { from: x(leader.interval.ci_low * 100), to: 100 } : null;

  return (
    <div className="space-y-1">
      {/* axis */}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-2 sm:grid-cols-[minmax(160px,240px)_minmax(0,1fr)_64px]">
        <span className="hidden sm:block" />
        <div className="relative h-5">
          {ticks.map((tick) => (
            <span key={tick} className="subtle tnum absolute -translate-x-1/2 text-2xs" style={{ left: `${x(tick)}%` }}>
              {tick}%
            </span>
          ))}
        </div>
        <span className="hidden sm:block" />
      </div>
      <ol className="space-y-px">
        {rows.map((row) => {
          const tied = row.harness_leader || row.tied_with_harness_leader === true;
          return (
            <li
              key={`${row.rank}-${row.model}-${row.harness}`}
              className="row-hover grid grid-cols-[minmax(0,1fr)] items-center gap-x-2 gap-y-1 rounded-md px-1 py-1.5 sm:grid-cols-[minmax(160px,240px)_minmax(0,1fr)_64px]"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="subtle tnum w-6 shrink-0 text-right font-mono text-2xs">{row.rank}</span>
                <span className="min-w-0 truncate text-xs font-medium" title={`${row.model} · ${row.lab} · ${row.harness}`}>
                  {row.model}
                </span>
                {!row.on_default_harness && <Badge tone="info">{row.harness}</Badge>}
              </div>
              <div className="relative h-5" role="img" aria-label={`${row.model}: ${row.score_percent}%, derived 95% interval ${(row.interval.ci_low * 100).toFixed(1)} to ${(row.interval.ci_high * 100).toFixed(1)}%`}>
                {ticks.map((tick) => (
                  <span key={tick} className="absolute inset-y-0 w-px" style={{ left: `${x(tick)}%`, backgroundColor: "rgb(var(--border) / 0.6)" }} />
                ))}
                {tieBand && row.on_default_harness && (
                  <span className="absolute inset-y-0 rounded-sm" style={{ left: `${tieBand.from}%`, right: 0, backgroundColor: "rgb(var(--accent) / 0.05)" }} />
                )}
                <span
                  className="absolute top-1/2 h-[2px] -translate-y-1/2 rounded-full"
                  style={{
                    left: `${x(row.interval.ci_low * 100)}%`,
                    width: `${x(row.interval.ci_high * 100) - x(row.interval.ci_low * 100)}%`,
                    backgroundColor: tied ? "rgb(var(--accent) / 0.55)" : "rgb(var(--text-subtle))",
                  }}
                />
                <span
                  className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2"
                  style={{
                    left: `${x(row.score_percent)}%`,
                    borderColor: tied ? "rgb(var(--accent))" : "rgb(var(--text-muted))",
                    backgroundColor: tied ? "rgb(var(--accent))" : "rgb(var(--surface))",
                  }}
                />
              </div>
              <span className="tnum text-right text-xs font-semibold">{row.score_percent.toFixed(1)}%</span>
            </li>
          );
        })}
      </ol>
      <div className="subtle flex flex-wrap items-center gap-x-5 gap-y-1 pt-3 text-2xs">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: "rgb(var(--accent))" }} /> statistically tied with #1
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full border-2" style={{ borderColor: "rgb(var(--text-muted))" }} /> below #1's interval
        </span>
        <span>Line: derived 95% interval · axis starts at {min}%, not 0</span>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
function CostChart({ rows }: { rows: PublishedRow[] }) {
  const theme = useChartTheme();
  const points = rows
    .filter((row) => row.cost_per_task_usd !== null && row.cost_per_task_usd > 0)
    .map((row) => ({ cost: row.cost_per_task_usd as number, score: row.score_percent, model: row.model, lab: row.lab, tied: row.harness_leader || row.tied_with_harness_leader === true }));
  const tied = points.filter((p) => p.tied);
  const rest = points.filter((p) => !p.tied);
  const missing = rows.length - points.length;

  return (
    <div>
      <div className="h-[340px]">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 16, bottom: 22, left: 0 }}>
            <CartesianGrid stroke={theme.grid} strokeDasharray="3 3" />
            <XAxis
              type="number"
              dataKey="cost"
              scale="log"
              domain={[0.01, 5]}
              ticks={[0.01, 0.03, 0.1, 0.3, 1, 3]}
              allowDataOverflow
              tick={{ ...AXIS_TICK, fill: theme.axis }}
              stroke={theme.grid}
              tickFormatter={(v: number) => `$${v}`}
              label={{ value: "Cost per task (USD, log scale)", position: "insideBottom", offset: -12, fill: theme.axis, fontSize: 11 }}
            />
            <YAxis type="number" dataKey="score" domain={[(dataMin: number) => Math.floor(dataMin / 10) * 10, 100]} tick={{ ...AXIS_TICK, fill: theme.axis }} stroke={theme.grid} tickFormatter={(v: number) => `${v}%`} width={44} />
            <ZAxis range={[46, 46]} />
            <Tooltip
              cursor={{ stroke: theme.border }}
              contentStyle={theme.tooltip}
              content={({ payload }) => {
                const point = payload?.[0]?.payload as (typeof points)[number] | undefined;
                if (!point) return null;
                return (
                  <div style={theme.tooltip} className="px-3 py-2">
                    <p className="font-medium">{point.model}</p>
                    <p className="subtle">{point.lab}</p>
                    <p className="tnum mt-1">
                      {point.score.toFixed(1)}% · ${point.cost.toFixed(2)} per task
                    </p>
                  </div>
                );
              }}
            />
            <Scatter data={rest} fill={theme.axis} isAnimationActive={false} />
            <Scatter data={tied} fill={theme.accent} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      {missing > 0 && <p className="subtle mt-1 text-2xs">{missing} row(s) have no published cost and are not plotted.</p>}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function SourceCard({ dataset, notice }: { dataset: PublishedDataset; notice: string }) {
  const source = dataset.source;
  return (
    <div className="rounded-xl border p-4" style={{ borderColor: "rgb(var(--warn) / 0.35)", backgroundColor: "rgb(var(--warn) / 0.05)" }}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="warn">Published by {source.publisher} · not run in Agent Labs</Badge>
        <Badge>{source.benchmark}</Badge>
        <Badge>{source.tasks} tasks</Badge>
        {source.status && <Badge>archived</Badge>}
      </div>
      <p className="mt-3 text-xs leading-relaxed">{notice}</p>
      <dl className="mt-3 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="subtle text-2xs">Source</dt>
          <dd>
            <a href={source.url} target="_blank" rel="noreferrer" className="link">
              {new URL(source.url).hostname}
            </a>
          </dd>
        </div>
        <div>
          <dt className="subtle text-2xs">Published</dt>
          <dd className="tnum">{source.published}</dd>
        </div>
        <div>
          <dt className="subtle text-2xs">Retrieved</dt>
          <dd className="tnum">{source.retrieved}</dd>
        </div>
        <div>
          <dt className="subtle text-2xs">Main harness</dt>
          <dd>{source.default_harness}</dd>
        </div>
      </dl>
      <ul className="muted mt-3 space-y-1 text-2xs leading-relaxed">
        {source.status && <li>· Status: {source.status}.</li>}
        {source.notes.map((note) => (
          <li key={note}>· {note}</li>
        ))}
      </ul>
    </div>
  );
}

// --------------------------------------------------------------------------- //
export default function PublishedResults() {
  const { data, isLoading, isError, error, refetch } = usePublishedResults();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const scope = (params.get("harness") === "all" ? "all" : "default") as "default" | "all";
  const limit = (params.get("show") === "all" ? "all" : "top") as "top" | "all";
  const lab = params.get("lab") ?? "";

  const dataset = data?.datasets[0];
  const update = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const filtered = useMemo(() => {
    if (!dataset) return [];
    const q = search.trim().toLowerCase();
    return dataset.rows.filter(
      (row) =>
        (scope === "all" || row.on_default_harness) &&
        (!lab || row.lab === lab) &&
        (!q || `${row.model} ${row.lab} ${row.harness}`.toLowerCase().includes(q)),
    );
  }, [dataset, scope, lab, search]);
  const plotted = limit === "top" ? filtered.slice(0, TOP_N) : filtered;
  const labs = useMemo(() => (dataset ? [...new Set(dataset.rows.map((row) => row.lab))].sort() : []), [dataset]);
  const tiedCount = dataset?.rows.filter((row) => row.on_default_harness && (row.harness_leader || row.tied_with_harness_leader)).length ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Published results"
        description="Frontier coding models as measured by a public leaderboard, with the source attached. Agent Labs did not run these models; use this to choose what to evaluate next, not to rank against your own experiments."
      />

      {isLoading && <LoadingRows rows={8} label="Loading published results" />}
      {isError && <ErrorState error={error} onRetry={() => void refetch()} />}
      {data && data.errors.length > 0 && (
        <Note tone="warn">Some dataset files were rejected and are not shown: {data.errors.join("; ")}</Note>
      )}
      {data && !dataset && (
        <EmptyState title="No published datasets" description="Add a sourced JSON file to benchmarks/external/ to show third-party results here." />
      )}

      {dataset && data && (
        <>
          <SourceCard dataset={dataset} notice={data.notice} />

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="surface elevated hairline rounded-xl border p-4">
              <p className="eyebrow">Highest published score</p>
              <p className="mt-2 text-[22px] font-semibold tracking-tight">{dataset.rows[0].model}</p>
              <p className="muted mt-1 text-xs">
                {dataset.rows[0].score_percent.toFixed(1)}% · {dataset.rows[0].solved_estimate}/{dataset.source.tasks} tasks · {dataset.rows[0].harness}
              </p>
            </div>
            <div className="surface elevated hairline rounded-xl border p-4">
              <p className="eyebrow">Tied for first</p>
              <p className="tnum mt-2 text-[22px] font-semibold tracking-tight">{tiedCount} models</p>
              <p className="muted mt-1 text-xs">Their 95% intervals overlap the leader's on {dataset.source.default_harness}; no single winner can be named.</p>
            </div>
            <div className="surface elevated hairline rounded-xl border p-4">
              <p className="eyebrow">Models listed</p>
              <p className="tnum mt-2 text-[22px] font-semibold tracking-tight">{dataset.rows.length}</p>
              <p className="muted mt-1 text-xs">
                {dataset.rows.filter((r) => r.on_default_harness).length} on {dataset.source.default_harness}, {dataset.rows.filter((r) => !r.on_default_harness).length} on other harnesses
              </p>
            </div>
          </div>

          <Card
            title="Score with derived 95% interval"
            subtitle={`${dataset.source.benchmark} · ${scope === "default" ? dataset.source.default_harness + " only" : "all harnesses (not directly comparable)"}`}
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <Segmented
                  label="Harness"
                  value={scope}
                  onChange={(value) => update("harness", value === "all" ? "all" : null)}
                  options={[
                    { value: "default", label: dataset.source.default_harness },
                    { value: "all", label: "All harnesses" },
                  ]}
                />
                <Segmented
                  label="Rows"
                  value={limit}
                  onChange={(value) => update("show", value === "all" ? "all" : null)}
                  options={[
                    { value: "top", label: `Top ${TOP_N}` },
                    { value: "all", label: "All" },
                  ]}
                />
              </div>
            }
          >
            <div className="flex flex-wrap items-center gap-2 px-4 pt-4">
              <SearchInput value={search} onChange={setSearch} placeholder="Search model or lab" className="w-full sm:w-64" />
              <Select
                label="Lab"
                value={lab}
                onChange={(value) => update("lab", value || null)}
                options={[{ value: "", label: "All labs" }, ...labs.map((name) => ({ value: name, label: name }))]}
              />
              <span className="subtle ml-auto text-2xs">
                {plotted.length} of {filtered.length} shown
              </span>
            </div>
            <div className="p-4">
              {plotted.length === 0 ? <p className="muted text-xs">No rows match.</p> : <IntervalPlot rows={plotted} />}
              {scope === "all" && (
                <Note tone="warn">
                  Rows on Claude Code, Codex, Cursor CLI or Factory measure a different agent around the model. Compare a row only with rows on the same harness.
                </Note>
              )}
            </div>
          </Card>

          <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
            <Card title="Score against cost" subtitle="Published cost per task · highlighted points are tied for first">
              <div className="p-4">
                <CostChart rows={filtered} />
              </div>
            </Card>
            <Card title="Full table" subtitle="Values copied verbatim from the source; — means the source shows none">
              <div className="max-h-[420px] overflow-y-auto">
                <Table>
                  <thead className="surface sticky top-0 z-10">
                    <tr>
                      <Th>#</Th>
                      <Th>Model</Th>
                      <Th>Lab</Th>
                      <Th className="text-right">Score</Th>
                      <Th className="text-right">Cost/task</Th>
                      <Th className="text-right">Time/task</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((row) => (
                      <tr key={`${row.rank}-${row.model}-${row.harness}`} className="row-hover">
                        <Td className="subtle tnum">{row.rank}</Td>
                        <Td>
                          <span className="font-medium">{row.model}</span>
                          {!row.on_default_harness && <span className="subtle ml-1.5 text-2xs">{row.harness}</span>}
                        </Td>
                        <Td className="muted whitespace-nowrap">{row.lab}</Td>
                        <Td className={cx("tnum text-right font-medium", (row.harness_leader || row.tied_with_harness_leader) && "accent-text")}>{row.score_percent.toFixed(1)}%</Td>
                        <Td className="tnum text-right">{row.cost_per_task_usd === null ? "—" : `$${row.cost_per_task_usd.toFixed(2)}`}</Td>
                        <Td className="tnum whitespace-nowrap text-right">{row.duration_per_task ?? "—"}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            </Card>
          </div>

          <Note>{dataset.interval_note}</Note>
        </>
      )}
    </div>
  );
}
