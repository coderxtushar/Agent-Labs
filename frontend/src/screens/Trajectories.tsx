import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useExperiments, useRuns } from "../api/hooks";
import type { Run, RunOutcome } from "../api/types";
import {
  OUTCOME_META,
  OutcomeBadge,
  SimulatedBadge,
  StateBadge,
  formatDuration,
  formatRelative,
  formatTime,
} from "../components/domain";
import {
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
} from "../components/ui";

const PAGE_SIZE = 50;
const OUTCOMES = Object.keys(OUTCOME_META) as RunOutcome[];
const STATES = [
  "pending",
  "claimed",
  "preparing",
  "agent_running",
  "collecting",
  "grading",
  "finalizing",
  "completed",
  "cancelled",
];

function matches(run: Run, needle: string): boolean {
  if (!needle) return true;
  return [run.id, run.task_key, run.task_title, run.agent.label, run.agent.model, run.termination_reason]
    .filter((value): value is string => typeof value === "string")
    .some((value) => value.toLowerCase().includes(needle));
}

function PatchCell({ run }: { run: Run }) {
  if (!run.patch_stats || run.patch_stats.files_changed === 0) {
    return <span className="subtle">{run.is_terminal ? "no patch" : "—"}</span>;
  }
  const stats = run.patch_stats;
  return (
    <span className="tnum whitespace-nowrap font-mono text-2xs">
      {stats.files_changed}f <span className="text-good">+{stats.insertions}</span>{" "}
      <span className="text-bad">−{stats.deletions}</span>
    </span>
  );
}

