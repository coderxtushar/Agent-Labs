import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { downloadUrl } from "../api/client";
import {
  useBenchmarks,
  useImportBenchmark,
  useSetTaskEnabled,
  useTasks,
  useValidateTask,
  type TaskFilters,
} from "../api/hooks";
import type { Task } from "../api/types";
import { ValidationBadge } from "../components/domain";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  JsonBlock,
  LinkButton,
  LoadingRows,
  Modal,
  Mono,
  PageHeader,
  SearchInput,
  Select,
  Table,
  Td,
  Th,
  cx,
  inputClass,
} from "../components/ui";

const CATEGORIES = ["bug_fix", "feature", "refactor", "regression_fix", "test_authoring", "performance"];
const DIFFICULTIES = ["easy", "medium", "hard"];
const VALIDATION = ["valid", "invalid", "unvalidated", "error"];

const taskPath = (key: string) => `/benchmarks/tasks/${encodeURIComponent(key)}`;

function compactSeconds(value: number | string | undefined): string | null {
  if (typeof value !== "number") return null;
  if (value >= 60 && value % 60 === 0) return `${value / 60}m`;
  if (value >= 60) return `${Math.floor(value / 60)}m ${value % 60}s`;
  return `${value}s`;
}

function TimeoutCell({ task }: { task: Task }) {
  const limits = task.summary.limits;
  const parts = limits
    ? (
        [
          ["agent", compactSeconds(limits.agent_timeout_s)],
          ["setup", compactSeconds(limits.setup_timeout_s)],
          ["test", compactSeconds(limits.test_timeout_s)],
        ] as const
      ).filter(([, value]) => value !== null)
    : [];
  if (parts.length === 0) return <span className="subtle">defaults</span>;
  return (
    <span className="tnum whitespace-nowrap text-2xs">
      {parts.map(([label, value], index) => (
        <span key={label}>
          {index > 0 && <span className="subtle"> · </span>}
          <span className="subtle">{label} </span>
          {value}
        </span>
      ))}
      {limits?.max_agent_steps !== undefined && (
        <span className="subtle"> · {limits.max_agent_steps} steps</span>
      )}
    </span>
  );
}

