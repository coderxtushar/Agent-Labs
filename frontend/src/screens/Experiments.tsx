import { useState, type MouseEvent, useDeferredValue } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useAgents, useCreateExperiment, useExperiments, useTasks } from "../api/hooks";
import type { Experiment, ExperimentState, ProvenanceValue, RunOutcome } from "../api/types";
import {
  COUNTED_OUTCOMES,
  EXCLUDED_OUTCOMES,
  OUTCOME_META,
  ProvenanceBadge,
  SimulatedBadge,
  formatDuration,
  formatRelative,
  formatTime,
} from "../components/domain";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  LoadingRows,
  Modal,
  Mono,
  PageHeader,
  ProgressBar,
  SearchInput,
  Select,
  Table,
  Td,
  Th,
  inputClass,
  type BadgeTone,
} from "../components/ui";

// --------------------------------------------------------------------------- //
// local helpers for the explorer
// --------------------------------------------------------------------------- //
const STATES: ExperimentState[] = ["draft", "queued", "running", "paused", "completed", "cancelled"];

const STATE_TONES: Record<ExperimentState, BadgeTone> = {
  draft: "neutral",
  queued: "info",
  running: "info",
  paused: "warn",
  completed: "accent",
  cancelled: "neutral",
};

type SortOption = "newest" | "oldest" | "name" | "success" | "progress" | "duration";

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "name", label: "Name A–Z" },
  { value: "success", label: "Success rate (high first)" },
  { value: "progress", label: "Progress (high first)" },
  { value: "duration", label: "Duration (longest first)" },
];

/** solved / attributable outcomes, from progress counts. `null` when nothing is attributable. */
function successOf(experiment: Experiment): { solved: number; attributable: number; excluded: number } | null {
  const byOutcome = experiment.progress?.by_outcome;
  if (!byOutcome) return null;
  const attributable = COUNTED_OUTCOMES.reduce((sum, outcome) => sum + (byOutcome[outcome] ?? 0), 0);
  const excluded = EXCLUDED_OUTCOMES.reduce((sum, outcome) => sum + (byOutcome[outcome] ?? 0), 0);
  return { solved: byOutcome.solved ?? 0, attributable, excluded };
}

function successValue(experiment: Experiment): number | null {
  const success = successOf(experiment);
  return success && success.attributable > 0 ? success.solved / success.attributable : null;
}

/** Wall-clock execution time; `null` when the experiment has not both started and finished. */
function durationOf(experiment: Experiment): number | null {
  if (!experiment.started_at || !experiment.finished_at) return null;
  const seconds = (new Date(experiment.finished_at).getTime() - new Date(experiment.started_at).getTime()) / 1000;
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

function timestamp(iso: string | null): number {
  if (!iso) return 0;
  const value = new Date(iso).getTime();
  return Number.isNaN(value) ? 0 : value;
}

function compareNullable(left: number | null, right: number | null): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return right - left;
}

function sortExperiments(items: Experiment[], sort: SortOption): Experiment[] {
  const sorted = [...items];
  switch (sort) {
    case "oldest":
      return sorted.sort((a, b) => timestamp(a.created_at) - timestamp(b.created_at));
    case "name":
      return sorted.sort((a, b) => a.name.localeCompare(b.name));
    case "success":
      return sorted.sort((a, b) => compareNullable(successValue(a), successValue(b)));
    case "progress":
      return sorted.sort((a, b) => compareNullable(a.progress?.percent ?? null, b.progress?.percent ?? null));
    case "duration":
      return sorted.sort((a, b) => compareNullable(durationOf(a), durationOf(b)));
    default:
      return sorted.sort((a, b) => timestamp(b.created_at) - timestamp(a.created_at));
  }
}

function SuccessCell({ experiment }: { experiment: Experiment }) {
  const success = successOf(experiment);
  if (!success || success.attributable === 0) {
    return <span className="subtle text-2xs">no attributable runs</span>;
  }
  const percent = (success.solved / success.attributable) * 100;
  return (
    <div>
      <p className="tnum whitespace-nowrap font-medium">
        {Number.isInteger(percent) ? percent : percent.toFixed(1)}%
        <span className="muted ml-1 font-normal">
          ({success.solved}/{success.attributable})
        </span>
      </p>
      {experiment.provenance.is_model_result ? (
        success.excluded > 0 && <p className="subtle text-2xs">{success.excluded} excluded</p>
      ) : (
        <p className="text-2xs text-warn">not a model score</p>
      )}
    </div>
  );
}

function DurationCell({ experiment }: { experiment: Experiment }) {
  const seconds = durationOf(experiment);
  if (seconds !== null) return <span className="tnum">{formatDuration(seconds)}</span>;
  if (experiment.started_at) return <span className="subtle">in progress</span>;
  return <span className="subtle">not started</span>;
}

