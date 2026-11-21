import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useCompareMatrix, useExperiments } from "../api/hooks";
import type { CompareMatrix, MatrixCell, Rate, RunOutcome } from "../api/types";
import { OutcomeStack, RateBar } from "../components/charts";
import {
  BlockerList,
  COUNTED_OUTCOMES,
  EXCLUDED_OUTCOMES,
  OUTCOME_META,
  ProvenanceBadge,
  WarningList,
  formatDuration,
  formatRelative,
} from "../components/domain";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  SearchInput,
  Segmented,
  Table,
  Td,
  Th,
  cx,
} from "../components/ui";
import Comparisons from "./Comparisons";

// --------------------------------------------------------------------------- //
// local helpers
// --------------------------------------------------------------------------- //
const MAX_ARMS = 6;
const MIN_RUNS_FOR_INFERENCE = 30;
const ARM_LETTERS = ["A", "B", "C", "D", "E", "F"];

type Mode = "multi" | "pairwise";
type Arm = CompareMatrix["arms"][number];

function percentText(rate: Rate): string {
  if (rate.percent === null) return "—";
  return `${Number.isInteger(rate.percent) ? rate.percent : rate.percent.toFixed(1)}%`;
}

function ciText(rate: Rate): string | null {
  if (rate.denominator === 0 || rate.ci_low === null || rate.ci_high === null) return null;
  return `95% CI ${(rate.ci_low * 100).toFixed(0)}–${(rate.ci_high * 100).toFixed(0)}%`;
}

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

/** The outcome a cell saw most often; ties resolve in COUNTED-then-EXCLUDED order. */
function dominantOutcome(cell: MatrixCell): RunOutcome | null {
  let best: RunOutcome | null = null;
  let bestCount = 0;
  for (const outcome of [...COUNTED_OUTCOMES, ...EXCLUDED_OUTCOMES]) {
    const n = cell.outcomes[outcome] ?? 0;
    if (n > bestCount) {
      best = outcome;
      bestCount = n;
    }
  }
  return best;
}

function cellTitle(cell: MatrixCell): string {
  const parts = (Object.entries(cell.outcomes) as [RunOutcome, number][])
    .filter(([, n]) => n > 0)
    .map(([outcome, n]) => `${OUTCOME_META[outcome].label} ${n}`);
  const duration = cell.mean_duration_s !== null ? ` · mean ${formatDuration(cell.mean_duration_s)}` : "";
  return `${cell.solved}/${cell.attributable} attributable solved · ${cell.attempts} attempt(s)${duration}${
    parts.length ? `\n${parts.join(" · ")}` : ""
  }`;
}

function MatrixChip({ cell }: { cell: MatrixCell | null }) {
  if (!cell) return <span className="subtle text-2xs italic">not in this arm</span>;
  const dominant = dominantOutcome(cell);
  const color = dominant ? OUTCOME_META[dominant].color : null;
  const excluded = cell.attempts - cell.attributable;
  return (
    <span
      title={cellTitle(cell)}
      className="tnum inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-2xs font-medium"
      style={
        color
          ? { backgroundColor: `${color}1f`, borderColor: `${color}59`, color: "rgb(var(--text))" }
          : { borderColor: "rgb(var(--border))" }
      }
    >
      {color && <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: color }} aria-hidden />}
      {cell.attributable > 0 ? `${cell.solved}/${cell.attributable}` : "0 attributable"}
      {excluded > 0 && <span className="subtle font-normal">+{excluded} excl.</span>}
      {dominant && <span className="sr-only">mostly {OUTCOME_META[dominant].label}</span>}
    </span>
  );
}

