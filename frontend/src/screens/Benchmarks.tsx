import { useState } from "react";

import { downloadUrl } from "../api/client";
import {
  useBenchmarks,
  useImportBenchmark,
  useSetTaskEnabled,
  useTask,
  useTasks,
  useValidateTask,
  type TaskFilters,
} from "../api/hooks";
import type { Task, ValidationReport } from "../api/types";
import { ValidationBadge, formatTime } from "../components/domain";
import {
  Badge,
  Button,
  Card,
  CodeBlock,
  EmptyState,
  ErrorState,
  Field,
  JsonBlock,
  KeyValue,
  LinkButton,
  LoadingRows,
  Modal,
  Mono,
  Table,
  Td,
  Th,
  inputClass,
} from "../components/ui";

function ChecksList({ report }: { report: ValidationReport }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <ValidationBadge status={report.status} />
        <span className="muted text-2xs">
          validated {formatTime(report.validated_at)} via {report.sandbox_provider ?? "unknown"} provider
        </span>
      </div>
      <ul className="space-y-1.5">
        {report.checks.map((check) => (
          <li key={check.name} className="flex gap-2 text-xs">
            <Badge tone={check.ok ? "good" : "bad"}>{check.ok ? "ok" : "fail"}</Badge>
            <div className="min-w-0">
              <Mono className="font-semibold">{check.name}</Mono>
              <p className="muted mt-0.5 break-words text-2xs">{check.detail}</p>
            </div>
          </li>
        ))}
      </ul>
      {report.warnings.length > 0 && (
        <ul className="space-y-1 text-2xs text-amber-400">
          {report.warnings.map((warning) => (
            <li key={warning}>· {warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TaskDetail({ taskKey, onClose }: { taskKey: string; onClose: () => void }) {
  const { data, isLoading, isError, error, refetch } = useTask(taskKey);
  const validate = useValidateTask();
  const setEnabled = useSetTaskEnabled();

  return (
    <Modal open onClose={onClose} title={taskKey} wide>
      {isError && <ErrorState error={error} onRetry={() => void refetch()} />}
      {isLoading && <LoadingRows rows={6} />}
      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <ValidationBadge status={data.validation_status} />
            <Badge tone="neutral">{data.category}</Badge>
            <Badge tone="neutral">{data.difficulty}</Badge>
            {!data.enabled && <Badge tone="warn">disabled</Badge>}
            {data.tags.map((tag) => (
              <Badge key={tag} tone="neutral">
                {tag}
              </Badge>
            ))}
          </div>

          <h3 className="text-sm font-semibold">{data.title}</h3>

          <KeyValue
            rows={[
              ["Benchmark", <Mono>{`${data.benchmark_id}@${data.benchmark_version}`}</Mono>],
              ["Image", <Mono>{data.summary.image ?? "—"}</Mono>],
              ["fail_to_pass tests", data.summary.fail_to_pass_count],
              ["pass_to_pass tests", data.summary.pass_to_pass_count],
              ["Hidden grading files", data.summary.hidden_acceptance_files.length],
              ["Hints (withheld from agent by default)", data.summary.hint_count],
            ]}
          />

          <div>
            <p className="muted text-2xs font-semibold uppercase tracking-wide">
              Problem statement (the agent's only natural-language input)
            </p>
            <CodeBlock className="mt-1 whitespace-pre-wrap">
              {data.summary.problem_statement}
            </CodeBlock>
          </div>

          {data.summary.acceptance_criteria.length > 0 && (
            <div>
              <p className="muted text-2xs font-semibold uppercase tracking-wide">
                Acceptance criteria
              </p>
              <ul className="mt-1 space-y-1 text-xs">
                {data.summary.acceptance_criteria.map((item) => (
                  <li key={item}>· {item}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <p className="muted text-2xs font-semibold uppercase tracking-wide">
                Agent must not modify
              </p>
              <ul className="mt-1 space-y-0.5">
                {data.summary.forbidden_paths.map((path) => (
                  <li key={path}>
                    <Mono>{path}</Mono>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="muted text-2xs font-semibold uppercase tracking-wide">Limits</p>
              <JsonBlock value={data.summary.limits} maxHeight="max-h-40" />
            </div>
          </div>

          <div className="hairline space-y-3 border-t pt-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                disabled={validate.isPending}
                onClick={() => validate.mutate(taskKey)}
              >
                {validate.isPending ? "Validating in a container…" : "Validate environment"}
              </Button>
              <Button
                onClick={() => setEnabled.mutate({ taskKey, enabled: !data.enabled })}
                disabled={setEnabled.isPending}
              >
                {data.enabled ? "Disable task" : "Enable task"}
              </Button>
            </div>
            {validate.isError && <ErrorState error={validate.error} />}
            {setEnabled.isError && <ErrorState error={setEnabled.error} />}

            {(validate.data ?? data.validation_report) && (
              <ChecksList report={(validate.data ?? data.validation_report) as ValidationReport} />
            )}
            {!validate.data && !data.validation_report && (
              <p className="muted text-2xs">
                Never validated. Validation builds the real environment and checks the task is
                executable and falsifiable — that its target tests actually fail before a fix.
              </p>
            )}
          </div>
        </div>
      )}
    </Modal>
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
          <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs">
            <p className="font-semibold text-emerald-400">Imported</p>
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
  const [filters, setFilters] = useState<TaskFilters>(EMPTY_FILTERS);
  const [selected, setSelected] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);

  const tasks = useTasks(filters);
  const benchmarks = useBenchmarks();

  const hasFilters = Boolean(
    filters.search || filters.category || filters.difficulty || filters.validation_status || filters.benchmark_id,
  );

  const update = (patch: Partial<TaskFilters>) =>
    setFilters((current) => ({ ...current, ...patch, offset: 0 }));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Benchmarks</h1>
        <div className="flex flex-wrap items-center gap-2">
          <LinkButton href={downloadUrl("/tasks/export", { format: "json" })} title="Export as JSON">
            Export JSON
          </LinkButton>
          <LinkButton href={downloadUrl("/tasks/export", { format: "jsonl" })} title="Export as JSONL">
            Export JSONL
          </LinkButton>
          <Button variant="primary" onClick={() => setImporting(true)}>
            Import benchmark
          </Button>
        </div>
      </div>

      {benchmarks.data && benchmarks.data.benchmarks.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {benchmarks.data.benchmarks.map((benchmark) => (
            <button
              key={`${benchmark.benchmark_id}@${benchmark.benchmark_version}`}
              type="button"
              onClick={() => update({ benchmark_id: benchmark.benchmark_id })}
              className="surface hairline rounded-lg border p-3 text-left transition hover:opacity-80"
            >
              <p className="font-mono text-xs font-semibold">
                {benchmark.benchmark_id}@{benchmark.benchmark_version}
              </p>
              <p className="tnum muted mt-1 text-2xs">
                {benchmark.task_count} task(s) · {benchmark.validated_count} validated
              </p>
            </button>
          ))}
        </div>
      )}

      <Card
        title="Tasks"
        subtitle={tasks.data ? `${tasks.data.pagination.total} total` : undefined}
        actions={
          hasFilters ? (
            <Button onClick={() => setFilters(EMPTY_FILTERS)}>Clear filters</Button>
          ) : undefined
        }
      >
        <div className="hairline grid grid-cols-1 gap-2 border-b p-3 sm:grid-cols-2 lg:grid-cols-5">
          <input
            className={inputClass}
            placeholder="Search title or id…"
            value={filters.search ?? ""}
            onChange={(event) => update({ search: event.target.value })}
          />
          <select
            className={inputClass}
            value={filters.category ?? ""}
            onChange={(event) => update({ category: event.target.value || undefined })}
          >
            <option value="">All categories</option>
            {["bug_fix", "feature", "refactor", "regression_fix", "test_authoring", "performance"].map(
              (value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ),
            )}
          </select>
          <select
            className={inputClass}
            value={filters.difficulty ?? ""}
            onChange={(event) => update({ difficulty: event.target.value || undefined })}
          >
            <option value="">All difficulties</option>
            {["easy", "medium", "hard"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <select
            className={inputClass}
            value={filters.validation_status ?? ""}
            onChange={(event) => update({ validation_status: event.target.value || undefined })}
          >
            <option value="">Any validation status</option>
            {["valid", "invalid", "unvalidated", "error"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <select
            className={inputClass}
            value={filters.benchmark_id ?? ""}
            onChange={(event) => update({ benchmark_id: event.target.value || undefined })}
          >
            <option value="">All benchmarks</option>
            {(benchmarks.data?.benchmarks ?? []).map((benchmark) => (
              <option key={benchmark.benchmark_id} value={benchmark.benchmark_id}>
                {benchmark.benchmark_id}
              </option>
            ))}
          </select>
        </div>

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
                <Th>Image</Th>
              </tr>
            </thead>
            <tbody>
              {tasks.data.items.map((task: Task) => (
                <tr
                  key={task.key}
                  onClick={() => setSelected(task.key)}
                  className="cursor-pointer hover:surface-2"
                >
                  <Td>
                    <p className="font-medium">{task.title}</p>
                    <Mono className="muted">{task.key}</Mono>
                    {!task.enabled && (
                      <Badge tone="warn" className="ml-2">
                        disabled
                      </Badge>
                    )}
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
                    <Mono className="muted">{task.summary.image ?? "—"}</Mono>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {selected && <TaskDetail taskKey={selected} onClose={() => setSelected(null)} />}
      {importing && <ImportModal onClose={() => setImporting(false)} />}
    </div>
  );
}