export default function Trajectories() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const outcome = params.get("outcome") ?? "";
  const state = params.get("state") ?? "";
  const experimentId = params.get("experiment") ?? "";
  const query = params.get("q") ?? "";
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const update = (patch: Record<string, string | null>, resetOffset = true) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const [key, value] of Object.entries(patch)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        if (resetOffset) next.delete("offset");
        return next;
      },
      { replace: true },
    );

  const runs = useRuns({
    outcome: outcome || undefined,
    state: state || undefined,
    experiment_id: experimentId || undefined,
    limit: PAGE_SIZE,
    offset,
  });
  const experiments = useExperiments();

  const needle = query.trim().toLowerCase();
  const rows = useMemo(() => (runs.data?.items ?? []).filter((run) => matches(run, needle)), [runs.data, needle]);
  const pagination = runs.data?.pagination;
  const hasFilters = Boolean(outcome || state || experimentId || query);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Trajectories"
        description="Every run's recorded event stream — model requests and responses, tool calls and results, file and command activity, retries, and grading — in the order the platform captured it."
        meta={
          pagination ? (
            <span className="muted tnum text-2xs">
              {pagination.total.toLocaleString()} run{pagination.total === 1 ? "" : "s"}
              {hasFilters && " matching server filters"}
            </span>
          ) : undefined
        }
      />

      <Note>
        A trajectory is the ordered record of events this platform actually captured for a run. It holds
        provider-visible output only; hidden model reasoning is not observable, and nothing missing from
        the record is reconstructed.
      </Note>

      <Card
        title="Runs"
        subtitle="Open a run to explore its trajectory, patch and grading"
        actions={
          hasFilters ? (
            <Button variant="ghost" onClick={() => update({ outcome: null, state: null, experiment: null, q: null })}>
              Clear filters
            </Button>
          ) : undefined
        }
      >
        <div className="hairline flex flex-wrap items-center gap-2 border-b p-3">
          <SearchInput
            value={query}
            onChange={(value) => update({ q: value || null }, false)}
            placeholder="Filter this page by task, agent, model, run id…"
            label="Filter runs on this page"
            className="min-w-[14rem] flex-1"
          />
          <Select
            label="Outcome"
            value={outcome}
            onChange={(value) => update({ outcome: value || null })}
            options={[
              { value: "", label: "Any outcome" },
              ...OUTCOMES.map((value) => ({
                value,
                label: `${OUTCOME_META[value].label}${OUTCOME_META[value].counted ? "" : " (excluded)"}`,
              })),
            ]}
          />
          <Select
            label="State"
            value={state}
            onChange={(value) => update({ state: value || null })}
            options={[
              { value: "", label: "Any state" },
              ...STATES.map((value) => ({ value, label: value.replace(/_/g, " ") })),
            ]}
          />
          <Select
            label="Experiment"
            value={experimentId}
            onChange={(value) => update({ experiment: value || null })}
            className="max-w-[16rem]"
            options={[
              { value: "", label: "All experiments" },
              ...(experiments.data?.items ?? []).map((experiment) => ({
                value: experiment.id,
                label: `${experiment.name}${experiment.agent.is_simulated ? " (simulated)" : ""}`,
              })),
            ]}
          />
        </div>

        {runs.isError && (
          <div className="p-4">
            <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
          </div>
        )}
        {runs.isLoading && <LoadingRows rows={8} label="Loading runs" />}

        {runs.data && rows.length === 0 && (
          <div className="p-4">
            <EmptyState
              title={hasFilters ? "No runs match" : "No runs yet"}
              description={
                hasFilters
                  ? needle && runs.data.items.length > 0
                    ? "The text filter applies to the current page only. Try another page or clear it."
                    : "Try widening the filters."
                  : "Launch an experiment to produce runs and their trajectories."
              }
              action={
                hasFilters ? (
                  <Button onClick={() => update({ outcome: null, state: null, experiment: null, q: null })}>
                    Clear filters
                  </Button>
                ) : (
                  <Link to="/experiments" className="accent-text text-xs font-medium">
                    Go to experiments →
                  </Link>
                )
              }
            />
          </div>
        )}

        {rows.length > 0 && (
          <Table>
            <thead>
              <tr>
                <Th>Run</Th>
                <Th>Outcome</Th>
                <Th>State</Th>
                <Th>Agent</Th>
                <Th>Task</Th>
                <Th className="text-right">Duration</Th>
                <Th className="text-right">Tokens</Th>
                <Th>Patch</Th>
                <Th className="text-right">Time</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((run) => {
                const tokens = run.usage?.reported_by_provider ? run.usage.total_tokens : null;
                const stamp = run.finished_at ?? run.started_at ?? run.created_at;
                return (
                  <tr
                    key={run.id}
                    onClick={(event) => {
                      if ((event.target as HTMLElement).closest("a")) return;
                      void navigate(`/runs/${run.id}`);
                    }}
                    className="row-hover cursor-pointer"
                  >
                    <Td>
                      <Link to={`/runs/${run.id}`} className="link font-mono text-2xs">
                        {run.id.slice(0, 10)}
                      </Link>
                      {run.attempt_index > 0 && <span className="subtle ml-1.5 text-2xs">#{run.attempt_index}</span>}
                      {run.infra_attempts > 0 && (
                        <span className="subtle ml-1.5 text-2xs" title="Infrastructure retries">
                          ↻{run.infra_attempts}
                        </span>
                      )}
                    </Td>
                    <Td>
                      <OutcomeBadge outcome={run.outcome} />
                    </Td>
                    <Td>
                      <StateBadge state={run.state} />
                    </Td>
                    <Td className="max-w-[14rem]">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Mono className="truncate">{run.agent.label}</Mono>
                        {run.agent.is_simulated && <SimulatedBadge compact />}
                      </div>
                      {run.agent.model && <p className="subtle mt-0.5 truncate font-mono text-2xs">{run.agent.model}</p>}
                    </Td>
                    <Td className="max-w-[16rem]">
                      <p className="truncate" title={run.task_title ?? undefined}>
                        {run.task_title ?? run.task_key}
                      </p>
                      <Mono className="subtle">{run.task_key}</Mono>
                    </Td>
                    <Td className="tnum whitespace-nowrap text-right">
                      {run.timings.total_s !== undefined ? formatDuration(run.timings.total_s) : <span className="subtle">—</span>}
                    </Td>
                    <Td className="tnum whitespace-nowrap text-right">
                      {tokens !== null && tokens !== undefined ? (
                        tokens.toLocaleString()
                      ) : (
                        <span className="subtle" title="The provider did not report token usage for this run">
                          not reported
                        </span>
                      )}
                    </Td>
                    <Td>
                      <PatchCell run={run} />
                    </Td>
                    <Td className="subtle whitespace-nowrap text-right text-2xs" title={formatTime(stamp)}>
                      {formatRelative(stamp)}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}

        {pagination && pagination.total > 0 && (
          <div className="hairline flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2.5">
            <p className="subtle tnum text-2xs">
              {pagination.offset + 1}–{Math.min(pagination.offset + pagination.limit, pagination.total)} of{" "}
              {pagination.total.toLocaleString()}
              {needle && ` · ${rows.length} shown after text filter`}
            </p>
            <div className="flex items-center gap-1.5">
              <Button
                disabled={offset === 0}
                onClick={() => update({ offset: offset - PAGE_SIZE > 0 ? String(offset - PAGE_SIZE) : null }, false)}
              >
                Previous
              </Button>
              <Button
                disabled={!pagination.has_more}
                onClick={() => update({ offset: String(offset + PAGE_SIZE) }, false)}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
