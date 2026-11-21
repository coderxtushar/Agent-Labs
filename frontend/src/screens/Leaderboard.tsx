import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useLeaderboard } from "../api/hooks";
import type { Leaderboard as LeaderboardData, LeaderboardRow, ProvenanceValue, Rate } from "../api/types";
import { RateBar } from "../components/charts";
import { ProvenanceBadge, WarningList, formatDuration, formatRelative } from "../components/domain";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingRows,
  Mono,
  Note,
  PageHeader,
  SearchInput,
  Select,
  Table,
  Td,
  Th,
  cx,
  type BadgeTone,
} from "../components/ui";

// --------------------------------------------------------------------------- //
// local helpers
// --------------------------------------------------------------------------- //
const PROVENANCE_TONES: Record<ProvenanceValue, BadgeTone> = {
  simulated: "warn",
  offline_adapter_test: "warn",
  live_model: "good",
  external_agent: "info",
};

function percentText(rate: Rate): string {
  if (rate.percent === null) return "—";
  return `${Number.isInteger(rate.percent) ? rate.percent : rate.percent.toFixed(1)}%`;
}

function ciText(rate: Rate): string | null {
  if (rate.denominator === 0 || rate.ci_low === null || rate.ci_high === null) return null;
  return `95% CI ${(rate.ci_low * 100).toFixed(0)}–${(rate.ci_high * 100).toFixed(0)}%`;
}

/** A secondary rate in a dense table cell: percent with its counts, never bare. */
function RateCell({ rate }: { rate: Rate }) {
  if (rate.denominator === 0) return <span className="subtle">—</span>;
  return (
    <span className="tnum whitespace-nowrap" title={ciText(rate) ?? undefined}>
      {percentText(rate)}
      <span className="subtle ml-1">
        ({rate.numerator}/{rate.denominator})
      </span>
    </span>
  );
}

type SortKey =
  | "position"
  | "label"
  | "attempted"
  | "passed"
  | "success"
  | "mean"
  | "median"
  | "timeout"
  | "error"
  | "experiments"
  | "excluded";

const SORT_ACCESSORS: Record<SortKey, (row: LeaderboardRow) => number | string | null> = {
  position: (row) => row.position,
  label: (row) => row.label.toLowerCase(),
  attempted: (row) => row.tasks_attempted,
  passed: (row) => row.tasks_passed,
  success: (row) => row.success_rate.value,
  mean: (row) => row.runtime.mean_s,
  median: (row) => row.runtime.median_s,
  timeout: (row) => row.timeout_rate.value,
  error: (row) => row.error_rate.value,
  experiments: (row) => row.experiment_count,
  excluded: (row) => row.excluded_runs,
};

const SORT_KEYS = Object.keys(SORT_ACCESSORS) as SortKey[];

function sortRows(rows: LeaderboardRow[], key: SortKey, dir: "asc" | "desc"): LeaderboardRow[] {
  if (key === "position" && dir === "asc") return rows;
  const accessor = SORT_ACCESSORS[key];
  return [...rows].sort((a, b) => {
    const left = accessor(a);
    const right = accessor(b);
    // Unknown values always sort last, whichever direction is chosen.
    if (left === null && right === null) return a.position - b.position;
    if (left === null) return 1;
    if (right === null) return -1;
    const cmp = left < right ? -1 : left > right ? 1 : 0;
    return (dir === "asc" ? cmp : -cmp) || a.position - b.position;
  });
}

function SortHeader({
  label,
  sortKey,
  active,
  dir,
  onSort,
  align = "left",
  title,
}: {
  label: string;
  sortKey: SortKey;
  active: boolean;
  dir: "asc" | "desc";
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
  title?: string;
}) {
  return (
    <Th className={align === "right" ? "text-right" : undefined}>
      <button
        type="button"
        title={title ?? `Sort by ${label.toLowerCase()}`}
        onClick={() => onSort(sortKey)}
        aria-label={`Sort by ${label}${active ? (dir === "asc" ? ", ascending" : ", descending") : ""}`}
        className={cx(
          "inline-flex items-center gap-1 uppercase tracking-[0.06em] transition-colors hover:text-[rgb(var(--text))]",
          active && "text-[rgb(var(--text))]",
        )}
      >
        {label}
        <span aria-hidden className={cx("text-[9px]", !active && "opacity-0")}>
          {dir === "asc" ? "▲" : "▼"}
        </span>
      </button>
    </Th>
  );
}