// --------------------------------------------------------------------------- //
// picker
// --------------------------------------------------------------------------- //
function ArmPicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const experiments = useExperiments();
  const [query, setQuery] = useState("");
  const items = experiments.data?.items ?? [];
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? items.filter((experiment) =>
        [
          experiment.name,
          experiment.agent.label,
          experiment.agent.model ?? "",
          `${experiment.benchmark_id}@${experiment.benchmark_version}`,
        ].some((field) => field.toLowerCase().includes(needle)),
      )
    : items;
  const full = selected.length >= MAX_ARMS;

  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((entry) => entry !== id) : [...selected, id]);

  return (
    <Card
      title="Arms"
      subtitle={`Pick 2–${MAX_ARMS} experiments. Comparability is checked, not assumed.`}
      actions={
        <>
          <span className={cx("tnum text-2xs", full ? "text-warn" : "subtle")}>
            {selected.length}/{MAX_ARMS} selected
          </span>
          <Button variant="ghost" onClick={() => onChange([])} disabled={selected.length === 0}>
            Clear
          </Button>
        </>
      }
    >
      <div className="space-y-2 p-3">
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder="Search name, agent, model or benchmark"
          label="Search experiments to compare"
        />
        {experiments.isError && <ErrorState error={experiments.error} onRetry={() => void experiments.refetch()} />}
        {experiments.isLoading && <LoadingRows rows={4} label="Loading experiments" />}
        {experiments.data && items.length === 0 && (
          <p className="muted p-2 text-xs">
            No experiments yet. <Link to="/experiments" className="link">Create one</Link> first.
          </p>
        )}
        {experiments.data && items.length > 0 && (
          <ul className="hairline max-h-[19.5rem] overflow-y-auto rounded-md border p-1">
            {visible.length === 0 && <li className="muted p-2 text-xs">No experiments match.</li>}
            {visible.map((experiment) => {
              const index = selected.indexOf(experiment.id);
              const checked = index >= 0;
              const disabled = !checked && full;
              return (
                <li key={experiment.id}>
                  <label
                    className={cx(
                      "flex items-start gap-2 rounded-md px-2 py-1.5 text-xs transition-colors",
                      disabled ? "cursor-not-allowed opacity-50" : "row-hover cursor-pointer",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => toggle(experiment.id)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        {checked && (
                          <span className="accent-soft-bg accent-text rounded px-1 text-2xs font-semibold">
                            {ARM_LETTERS[index]}
                          </span>
                        )}
                        <span className="truncate font-medium">{experiment.name}</span>
                      </span>
                      <span className="subtle flex flex-wrap items-center gap-x-1.5 text-2xs">
                        <Mono>{experiment.agent.label}</Mono>
                        <span aria-hidden>·</span>
                        <Mono>
                          {experiment.benchmark_id}@{experiment.benchmark_version}
                        </Mono>
                        <span aria-hidden>·</span>
                        {experiment.progress
                          ? `${experiment.progress.finished}/${experiment.progress.total} runs`
                          : experiment.state}
                        <span aria-hidden>·</span>
                        {formatRelative(experiment.created_at)}
                      </span>
                    </span>
                    <ProvenanceBadge provenance={experiment.provenance} compact />
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// results
// --------------------------------------------------------------------------- //
function StatisticalNote({ data }: { data: CompareMatrix }) {
  const note = data.statistical_note;
  const smallArms = data.arms.filter((arm) => arm.success_rate_attributable.denominator < MIN_RUNS_FOR_INFERENCE);
  return (
    <Card title="Statistical note">
      <div className="space-y-3 p-4 text-xs">
        {(note.note || note.reason) && <p className="muted leading-relaxed">{note.note ?? note.reason}</p>}
        {note.note && note.reason && <p className="muted leading-relaxed">{note.reason}</p>}
        {note.p_value !== undefined && (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
            <div>
              <dt className="eyebrow">Test</dt>
              <dd className="mt-0.5">{note.test ?? "—"}</dd>
            </div>
            <div>
              <dt className="eyebrow">z</dt>
              <dd className="tnum mt-0.5">{note.z !== undefined ? note.z : "—"}</dd>
            </div>
            <div>
              <dt className="eyebrow">p-value</dt>
              <dd className="tnum mt-0.5">{note.p_value}</dd>
            </div>
            <div>
              <dt className="eyebrow">Below α = 0.05</dt>
              <dd className="mt-0.5">{note.below_alpha === undefined ? "—" : String(note.below_alpha)}</dd>
            </div>
          </dl>
        )}
        {smallArms.length > 0 && (
          <Note tone="warn">
            {smallArms.length === data.arms.length ? "Every arm has" : `${smallArms.length} arm(s) have`} fewer than{" "}
            {MIN_RUNS_FOR_INFERENCE} attributable runs. Differences between arms are indicative only.
          </Note>
        )}
        <p className="text-warn text-2xs">
          {note.claims_significance
            ? "The API marks this result as a significance claim; read it together with the note above."
            : "No statistical significance is claimed for this comparison."}
        </p>
      </div>
    </Card>
  );
}

function ArmsTable({ arms }: { arms: Arm[] }) {
  return (
    <Table>
      <thead>
        <tr>
          <Th>Arm</Th>
          <Th>Model</Th>
          <Th>Provenance</Th>
          <Th>Success rate</Th>
          <Th className="text-right">Timeout</Th>
          <Th className="text-right">Agent error</Th>
          <Th className="text-right">Mean</Th>
          <Th className="text-right">Median</Th>
          <Th>Outcomes</Th>
          <Th>Failure categories</Th>
        </tr>
      </thead>
      <tbody>
        {arms.map((arm, index) => {
          const categories = Object.entries(arm.failure_categories).sort((a, b) => b[1] - a[1]);
          return (
            <tr key={arm.experiment_id} className="row-hover transition-colors">
              <Td className="min-w-[12rem]">
                <div className="flex items-start gap-2">
                  <span className="accent-soft-bg accent-text mt-0.5 rounded px-1 text-2xs font-semibold">
                    {ARM_LETTERS[index]}
                  </span>
                  <div className="min-w-0">
                    <Link to={`/experiments/${arm.experiment_id}`} className="link font-medium">
                      {arm.name}
                    </Link>
                    <Mono className="subtle mt-0.5 block">
                      {arm.agent} · {arm.benchmark} · {arm.repetitions} rep(s)
                    </Mono>
                    {!arm.completeness.is_complete && (
                      <p className="text-warn mt-0.5 text-2xs">
                        incomplete · {arm.completeness.finished_runs}/{arm.completeness.total_runs} finished
                      </p>
                    )}
                  </div>
                </div>
              </Td>
              <Td>
                <Mono className="whitespace-nowrap">{arm.model ?? "—"}</Mono>
              </Td>
              <Td>
                <ProvenanceBadge provenance={arm.provenance} compact />
              </Td>
              <Td className="min-w-[11rem]">
                {arm.success_rate_attributable.denominator === 0 ? (
                  <span className="subtle">no attributable runs</span>
                ) : (
                  <div className="space-y-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="tnum font-medium">
                        {percentText(arm.success_rate_attributable)}
                        <span className="muted ml-1 font-normal">
                          ({arm.success_rate_attributable.numerator}/{arm.success_rate_attributable.denominator})
                        </span>
                      </span>
                      <span className="subtle tnum text-2xs">{ciText(arm.success_rate_attributable)}</span>
                    </div>
                    <RateBar rate={arm.success_rate_attributable} />
                  </div>
                )}
              </Td>
              <Td className="text-right">
                <RateCell rate={arm.timeout_rate} />
              </Td>
              <Td className="text-right">
                <RateCell rate={arm.error_rate} />
              </Td>
              <Td className="tnum whitespace-nowrap text-right">{formatDuration(arm.runtime.mean_s)}</Td>
              <Td className="tnum whitespace-nowrap text-right">{formatDuration(arm.runtime.median_s)}</Td>
              <Td className="min-w-[8rem]">
                <OutcomeStack outcomes={arm.outcomes} />
              </Td>
              <Td>
                {categories.length === 0 ? (
                  <span className="subtle text-2xs">none</span>
                ) : (
                  <div className="flex flex-wrap gap-1">
                    {categories.slice(0, 3).map(([category, n]) => (
                      <Badge key={category}>
                        {category.replace(/_/g, " ")} <span className="tnum opacity-70">{n}</span>
                      </Badge>
                    ))}
                    {categories.length > 3 && <span className="subtle text-2xs">+{categories.length - 3} more</span>}
                  </div>
                )}
              </Td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

function TaskMatrix({ data }: { data: CompareMatrix }) {
  const present = [...COUNTED_OUTCOMES, ...EXCLUDED_OUTCOMES].filter((outcome) =>
    data.tasks.some((task) => task.cells.some((cell) => (cell?.outcomes[outcome] ?? 0) > 0)),
  );
  return (
    <>
      <Table>
        <thead>
          <tr>
            <Th>Task</Th>
            {data.arms.map((arm, index) => (
              <Th key={arm.experiment_id}>
                <span title={`${arm.name} · ${arm.agent}`} className="inline-flex max-w-[10rem] items-center gap-1.5">
                  <span className="accent-text font-semibold">{ARM_LETTERS[index]}</span>
                  <span className="truncate normal-case tracking-normal">{arm.agent}</span>
                </span>
              </Th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.tasks.map((task) => (
            <tr key={task.task_key} className="row-hover transition-colors">
              <Td className="min-w-[12rem]">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Link to={`/benchmarks/tasks/${encodeURIComponent(task.task_key)}`} className="link font-mono text-2xs">
                    {task.task_key}
                  </Link>
                  {!task.shared && (
                    <Badge tone="warn" title="Not every arm ran this task; it cannot be compared across all arms">
                      not shared
                    </Badge>
                  )}
                </div>
                {(task.title || task.category) && (
                  <p className="subtle mt-0.5 truncate text-2xs">
                    {task.title}
                    {task.title && task.category && " · "}
                    {task.category}
                  </p>
                )}
              </Td>
              {task.cells.map((cell, index) => (
                <Td key={data.arms[index]?.experiment_id ?? index}>
                  <MatrixChip cell={cell} />
                </Td>
              ))}
            </tr>
          ))}
        </tbody>
      </Table>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
        <span className="subtle text-2xs">Cell: solved / attributable attempts, coloured by most frequent outcome.</span>
        {present.map((outcome) => (
          <span key={outcome} className="muted inline-flex items-center gap-1.5 text-2xs">
            <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: OUTCOME_META[outcome].color }} />
            {OUTCOME_META[outcome].label}
            {!OUTCOME_META[outcome].counted && <span className="subtle">· excluded</span>}
          </span>
        ))}
      </div>
    </>
  );
}

function MultiArm() {
  const [params, setParams] = useSearchParams();
  const ids = useMemo(
    () =>
      Array.from(new Set((params.get("ids") ?? "").split(",").filter(Boolean))).slice(0, MAX_ARMS),
    [params],
  );
  const matrix = useCompareMatrix(ids);

  const setIds = (next: string[]) =>
    setParams(
      (current) => {
        const out = new URLSearchParams(current);
        if (next.length > 0) out.set("ids", next.join(","));
        else out.delete("ids");
        return out;
      },
      { replace: true },
    );

  const data = matrix.data;
  return (
    <div className="space-y-6">
      <ArmPicker selected={ids} onChange={setIds} />

      {ids.length < 2 && (
        <EmptyState
          title="Pick at least two experiments"
          description="Arms are compared over their shared task set. Differing task sets, grading versions, benchmark versions or provenance are reported as blockers rather than silently averaged."
        />
      )}

      {ids.length >= 2 && matrix.isError && (
        <ErrorState error={matrix.error} onRetry={() => void matrix.refetch()} />
      )}
      {ids.length >= 2 && matrix.isLoading && <LoadingTiles count={3} />}

      {ids.length >= 2 && data && (
        <div className="fade-in space-y-6">
          {(data.blockers.length > 0 || data.caveats.length > 0) && (
            <div className="space-y-3">
              <BlockerList blockers={data.blockers} />
              <WarningList warnings={data.caveats} title="Caveats" />
            </div>
          )}
          {data.comparable && (
            <Note tone="info">
              Arms share benchmark, grading version, provenance and task set ({data.shared_task_count} task(s)).
            </Note>
          )}

          <Card title="Arms summary" subtitle={`${data.arms.length} arm(s) · computed ${formatRelative(data.computed_at)}`}>
            <ArmsTable arms={data.arms} />
          </Card>

          <Card
            title="Task-level matrix"
            subtitle={`${data.tasks.length} task(s) · ${data.shared_task_count} shared by every arm`}
          >
            {data.tasks.length === 0 ? (
              <p className="muted p-4 text-xs">These arms have no task results yet.</p>
            ) : (
              <TaskMatrix data={data} />
            )}
          </Card>

          <StatisticalNote data={data} />
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
export default function CompareStudio() {
  const [params, setParams] = useSearchParams();
  const mode: Mode = params.get("mode") === "pairwise" ? "pairwise" : "multi";

  const setMode = (next: Mode) =>
    setParams(
      (current) => {
        const out = new URLSearchParams(current);
        if (next === "pairwise") out.set("mode", "pairwise");
        else out.delete("mode");
        return out;
      },
      { replace: true },
    );

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Compare"
        description={
          mode === "multi"
            ? "Side-by-side view of up to six experiments: arm summaries with intervals and a task-by-task matrix. No significance test is run across more than two arms."
            : "Baseline versus candidate with per-task movement. A net-zero delta can hide fixes and breakages, so both are listed."
        }
        actions={
          <Segmented<Mode>
            label="Comparison mode"
            value={mode}
            onChange={setMode}
            options={[
              { value: "multi", label: "Multi-arm", title: "Compare 2–6 experiments" },
              { value: "pairwise", label: "Pairwise", title: "Baseline vs candidate with task movement" },
            ]}
          />
        }
      />
      {mode === "multi" ? <MultiArm /> : <Comparisons embedded />}
    </div>
  );
}