function ProgressCell({ experiment }: { experiment: Experiment }) {
  const progress = experiment.progress;
  if (!progress) return <span className="muted text-2xs">—</span>;
  return (
    <>
      <ProgressBar percent={progress.percent} muted={experiment.state === "cancelled"} />
      <p className="tnum muted mt-1 text-2xs">
        {progress.finished}/{progress.total} finished
        {progress.active > 0 && ` · ${progress.active} in flight`}
        {progress.pending > 0 && ` · ${progress.pending} pending`}
      </p>
    </>
  );
}

function OutcomePills({ byOutcome }: { byOutcome: Partial<Record<RunOutcome, number>> }) {
  const entries = Object.entries(byOutcome) as [RunOutcome, number][];
  if (entries.length === 0) return <span className="muted text-2xs">no finished runs</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {entries.map(([outcome, count]) => (
        <Badge
          key={outcome}
          tone={OUTCOME_META[outcome].tone}
          title={
            OUTCOME_META[outcome].counted
              ? "counts toward the success rate"
              : "excluded from success rates"
          }
        >
          {OUTCOME_META[outcome].label} {count}
        </Badge>
      ))}
    </div>
  );
}

function CreateExperimentModal({ onClose }: { onClose: () => void }) {
  const agents = useAgents();
  const tasks = useTasks({ limit: 200, offset: 0 });
  const create = useCreateExperiment();

  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [selectedTasks, setSelectedTasks] = useState<string[]>([]);
  const [repetitions, setRepetitions] = useState(1);
  const [concurrency, setConcurrency] = useState(1);
  const [seed, setSeed] = useState("");
  const [includeHints, setIncludeHints] = useState(false);

  const enabledTasks = (tasks.data?.items ?? []).filter((task) => task.enabled);
  const enabledAgents = (agents.data?.items ?? []).filter((agent) => agent.enabled);

  const toggleTask = (key: string) =>
    setSelectedTasks((current) =>
      current.includes(key) ? current.filter((entry) => entry !== key) : [...current, key],
    );

  const submit = () =>
    create.mutate(
      {
        name,
        agent_config_id: agentId,
        task_keys: selectedTasks,
        description: "",
        repetitions,
        concurrency,
        seed: seed.trim() === "" ? null : Number(seed),
        include_hints: includeHints,
        launch: true,
      },
      { onSuccess: onClose },
    );

  const selectedAgent = enabledAgents.find((agent) => agent.id === agentId);

  return (
    <Modal open onClose={onClose} title="Create experiment" wide>
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Name">
            <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Agent">
            <select
              className={inputClass}
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
            >
              <option value="">Select an agent…</option>
              {enabledAgents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.label} ({agent.adapter}){agent.readiness.ready ? "" : " — not ready"}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {selectedAgent && !selectedAgent.readiness.ready && (
          <p className="text-2xs text-amber-400">
            This agent reports not ready: {selectedAgent.readiness.detail}
          </p>
        )}
        {selectedAgent?.is_simulated && (
          <div>
            <SimulatedBadge />
          </div>
        )}

        <Field
          label={`Tasks (${selectedTasks.length} selected)`}
          hint="Only enabled tasks can be benchmarked."
        >
          <div className="hairline max-h-56 overflow-y-auto rounded-md border p-2">
            {tasks.isLoading && <LoadingRows rows={3} />}
            {enabledTasks.length === 0 && !tasks.isLoading && (
              <p className="muted p-2 text-xs">No enabled tasks available.</p>
            )}
            {enabledTasks.map((task) => (
              <label
                key={task.key}
                className="flex cursor-pointer items-start gap-2 rounded px-1.5 py-1 text-xs hover:opacity-80"
              >
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={selectedTasks.includes(task.key)}
                  onChange={() => toggleTask(task.key)}
                />
                <span className="min-w-0">
                  <Mono className="font-semibold">{task.key}</Mono>
                  <span className="muted ml-2">{task.title}</span>
                  {task.validation_status !== "valid" && (
                    <Badge tone="warn" className="ml-1.5">
                      {task.validation_status}
                    </Badge>
                  )}
                </span>
              </label>
            ))}
          </div>
        </Field>
        <div className="flex gap-2">
          <Button onClick={() => setSelectedTasks(enabledTasks.map((task) => task.key))}>
            Select all
          </Button>
          <Button onClick={() => setSelectedTasks([])}>Clear</Button>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Repetitions" hint="More than 1 enables pass@k and variance.">
            <input
              type="number"
              min={1}
              max={50}
              className={inputClass}
              value={repetitions}
              onChange={(event) => setRepetitions(Number(event.target.value))}
            />
          </Field>
          <Field label="Concurrency">
            <input
              type="number"
              min={1}
              max={16}
              className={inputClass}
              value={concurrency}
              onChange={(event) => setConcurrency(Number(event.target.value))}
            />
          </Field>
          <Field label="Seed" hint="Blank for none.">
            <input
              className={inputClass}
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </Field>
          <Field label="Hints" hint="Withheld from the agent by default.">
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={includeHints}
                onChange={(event) => setIncludeHints(event.target.checked)}
              />
              Give hints to agent
            </label>
          </Field>
        </div>

        {create.isError && <ErrorState error={create.error} />}

        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!name.trim() || !agentId || selectedTasks.length === 0 || create.isPending}
            onClick={submit}
          >
            {create.isPending ? "Creating…" : "Create and queue"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export default function Experiments() {
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [params, setParams] = useSearchParams();

  const query = params.get("q") ?? "";
  const stateFilter = params.get("state") ?? "";
  const provenanceFilter = params.get("prov") ?? "";
  const sortParam = params.get("sort") as SortOption | null;
  const sort: SortOption = SORT_OPTIONS.some((option) => option.value === sortParam)
    ? (sortParam as SortOption)
    : "newest";

  // Search, state and date/name ordering run on the server, so they cover every
  // experiment, not just the latest page. Typing is deferred, not refetched per key.
  const deferredQuery = useDeferredValue(query.trim());
  const serverSort = sort === "oldest" || sort === "name" ? sort : "newest";
  const experiments = useExperiments({
    search: deferredQuery || undefined,
    state: stateFilter || undefined,
    sort: serverSort,
  });

  const update = (key: string, value: string) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  const all = experiments.data?.items ?? [];
  const provenanceCounts = new Map<ProvenanceValue, { label: string; count: number }>();
  for (const experiment of all) {
    const entry = provenanceCounts.get(experiment.provenance.value);
    if (entry) entry.count += 1;
    else provenanceCounts.set(experiment.provenance.value, { label: experiment.provenance.label, count: 1 });
  }

  // Provenance is derived per experiment, so it filters the loaded page here.
  const filtered = sortExperiments(
    all.filter((experiment) => !provenanceFilter || experiment.provenance.value === provenanceFilter),
    sort,
  );

  const filtersActive = Boolean(query || stateFilter || provenanceFilter || sort !== "newest");
  const total = experiments.data?.pagination.total;

  const openRow = (event: MouseEvent<HTMLElement>, id: string) => {
    // Let real links inside the row (and modified clicks) behave natively.
    if ((event.target as HTMLElement).closest("a, button, input, label")) return;
    if (event.metaKey || event.ctrlKey) {
      window.open(`/experiments/${id}`, "_blank", "noopener");
      return;
    }
    navigate(`/experiments/${id}`);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Experiments"
        description="Every experiment run on this machine. Success rates count solved over agent-attributable runs; excluded outcomes (infra, invalid task, grading error, cancelled) are never counted as failures."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            Create experiment
          </Button>
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-2 p-3">
          <SearchInput
            value={query}
            onChange={(value) => update("q", value)}
            placeholder="Search name, agent, model or benchmark"
            label="Search experiments"
            className="w-full sm:w-72"
          />
          <Select
            label="Filter by state"
            value={stateFilter}
            onChange={(value) => update("state", value)}
            options={[{ value: "", label: "All states" }, ...STATES.map((state) => ({ value: state, label: state }))]}
          />
          <Select
            label="Filter by provenance"
            value={provenanceFilter}
            onChange={(value) => update("prov", value)}
            options={[
              { value: "", label: "All provenance" },
              ...Array.from(provenanceCounts.entries()).map(([value, entry]) => ({
                value,
                label: `${entry.label} (${entry.count})`,
              })),
              ...(provenanceFilter && !provenanceCounts.has(provenanceFilter as ProvenanceValue)
                ? [{ value: provenanceFilter, label: provenanceFilter.replace(/_/g, " ") }]
                : []),
            ]}
          />
          <Select
            label="Sort experiments"
            value={sort}
            onChange={(value) => update("sort", value === "newest" ? "" : value)}
            options={SORT_OPTIONS}
          />
          {filtersActive && (
            <Button variant="ghost" onClick={() => setParams(new URLSearchParams(), { replace: true })}>
              Reset
            </Button>
          )}
          {experiments.data && (
            <span className="subtle tnum ml-auto text-2xs">
              {filtered.length} shown · {total ?? all.length} match on the server
              {total !== undefined && total > all.length && ` · first ${all.length} loaded`}
            </span>
          )}
        </div>
      </Card>

      <Card
        title="All experiments"
        subtitle={total !== undefined ? `${total} total` : undefined}
      >
        {experiments.isError && (
          <div className="p-4">
            <ErrorState error={experiments.error} onRetry={() => void experiments.refetch()} />
          </div>
        )}
        {experiments.isLoading && <LoadingRows rows={5} label="Loading experiments" />}
        {experiments.data && all.length === 0 && (
          <div className="p-4">
            <EmptyState
              title="No experiments yet"
              description={
                <>
                  Create one with the button above, or run{" "}
                  <Mono>agentbench experiment create --name demo --agent reference-solution</Mono>.
                </>
              }
              action={
                <Button variant="primary" onClick={() => setCreating(true)}>
                  Create experiment
                </Button>
              }
            />
          </div>
        )}
        {experiments.data && all.length > 0 && filtered.length === 0 && (
          <div className="p-4">
            <EmptyState
              title="No experiments match these filters"
              action={
                <Button onClick={() => setParams(new URLSearchParams(), { replace: true })}>Clear filters</Button>
              }
            />
          </div>
        )}
        {filtered.length > 0 && (
          <>
            {/* desktop / tablet: table */}
            <div className="hidden md:block">
              <Table>
                <thead>
                  <tr>
                    <Th>Experiment</Th>
                    <Th>State</Th>
                    <Th>Agent · model</Th>
                    <Th>Benchmark</Th>
                    <Th className="w-44">Progress</Th>
                    <Th>Success</Th>
                    <Th>Outcomes</Th>
                    <Th className="text-right">Duration</Th>
                    <Th className="text-right">Created</Th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((experiment) => (
                    <tr
                      key={experiment.id}
                      onClick={(event) => openRow(event, experiment.id)}
                      className="row-hover cursor-pointer transition-colors"
                    >
                      <Td className="min-w-[12rem]">
                        <Link to={`/experiments/${experiment.id}`} className="link font-medium">
                          {experiment.name}
                        </Link>
                        <p className="subtle mt-0.5 text-2xs">
                          <Mono>{experiment.id.slice(0, 8)}</Mono> · {experiment.repetitions} rep(s) ·{" "}
                          {experiment.task_keys.length} task(s)
                          {experiment.include_hints && " · hints given"}
                        </p>
                      </Td>
                      <Td>
                        <Badge tone={STATE_TONES[experiment.state] ?? "neutral"}>{experiment.state}</Badge>
                        {experiment.cancel_requested && experiment.state !== "cancelled" && (
                          <p className="text-warn mt-1 text-2xs">cancel requested</p>
                        )}
                      </Td>
                      <Td>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Mono>{experiment.agent.label}</Mono>
                          <ProvenanceBadge provenance={experiment.provenance} compact />
                        </div>
                        <Mono className="subtle mt-0.5 block">
                          {experiment.agent.model ?? "no model"} · {experiment.agent.adapter}
                        </Mono>
                      </Td>
                      <Td>
                        <Mono>
                          {experiment.benchmark_id}@{experiment.benchmark_version}
                        </Mono>
                        <Mono className="subtle mt-0.5 block">grader {experiment.grading_version}</Mono>
                      </Td>
                      <Td>
                        <ProgressCell experiment={experiment} />
                      </Td>
                      <Td>
                        <SuccessCell experiment={experiment} />
                      </Td>
                      <Td>{experiment.progress && <OutcomePills byOutcome={experiment.progress.by_outcome} />}</Td>
                      <Td className="whitespace-nowrap text-right">
                        <DurationCell experiment={experiment} />
                      </Td>
                      <Td className="muted tnum whitespace-nowrap text-right" title={formatTime(experiment.created_at)}>
                        {formatRelative(experiment.created_at)}
                        {experiment.finished_at && (
                          <p className="subtle text-2xs" title={formatTime(experiment.finished_at)}>
                            finished {formatRelative(experiment.finished_at)}
                          </p>
                        )}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>

            {/* mobile: cards */}
            <ul className="hairline divide-y divide-[rgb(var(--border))] md:hidden">
              {filtered.map((experiment) => (
                <li key={experiment.id}>
                  <Link
                    to={`/experiments/${experiment.id}`}
                    className="row-hover block space-y-2 px-4 py-3 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium">{experiment.name}</p>
                        <p className="subtle text-2xs">
                          {formatRelative(experiment.created_at)} · {experiment.task_keys.length} task(s) ·{" "}
                          {experiment.repetitions} rep(s)
                        </p>
                      </div>
                      <Badge tone={STATE_TONES[experiment.state] ?? "neutral"}>{experiment.state}</Badge>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Mono>{experiment.agent.label}</Mono>
                      <ProvenanceBadge provenance={experiment.provenance} compact />
                      <Mono className="subtle">
                        {experiment.benchmark_id}@{experiment.benchmark_version}
                      </Mono>
                    </div>
                    <ProgressCell experiment={experiment} />
                    <div className="flex items-end justify-between gap-3 text-xs">
                      <SuccessCell experiment={experiment} />
                      <span className="text-2xs">
                        <DurationCell experiment={experiment} />
                      </span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>

      {creating && <CreateExperimentModal onClose={() => setCreating(false)} />}
    </div>
  );
}