function ImportModal({ onClose }: { onClose: () => void }) {
  const [content, setContent] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const importBenchmark = useImportBenchmark();

  return (
    <Modal open onClose={onClose} title="Import benchmark" wide>
      <div className="space-y-3">
        <p className="muted text-xs">
          Paste a benchmark file: a task object, an array of tasks, or a{" "}
          <Mono>{"{benchmark_id, tasks: []}"}</Mono> envelope. The schema is validated server-side
          and field-level errors are reported below.
        </p>
        <Field label="Benchmark JSON">
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            rows={14}
            spellCheck={false}
            placeholder='{"benchmark_id": "my-bench", "tasks": [ ... ]}'
            className={`${inputClass} font-mono`}
          />
        </Field>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={overwrite}
            onChange={(event) => setOverwrite(event.target.checked)}
          />
          Allow replacing an existing task version (changes what prior results mean — prefer
          bumping the task version)
        </label>

        {importBenchmark.isError && <ErrorState error={importBenchmark.error} />}
        {importBenchmark.data && (
          <div className="space-y-2 rounded-lg border border-[rgb(var(--good)/0.3)] bg-[rgb(var(--good)/0.05)] p-3 text-xs">
            <p className="text-good font-semibold">Imported</p>
            <JsonBlock value={importBenchmark.data} maxHeight="max-h-48" />
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Close</Button>
          <Button
            variant="primary"
            disabled={!content.trim() || importBenchmark.isPending}
            onClick={() => importBenchmark.mutate({ content, allow_overwrite: overwrite })}
          >
            {importBenchmark.isPending ? "Importing…" : "Import"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

const EMPTY_FILTERS: TaskFilters = { limit: 100, offset: 0 };

export default function Benchmarks() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState<TaskFilters>(EMPTY_FILTERS);
  const [importing, setImporting] = useState(false);

  const tasks = useTasks(filters);
  const benchmarks = useBenchmarks();
  const validate = useValidateTask();
  const setEnabled = useSetTaskEnabled();

  const hasFilters = Boolean(
    filters.search || filters.category || filters.difficulty || filters.validation_status || filters.benchmark_id,
  );

  const update = (patch: Partial<TaskFilters>) =>
    setFilters((current) => ({ ...current, ...patch, offset: 0 }));

  const totals = (benchmarks.data?.benchmarks ?? []).reduce(
    (acc, benchmark) => ({ tasks: acc.tasks + benchmark.task_count, validated: acc.validated + benchmark.validated_count }),
    { tasks: 0, validated: 0 },
  );

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Benchmarks"
        description="Versioned task library. Each task pins a repository, environment, limits and the tests that grade it; validation proves a task is executable and falsifiable before results are trusted."
        meta={
          benchmarks.data ? (
            <span className="muted tnum text-2xs">
              {totals.tasks} task{totals.tasks === 1 ? "" : "s"} · {totals.validated} validated ·{" "}
              {benchmarks.data.benchmarks.length} benchmark{benchmarks.data.benchmarks.length === 1 ? "" : "s"}
            </span>
          ) : undefined
        }
        actions={
          <>
            <LinkButton href={downloadUrl("/tasks/export", { format: "json" })} title="Export as JSON">
              Export JSON
            </LinkButton>
            <LinkButton href={downloadUrl("/tasks/export", { format: "jsonl" })} title="Export as JSONL">
              Export JSONL
            </LinkButton>
            <Button variant="primary" onClick={() => setImporting(true)}>
              Import benchmark
            </Button>
          </>
        }
      />

      {benchmarks.data && benchmarks.data.benchmarks.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {benchmarks.data.benchmarks.map((benchmark) => {
            const active = filters.benchmark_id === benchmark.benchmark_id;
            const percent = benchmark.task_count > 0 ? (benchmark.validated_count / benchmark.task_count) * 100 : 0;
            return (
              <button
                key={`${benchmark.benchmark_id}@${benchmark.benchmark_version}`}
                type="button"
                aria-pressed={active}
                onClick={() => update({ benchmark_id: active ? undefined : benchmark.benchmark_id })}
                className={cx(
                  "surface elevated rounded-xl border p-4 text-left transition-colors duration-150",
                  active ? "border-[rgb(var(--accent)/0.6)]" : "hairline hover:border-[rgb(var(--border-strong))]",
                )}
              >
                <p className="font-mono text-xs font-semibold">
                  {benchmark.benchmark_id}
                  <span className="subtle">@{benchmark.benchmark_version}</span>
                </p>
                <p className="tnum muted mt-1 text-2xs">
                  {benchmark.task_count} task(s) · {benchmark.validated_count} validated
                </p>
                <div className="surface-3 mt-2.5 h-1 overflow-hidden rounded-full" aria-hidden>
                  <div className="h-full rounded-full" style={{ width: `${percent}%`, backgroundColor: "rgb(var(--good))" }} />
                </div>
              </button>
            );
          })}
        </div>
      )}

      <Card
        title="Tasks"
        subtitle={tasks.data ? `${tasks.data.pagination.total} total — open a task for its full specification and run history` : undefined}
        actions={hasFilters ? <Button variant="ghost" onClick={() => setFilters(EMPTY_FILTERS)}>Clear filters</Button> : undefined}
      >
        <div className="hairline flex flex-wrap items-center gap-2 border-b p-3">
          <SearchInput
            value={filters.search ?? ""}
            onChange={(value) => update({ search: value })}
            placeholder="Search title or id…"
            label="Search tasks"
            className="min-w-[14rem] flex-1"
          />
          <Select
            label="Category"
            value={filters.category ?? ""}
            onChange={(value) => update({ category: value || undefined })}
            options={[{ value: "", label: "All categories" }, ...CATEGORIES.map((value) => ({ value, label: value }))]}
          />
          <Select
            label="Difficulty"
            value={filters.difficulty ?? ""}
            onChange={(value) => update({ difficulty: value || undefined })}
            options={[{ value: "", label: "All difficulties" }, ...DIFFICULTIES.map((value) => ({ value, label: value }))]}
          />
          <Select
            label="Validation status"
            value={filters.validation_status ?? ""}
            onChange={(value) => update({ validation_status: value || undefined })}
            options={[{ value: "", label: "Any validation status" }, ...VALIDATION.map((value) => ({ value, label: value }))]}
          />
          <Select
            label="Benchmark"
            value={filters.benchmark_id ?? ""}
            onChange={(value) => update({ benchmark_id: value || undefined })}
            options={[
              { value: "", label: "All benchmarks" },
              ...(benchmarks.data?.benchmarks ?? []).map((benchmark) => ({
                value: benchmark.benchmark_id,
                label: benchmark.benchmark_id,
              })),
            ]}
          />
        </div>

        {(validate.isError || setEnabled.isError) && (
          <div className="hairline space-y-2 border-b p-3">
            {validate.isError && <ErrorState error={validate.error} />}
            {setEnabled.isError && <ErrorState error={setEnabled.error} />}
          </div>
        )}

        {tasks.isError && (
          <div className="p-4">
            <ErrorState error={tasks.error} onRetry={() => void tasks.refetch()} />
          </div>
        )}
        {tasks.isLoading && <LoadingRows rows={6} label="Loading tasks" />}

        {tasks.data && tasks.data.items.length === 0 && (
          <div className="p-4">
            <EmptyState
              title={hasFilters ? "No tasks match these filters" : "No tasks imported"}
              description={
                hasFilters
                  ? "Try widening the filters."
                  : "Import a benchmark file, or run `agentbench bootstrap` to load the bundled textkit benchmark."
              }
              action={
                hasFilters ? (
                  <Button onClick={() => setFilters(EMPTY_FILTERS)}>Clear filters</Button>
                ) : (
                  <Button variant="primary" onClick={() => setImporting(true)}>
                    Import benchmark
                  </Button>
                )
              }
            />
          </div>
        )}

        {tasks.data && tasks.data.items.length > 0 && (
          <Table>
            <thead>
              <tr>
                <Th>Task</Th>
                <Th>Category</Th>
                <Th>Difficulty</Th>
                <Th>Validation</Th>
                <Th className="text-right">fail→pass</Th>
                <Th className="text-right">pass→pass</Th>
                <Th>Timeouts</Th>
                <Th>Image</Th>
                <Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {tasks.data.items.map((task: Task) => {
                const validating = validate.isPending && validate.variables === task.key;
                const toggling = setEnabled.isPending && setEnabled.variables?.taskKey === task.key;
                return (
                  <tr
                    key={task.key}
                    onClick={(event) => {
                      if ((event.target as HTMLElement).closest("a,button")) return;
                      void navigate(taskPath(task.key));
                    }}
                    className={cx("row-hover cursor-pointer", !task.enabled && "opacity-70")}
                  >
                    <Td className="max-w-[20rem]">
                      <Link to={taskPath(task.key)} className="link font-medium">
                        {task.title}
                      </Link>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                        <Mono className="subtle">{task.key}</Mono>
                        {!task.enabled && <Badge tone="warn">disabled</Badge>}
                      </div>
                    </Td>
                    <Td>
                      <Badge tone="neutral">{task.category}</Badge>
                    </Td>
                    <Td>
                      <Badge tone="neutral">{task.difficulty}</Badge>
                    </Td>
                    <Td>
                      <ValidationBadge status={task.validation_status} />
                    </Td>
                    <Td className="tnum text-right">{task.summary.fail_to_pass_count}</Td>
                    <Td className="tnum text-right">{task.summary.pass_to_pass_count}</Td>
                    <Td>
                      <TimeoutCell task={task} />
                    </Td>
                    <Td>
                      <Mono className="muted">{task.summary.image ?? "—"}</Mono>
                    </Td>
                    <Td className="whitespace-nowrap text-right">
                      <div className="inline-flex gap-1">
                        <Button
                          variant="ghost"
                          disabled={validate.isPending}
                          onClick={() => validate.mutate(task.key)}
                          title="Build the environment in a container and check the task is falsifiable"
                        >
                          {validating ? "Validating…" : "Validate"}
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={toggling}
                          onClick={() => setEnabled.mutate({ taskKey: task.key, enabled: !task.enabled })}
                        >
                          {task.enabled ? "Disable" : "Enable"}
                        </Button>
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>

      {importing && <ImportModal onClose={() => setImporting(false)} />}
    </div>
  );
}