// --------------------------------------------------------------------------- //
type ExperimentOption = LeaderboardData["filters"]["experiments"][number];

function ExperimentPicker({
  options,
  selected,
  onChange,
}: {
  options: ExperimentOption[];
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const needle = query.trim().toLowerCase();
  const visible = needle
    ? options.filter((option) =>
        [option.name, option.agent, option.benchmark].some((field) => field.toLowerCase().includes(needle)),
      )
    : options;

  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((entry) => entry !== id) : [...selected, id]);

  return (
    <div ref={ref} className="relative">
      <Button onClick={() => setOpen((value) => !value)} title="Limit the leaderboard to specific experiments">
        <span>Experiments</span>
        <span className="tnum subtle">{selected.length === 0 ? "all" : `${selected.length} selected`}</span>
        <span aria-hidden className="subtle text-[9px]">
          ▼
        </span>
      </Button>
      {open && (
        <div
          role="dialog"
          aria-label="Select experiments"
          className="surface hairline fade-in absolute left-0 z-30 mt-1 w-[min(26rem,calc(100vw-2rem))] rounded-lg border shadow-2xl"
        >
          <div className="hairline space-y-2 border-b p-2">
            <SearchInput value={query} onChange={setQuery} placeholder="Filter experiments" label="Filter experiments" />
            <div className="flex items-center justify-between">
              <span className="subtle text-2xs">
                {options.length} experiment(s) · none selected means all
              </span>
              <Button variant="ghost" onClick={() => onChange([])} disabled={selected.length === 0}>
                Clear
              </Button>
            </div>
          </div>
          <ul className="max-h-72 overflow-y-auto p-1">
            {visible.length === 0 && <li className="muted p-2 text-xs">No experiments match.</li>}
            {visible.map((option) => (
              <li key={option.id}>
                <label className="row-hover flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-xs transition-colors">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={selected.includes(option.id)}
                    onChange={() => toggle(option.id)}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{option.name}</span>
                    <span className="subtle flex flex-wrap items-center gap-x-1.5 text-2xs">
                      <Mono>{option.agent}</Mono>
                      <span aria-hidden>·</span>
                      <Mono>{option.benchmark}</Mono>
                      <span aria-hidden>·</span>
                      {formatRelative(option.created_at)}
                    </span>
                  </span>
                  <Badge tone={PROVENANCE_TONES[option.provenance] ?? "neutral"}>
                    {option.provenance.replace(/_/g, " ")}
                  </Badge>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  children: ReactNode;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-xs">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx(
          "hairline relative h-4 w-7 rounded-full border transition-colors duration-150",
          checked ? "accent-bg" : "surface-3",
        )}
      >
        <span
          className={cx(
            "absolute top-1/2 h-2.5 w-2.5 -translate-y-1/2 rounded-full bg-[rgb(var(--surface))] shadow transition-[left] duration-150",
            checked ? "left-[14px]" : "left-[2px]",
          )}
        />
      </button>
      <span className="muted">{children}</span>
    </label>
  );
}

// --------------------------------------------------------------------------- //
export default function Leaderboard() {
  const [params, setParams] = useSearchParams();
  const suite = params.get("suite") ?? "";
  const experimentIds = useMemo(
    () => (params.get("exp") ?? "").split(",").filter(Boolean),
    [params],
  );
  const includeNonModel = params.get("all") === "1";
  const sortParam = params.get("sort") as SortKey | null;
  const sortKey: SortKey = sortParam && SORT_KEYS.includes(sortParam) ? sortParam : "position";
  const sortDir: "asc" | "desc" = params.get("dir") === "desc" ? "desc" : "asc";

  const update = (changes: Record<string, string | null>) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const [key, value] of Object.entries(changes)) {
          if (value === null || value === "") next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace: true },
    );

  const board = useLeaderboard({
    benchmark_id: suite || undefined,
    experiment_ids: experimentIds.length > 0 ? experimentIds : undefined,
    include_non_model: includeNonModel || undefined,
  });

  const onSort = (key: SortKey) => {
    if (key === sortKey) {
      update({ dir: sortDir === "asc" ? "desc" : null });
    } else {
      // Rates and counts read best high-first; names and positions low-first.
      const ascending = key === "position" || key === "label" || key === "mean" || key === "median";
      update({ sort: key === "position" ? null : key, dir: ascending ? null : "desc" });
    }
  };

  const data = board.data;
  const rows = data ? sortRows(data.rows, sortKey, sortDir) : [];
  const experimentOptions = data?.filters.experiments ?? [];
  const suiteOptions = [
    { value: "", label: "All benchmark suites" },
    ...(data?.filters.benchmarks ?? (suite ? [suite] : [])).map((b) => ({ value: b, label: b })),
  ];
  const sortProps = { dir: sortDir, onSort };

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Leaderboard"
        description="One row per agent configuration and result provenance. Every rate shows the runs it was computed from and its 95% interval; ordering is by observed success and is not a significance claim."
        meta={data ? <span className="subtle text-2xs">computed {formatRelative(data.computed_at)}</span> : undefined}
      />

      <Card>
        <div className="flex flex-wrap items-center gap-3 p-3">
          <Select
            label="Benchmark suite"
            value={suite}
            onChange={(value) => update({ suite: value || null })}
            options={suiteOptions}
          />
          <ExperimentPicker
            options={experimentOptions}
            selected={experimentIds}
            onChange={(ids) => update({ exp: ids.length > 0 ? ids.join(",") : null })}
          />
          <Toggle checked={includeNonModel} onChange={(value) => update({ all: value ? "1" : null })}>
            Include simulated / offline results
          </Toggle>
          {(suite || experimentIds.length > 0 || includeNonModel || sortKey !== "position") && (
            <Button variant="ghost" onClick={() => setParams(new URLSearchParams(), { replace: true })}>
              Reset
            </Button>
          )}
          {data && !data.include_non_model && data.hidden_non_model_experiments > 0 && (
            <span className="subtle ml-auto text-2xs">
              {data.hidden_non_model_experiments} non-model experiment(s) hidden
            </span>
          )}
        </div>
      </Card>

      {board.isError && <ErrorState error={board.error} onRetry={() => void board.refetch()} />}

      {data && (
        <div className="space-y-2">
          <Note>{data.ordering_note}</Note>
          <Note>{data.runtime_note}</Note>
          {data.include_non_model && (
            <Note tone="warn">
              Simulated and offline rows are shown for pipeline inspection only. They are not model
              scores; their provenance badge says so on every row.
            </Note>
          )}
        </div>
      )}
      {data && <WarningList warnings={data.warnings} title="Read with care" />}

      <Card
        title="Rankings"
        subtitle={
          data
            ? `${data.rows.length} row(s)${suite ? ` · ${suite}` : ""}${experimentIds.length > 0 ? ` · ${experimentIds.length} experiment(s)` : ""}`
            : undefined
        }
      >
        {board.isLoading && <LoadingRows rows={5} label="Loading leaderboard" />}
        {data && data.rows.length === 0 && (
          <div className="p-4">
            <EmptyState
              title="No rows match these filters"
              description={
                !data.include_non_model && data.hidden_non_model_experiments > 0
                  ? `${data.hidden_non_model_experiments} non-model experiment(s) are hidden. Turn on “Include simulated / offline results” to inspect them; they are not model scores.`
                  : "Run an experiment to completion to see it ranked here."
              }
              action={
                <Link to="/experiments" className="accent-text text-xs font-medium">
                  Go to experiments →
                </Link>
              }
            />
          </div>
        )}
        {data && data.rows.length > 0 && (
          <Table>
            <thead>
              <tr>
                <SortHeader label="#" sortKey="position" active={sortKey === "position"} {...sortProps} />
                <SortHeader label="Agent" sortKey="label" active={sortKey === "label"} {...sortProps} />
                <Th>Provenance</Th>
                <Th>Suite</Th>
                <SortHeader
                  label="Tasks"
                  sortKey="attempted"
                  active={sortKey === "attempted"}
                  align="right"
                  title="Distinct tasks with at least one attributable attempt"
                  {...sortProps}
                />
                <SortHeader
                  label="Passed"
                  sortKey="passed"
                  active={sortKey === "passed"}
                  align="right"
                  title="Distinct tasks solved at least once"
                  {...sortProps}
                />
                <SortHeader label="Success rate" sortKey="success" active={sortKey === "success"} {...sortProps} />
                <SortHeader label="Avg" sortKey="mean" active={sortKey === "mean"} align="right" {...sortProps} />
                <SortHeader label="Median" sortKey="median" active={sortKey === "median"} align="right" {...sortProps} />
                <SortHeader label="Timeout" sortKey="timeout" active={sortKey === "timeout"} align="right" {...sortProps} />
                <SortHeader
                  label="Agent error"
                  sortKey="error"
                  active={sortKey === "error"}
                  align="right"
                  {...sortProps}
                />
                <SortHeader
                  label="Exps"
                  sortKey="experiments"
                  active={sortKey === "experiments"}
                  align="right"
                  title="Experiments contributing to this row"
                  {...sortProps}
                />
                <SortHeader
                  label="Excluded"
                  sortKey="excluded"
                  active={sortKey === "excluded"}
                  align="right"
                  title="Infra, invalid-task, grading-error and cancelled runs; not failures and not in any rate"
                  {...sortProps}
                />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.agent_config_id}:${row.provenance.value}`} className="row-hover transition-colors">
                  <Td className="tnum subtle">{row.position}</Td>
                  <Td className="min-w-[12rem]">
                    <Link to={`/leaderboard/${row.agent_config_id}`} className="link font-medium">
                      {row.label}
                    </Link>
                    <Mono className="subtle mt-0.5 block">
                      {row.model ?? "model not recorded"}
                      {row.adapter ? ` · ${row.adapter}` : ""}
                    </Mono>
                  </Td>
                  <Td>
                    <ProvenanceBadge provenance={row.provenance} compact />
                  </Td>
                  <Td>
                    <div className="flex flex-col gap-0.5">
                      {row.benchmarks.map((benchmark) => (
                        <Mono key={benchmark}>{benchmark}</Mono>
                      ))}
                    </div>
                  </Td>
                  <Td className="tnum text-right">{row.tasks_attempted}</Td>
                  <Td className="tnum text-right">{row.tasks_passed}</Td>
                  <Td className="min-w-[15rem]">
                    {row.success_rate.denominator === 0 ? (
                      <span className="subtle">no attributable runs</span>
                    ) : (
                      <div className="space-y-1">
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="tnum whitespace-nowrap font-medium">
                            {percentText(row.success_rate)}
                            <span className="muted ml-1 font-normal">
                              ({row.success_rate.numerator}/{row.success_rate.denominator})
                            </span>
                          </span>
                          <span className="subtle tnum whitespace-nowrap text-2xs">{ciText(row.success_rate)}</span>
                        </div>
                        <RateBar rate={row.success_rate} />
                        {row.ci_overlaps_leader === true && (
                          <p
                            className="subtle text-2xs"
                            title="This row's 95% interval overlaps the leader's, so the two are not distinguishable."
                          >
                            ≈ leader (CIs overlap)
                          </p>
                        )}
                      </div>
                    )}
                  </Td>
                  <Td className="tnum whitespace-nowrap text-right">{formatDuration(row.runtime.mean_s)}</Td>
                  <Td className="tnum whitespace-nowrap text-right" title={`n = ${row.runtime.n} timed run(s)`}>
                    {formatDuration(row.runtime.median_s)}
                  </Td>
                  <Td className="text-right">
                    <RateCell rate={row.timeout_rate} />
                  </Td>
                  <Td className="text-right">
                    <RateCell rate={row.error_rate} />
                  </Td>
                  <Td className="tnum text-right">{row.experiment_count}</Td>
                  <Td className={cx("tnum text-right", row.excluded_runs === 0 && "subtle")}>
                    {row.excluded_runs}
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
