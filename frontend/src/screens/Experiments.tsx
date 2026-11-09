import { useState } from "react";
import { Link } from "react-router-dom";

import { useAgents, useCreateExperiment, useExperiments, useTasks } from "../api/hooks";
import type { Experiment, RunOutcome } from "../api/types";
import { OUTCOME_META, ProvenanceBadge, SimulatedBadge, formatRelative } from "../components/domain";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  Field,
  LoadingRows,
  Modal,
  Mono,
  ProgressBar,
  Table,
  Td,
  Th,
  inputClass,
} from "../components/ui";

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
  const experiments = useExperiments();
  const [creating, setCreating] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Experiments</h1>
        <Button variant="primary" onClick={() => setCreating(true)}>
          Create experiment
        </Button>
      </div>

      <Card
        title="All experiments"
        subtitle={experiments.data ? `${experiments.data.pagination.total} total` : undefined}
      >
        {experiments.isError && (
          <div className="p-4">
            <ErrorState error={experiments.error} onRetry={() => void experiments.refetch()} />
          </div>
        )}
        {experiments.isLoading && <LoadingRows rows={5} label="Loading experiments" />}
        {experiments.data && experiments.data.items.length === 0 && (
          <p className="muted p-4 text-xs">
            No experiments yet. Create one above, or run{" "}
            <Mono>agentbench experiment create --name demo --agent reference-solution</Mono>.
          </p>
        )}
        {experiments.data && experiments.data.items.length > 0 && (
          <Table>
            <thead>
              <tr>
                <Th>Experiment</Th>
                <Th>State</Th>
                <Th>Agent</Th>
                <Th>Benchmark</Th>
                <Th className="w-48">Progress</Th>
                <Th>Outcomes</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {experiments.data.items.map((experiment: Experiment) => {
                const progress = experiment.progress;
                return (
                  <tr key={experiment.id} className="hover:surface-2">
                    <Td>
                      <Link
                        to={`/experiments/${experiment.id}`}
                        className="accent-text font-medium hover:underline"
                      >
                        {experiment.name}
                      </Link>
                      <p className="muted mt-0.5 text-2xs">
                        {experiment.repetitions} rep(s) · {experiment.task_keys.length} task(s)
                        {experiment.include_hints && " · hints given"}
                      </p>
                    </Td>
                    <Td>
                      <Badge
                        tone={
                          experiment.state === "completed"
                            ? "accent"
                            : experiment.state === "cancelled"
                              ? "neutral"
                              : "info"
                        }
                      >
                        {experiment.state}
                      </Badge>
                    </Td>
                    <Td>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Mono>{experiment.agent.label}</Mono>
                        <ProvenanceBadge provenance={experiment.provenance} compact />
                      </div>
                      <Mono className="muted mt-0.5 block">{experiment.agent.adapter}</Mono>
                    </Td>
                    <Td>
                      <Mono>
                        {experiment.benchmark_id}@{experiment.benchmark_version}
                      </Mono>
                    </Td>
                    <Td>
                      {progress ? (
                        <>
                          <ProgressBar
                            percent={progress.percent}
                            muted={experiment.state === "cancelled"}
                          />
                          <p className="tnum muted mt-1 text-2xs">
                            {progress.finished}/{progress.total} finished
                            {progress.active > 0 && ` · ${progress.active} in flight`}
                            {progress.pending > 0 && ` · ${progress.pending} pending`}
                          </p>
                        </>
                      ) : (
                        <span className="muted text-2xs">—</span>
                      )}
                    </Td>
                    <Td>{progress && <OutcomePills byOutcome={progress.by_outcome} />}</Td>
                    <Td className="muted text-2xs">{formatRelative(experiment.created_at)}</Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>

      {creating && <CreateExperimentModal onClose={() => setCreating(false)} />}
    </div>
  );
}
