import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { useSetTaskEnabled, useTask, useTaskHistory, useValidateTask } from "../api/hooks";
import type { ProvenanceValue, Task, TaskHistory, ValidationReport } from "../api/types";
import { RateBar, OutcomeStack } from "../components/charts";
import {
  OutcomeBadge,
  ProvenanceBadge,
  RateDisplay,
  ValidationBadge,
  formatDuration,
  formatRelative,
  formatTime,
} from "../components/domain";
import {
  Badge,
  Button,
  Card,
  CodeBlock,
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  Table,
  Td,
  Th,
  type BadgeTone,
} from "../components/ui";

// --------------------------------------------------------------------------- //
const rec = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const strList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
const str = (value: unknown): string | undefined =>
  value === undefined || value === null ? undefined : typeof value === "string" ? value : JSON.stringify(value);

const PROVENANCE_TONE: Record<ProvenanceValue, BadgeTone> = {
  simulated: "warn",
  offline_adapter_test: "warn",
  live_model: "good",
  external_agent: "info",
};

function ProvenanceTag({ value }: { value: ProvenanceValue }) {
  const model = value === "live_model" || value === "external_agent";
  return (
    <Badge tone={PROVENANCE_TONE[value] ?? "neutral"} title={model ? undefined : "Not a model benchmark result"}>
      {value.replace(/_/g, " ")}
    </Badge>
  );
}

function Block({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="eyebrow">{label}</p>
      <div className="mt-1.5">{children}</div>
      {hint && <p className="subtle mt-1 text-2xs">{hint}</p>}
    </div>
  );
}

function Rows({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="divide-y divide-[rgb(var(--border))]">
      {rows.map((row) => (
        <div key={row.label} className="flex items-baseline justify-between gap-4 py-1.5 text-xs">
          <dt className="muted shrink-0">{row.label}</dt>
          <dd className="tnum min-w-0 break-words text-right">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function PathList({ paths, empty }: { paths: string[]; empty: string }) {
  if (paths.length === 0) return <p className="subtle text-2xs">{empty}</p>;
  return (
    <ul className="space-y-0.5">
      {paths.map((path) => (
        <li key={path} className="break-all">
          <Mono>{path}</Mono>
        </li>
      ))}
    </ul>
  );
}

function TestList({ tests, label }: { tests: string[]; label: string }) {
  const [showAll, setShowAll] = useState(false);
  if (tests.length === 0) return <p className="subtle text-2xs">None declared.</p>;
  const shown = showAll ? tests : tests.slice(0, 6);
  return (
    <div>
      <ul className="space-y-0.5">
        {shown.map((test) => (
          <li key={test} className="break-all">
            <Mono className="muted">{test}</Mono>
          </li>
        ))}
      </ul>
      {tests.length > 6 && (
        <button
          type="button"
          aria-expanded={showAll}
          onClick={() => setShowAll(!showAll)}
          className="accent-text mt-1 text-2xs font-medium"
        >
          {showAll ? "Show fewer" : `Show all ${tests.length} ${label}`}
        </button>
      )}
    </div>
  );
}

function seconds(value: number | string | undefined): ReactNode {
  if (value === undefined) return <span className="subtle">not set</span>;
  if (typeof value !== "number") return value;
  return (
    <>
      {value.toLocaleString()}s{value >= 60 && <span className="subtle ml-1">({formatDuration(value)})</span>}
    </>
  );
}

function ChecksList({ report }: { report: ValidationReport }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ValidationBadge status={report.status} />
        <span className="muted text-2xs">
          validated {formatTime(report.validated_at)} via {report.sandbox_provider ?? "unknown"} provider
        </span>
      </div>
      <ul className="space-y-2">
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
        <ul className="text-warn space-y-1 text-2xs">
          {report.warnings.map((warning) => (
            <li key={warning}>· {warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
function SpecificationCard({ task }: { task: Task }) {
  const summary = task.summary;
  const [showHints, setShowHints] = useState(false);
  const hints = strList(task.spec?.hints);
  return (
    <Card title="Specification" subtitle="What the agent is given. Hints are withheld unless an experiment opts in.">
      <div className="space-y-5 p-4">
        <Block label="Problem statement — the agent's natural-language input">
          <CodeBlock className="whitespace-pre-wrap break-words" maxHeight="max-h-[28rem]">
            {summary.problem_statement || "—"}
          </CodeBlock>
        </Block>

        <Block label="Acceptance criteria">
          {summary.acceptance_criteria.length > 0 ? (
            <ul className="space-y-1 text-xs">
              {summary.acceptance_criteria.map((item) => (
                <li key={item} className="flex gap-2">
                  <span aria-hidden className="subtle">·</span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="subtle text-2xs">None declared.</p>
          )}
        </Block>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <Block label="Relevant files" hint="Supplied to the agent as context_files.">
            <PathList paths={summary.context_files} empty="None declared." />
          </Block>
          <Block label="Forbidden paths" hint="A patch touching these fails grading.">
            <PathList paths={summary.forbidden_paths} empty="None declared by the task." />
          </Block>
        </div>

        <Block label={`Hints · ${summary.hint_count}`}>
          {summary.hint_count === 0 ? (
            <p className="subtle text-2xs">No hints defined.</p>
          ) : hints.length === 0 ? (
            <p className="subtle text-2xs">{summary.hint_count} hint(s) defined; their text is not returned by the API.</p>
          ) : (
            <div>
              <button
                type="button"
                aria-expanded={showHints}
                onClick={() => setShowHints(!showHints)}
                className="accent-text text-2xs font-medium"
              >
                {showHints ? "Hide hints" : "Show hints (withheld from the agent by default)"}
              </button>
              {showHints && (
                <ul className="fade-in mt-1.5 space-y-1 text-xs">
                  {hints.map((hint) => (
                    <li key={hint} className="flex gap-2">
                      <span aria-hidden className="subtle">·</span>
                      <span>{hint}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </Block>
      </div>
    </Card>
  );
}

function EnvironmentCard({ task }: { task: Task }) {
  const repo = rec(task.spec?.repo) ?? task.summary.repo;
  const env = rec(task.spec?.environment);
  const setup = strList(env?.setup_commands);
  const pip = strList(env?.pip_packages);
  const envVars = rec(env?.env);
  const kind = str(repo?.kind);
  return (
    <Card title="Repository & environment">
      <div className="space-y-4 p-4">
        <Rows
          rows={[
            { label: "Repository", value: kind ? <Mono>{kind}</Mono> : "—" },
            {
              label: kind === "git" ? "URL" : "Path",
              value: <Mono className="break-all">{str(repo?.url) ?? str(repo?.path) ?? "—"}</Mono>,
            },
            { label: "Revision", value: <Mono>{str(repo?.revision) ?? "—"}</Mono> },
            ...(str(repo?.subdir) ? [{ label: "Subdir", value: <Mono>{str(repo?.subdir)}</Mono> }] : []),
            { label: "Image", value: <Mono className="break-all">{task.summary.image ?? "—"}</Mono> },
            { label: "Workdir", value: <Mono>{str(env?.workdir) ?? "—"}</Mono> },
          ]}
        />
        <Block label={`Setup commands · ${setup.length}`} hint="Run before the agent starts; not agent-controlled.">
          {setup.length > 0 ? (
            <CodeBlock className="whitespace-pre-wrap break-words" maxHeight="max-h-48">
              {setup.map((command) => `$ ${command}`).join("\n")}
            </CodeBlock>
          ) : (
            <p className="subtle text-2xs">None.</p>
          )}
        </Block>
        {pip.length > 0 && (
          <Block label="pip packages">
            <PathList paths={pip} empty="" />
          </Block>
        )}
        {envVars && Object.keys(envVars).length > 0 && (
          <Block label="Environment variables">
            <ul className="space-y-0.5">
              {Object.entries(envVars).map(([key, value]) => (
                <li key={key} className="break-all">
                  <Mono>
                    {key}=<span className="muted">{str(value)}</span>
                  </Mono>
                </li>
              ))}
            </ul>
          </Block>
        )}
      </div>
    </Card>
  );
}

function LimitsCard({ task }: { task: Task }) {
  const limits = task.summary.limits;
  return (
    <Card title="Limits & timeouts" subtitle="Per run; experiments may override within platform maxima">
      <div className="p-4">
        {limits ? (
          <Rows
            rows={[
              { label: "Agent timeout", value: seconds(limits.agent_timeout_s) },
              { label: "Setup timeout", value: seconds(limits.setup_timeout_s) },
              { label: "Test timeout", value: seconds(limits.test_timeout_s) },
              { label: "Max agent steps", value: limits.max_agent_steps ?? <span className="subtle">not set</span> },
              { label: "Memory", value: limits.memory_mb !== undefined ? `${limits.memory_mb} MB` : "—" },
              { label: "CPUs", value: limits.cpus ?? "—" },
              { label: "Disk", value: limits.disk_mb !== undefined ? `${limits.disk_mb} MB` : "—" },
              { label: "Network", value: <Mono>{String(limits.network ?? "—")}</Mono> },
            ]}
          />
        ) : (
          <p className="subtle text-xs">No limits recorded for this task; platform defaults apply.</p>
        )}
      </div>
    </Card>
  );
}

function GradingCard({ task }: { task: Task }) {
  const tests = rec(task.spec?.tests);
  const grading = rec(task.spec?.grading);
  const failToPass = strList(tests?.fail_to_pass);
  const passToPass = strList(tests?.pass_to_pass);
  const staticChecks = Array.isArray(grading?.static_checks)
    ? grading.static_checks.map(rec).filter((check) => check !== null)
    : [];
  const judge = rec(grading?.judge);
  return (
    <Card title="Grading requirements" subtitle="How a run is judged — the agent never grades itself">
      <div className="space-y-5 p-4">
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <Block label="Test runner">
            <Rows
              rows={[
                { label: "Runner", value: <Mono>{str(tests?.runner) ?? "—"}</Mono> },
                { label: "Grader version", value: <Mono>{str(grading?.version) ?? "—"}</Mono> },
                {
                  label: "Non-empty patch required",
                  value: grading?.require_non_empty_patch === false ? "no" : grading ? "yes" : "—",
                },
                {
                  label: "Baseline verified",
                  value: grading?.verify_baseline === false ? "no" : grading ? "yes" : "—",
                },
                {
                  label: "Model-assisted review",
                  value: judge?.enabled === true ? "enabled (advisory only)" : "disabled",
                },
              ]}
            />
          </Block>
          <Block label="Command">
            {str(tests?.command) ? (
              <CodeBlock className="whitespace-pre-wrap break-words" maxHeight="max-h-32">
                $ {str(tests?.command)}
              </CodeBlock>
            ) : (
              <p className="subtle text-2xs">Not returned by the API.</p>
            )}
          </Block>
        </div>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <Block
            label={`fail → pass · ${task.summary.fail_to_pass_count}`}
            hint="Must fail before the fix and pass after it."
          >
            <TestList tests={failToPass} label="fail_to_pass tests" />
          </Block>
          <Block
            label={`pass → pass · ${task.summary.pass_to_pass_count}`}
            hint="Must keep passing — a failure here is a regression."
          >
            <TestList tests={passToPass} label="pass_to_pass tests" />
          </Block>
        </div>

        <Block label={`Hidden acceptance files · ${task.summary.hidden_acceptance_files.length}`}>
          <PathList paths={task.summary.hidden_acceptance_files} empty="None — graded tests live in the repository." />
          {task.summary.hidden_acceptance_files.length > 0 && (
            <p className="subtle mt-1 text-2xs">
              Names only. Contents are injected into the sandbox after host-side patch capture and are not
              shown here.
            </p>
          )}
        </Block>

        {staticChecks.length > 0 && (
          <Block label="Static checks">
            <ul className="space-y-1">
              {staticChecks.map((check, index) => (
                <li key={`${str(check.name)}-${index}`} className="flex flex-wrap items-center gap-2 text-xs">
                  <Mono className="font-semibold">{str(check.name)}</Mono>
                  <Badge tone="neutral">{check.required === true ? "required" : "advisory"}</Badge>
                  <Mono className="muted break-all">{str(check.command)}</Mono>
                </li>
              ))}
            </ul>
          </Block>
        )}

        <Note>
          Grading-integrity files — <Mono>pytest.ini</Mono>, <Mono>setup.cfg</Mono>, <Mono>tox.ini</Mono>,{" "}
          <Mono>pyproject.toml</Mono> and <Mono>conftest.py</Mono> at any depth — are protected by the grading
          engine itself. A patch that modifies them is rejected before any test runs; tasks cannot opt out.
        </Note>
      </div>
    </Card>
  );
}

function ValidationCard({
  task,
  report,
}: {
  task: Task;
  report: ValidationReport | null;
}) {
  return (
    <Card title="Baseline & validation" subtitle="Validation builds the real environment and checks the task is falsifiable">
      <div className="space-y-3 p-4">
        {report ? (
          <>
            <ChecksList report={report} />
            {report.failed_checks.length > 0 && (
              <p className="text-bad text-2xs">Failed checks: {report.failed_checks.join(", ")}</p>
            )}
          </>
        ) : (
          <p className="muted text-xs">
            Never validated ({task.validation_status}). Validation confirms the environment builds, the
            declared tests are collected, and the target tests fail before a fix.
          </p>
        )}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
function HistorySection({ history, taskId }: { history: TaskHistory; taskId: string }) {
  const failureEntries = Object.entries(history.failure_categories).sort((a, b) => b[1] - a[1]);
  const failureTotal = failureEntries.reduce((sum, [, count]) => sum + count, 0);
  const patches = history.patches;
  const maxPathRuns = Math.max(1, ...patches.paths_touched.map((entry) => entry.runs));

  return (
    <>
      <Card
        title="Results by agent"
        subtitle={`${history.finished_runs} finished of ${history.total_runs} run(s). Rates exclude infrastructure, invalid-task, grading-error and cancelled outcomes. No significance is claimed.`}
      >
        {history.by_agent.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No finished runs" description="No agent has finished a run of this task yet." />
          </div>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Agent</Th>
                <Th>Provenance</Th>
                <Th>Success rate</Th>
                <Th className="hidden sm:table-cell">Interval</Th>
                <Th>Outcomes</Th>
                <Th className="text-right">Excluded</Th>
                <Th className="text-right">Median</Th>
                <Th className="text-right">Last</Th>
              </tr>
            </thead>
            <tbody>
              {history.by_agent.map((row) => (
                <tr key={`${row.agent_config_id}-${row.provenance.value}`}>
                  <Td>
                    <Link to={`/leaderboard/${row.agent_config_id}`} className="link font-mono text-2xs">
                      {row.label}
                    </Link>
                    {row.model && <p className="subtle mt-0.5 font-mono text-2xs">{row.model}</p>}
                  </Td>
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
                  <Td className="tnum text-right">{row.excluded_runs}</Td>
                  <Td className="tnum text-right">{formatDuration(row.runtime.median_s)}</Td>
                  <Td className="subtle text-right text-2xs" title={formatTime(row.last_finished_at)}>
                    {formatRelative(row.last_finished_at)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card
          title="Failure patterns"
          subtitle="Effective classification of finished, unsuccessful runs"
          actions={
            failureTotal > 0 ? (
              <Link to={`/failures?task_id=${encodeURIComponent(taskId)}`} className="accent-text text-2xs font-medium">
                Open in failures →
              </Link>
            ) : undefined
          }
        >
          <div className="p-4">
            {failureEntries.length === 0 ? (
              <p className="muted text-xs">No classified failures for this task.</p>
            ) : (
              <ul className="space-y-2.5">
                {failureEntries.map(([category, count]) => {
                  const unknown = category === "unknown";
                  return (
                    <li key={category}>
                      <div className="flex items-baseline justify-between gap-3 text-xs">
                        <Link
                          to={`/failures?category=${encodeURIComponent(category)}&task_id=${encodeURIComponent(taskId)}`}
                          className="link min-w-0 truncate"
                        >
                          {unknown ? "Unclassified" : category.replace(/_/g, " ")}
                          <Mono className="subtle ml-1.5">{category}</Mono>
                        </Link>
                        <span className="tnum shrink-0">
                          {count}
                          <span className="subtle"> / {failureTotal}</span>
                        </span>
                      </div>
                      <div className="surface-3 mt-1 h-1 overflow-hidden rounded-full">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${(count / failureTotal) * 100}%`,
                            backgroundColor: unknown ? "rgb(var(--text-subtle))" : "rgb(var(--bad))",
                          }}
                        />
                      </div>
                      {unknown && (
                        <p className="subtle mt-0.5 text-2xs">Ambiguous — not classified, not guessed. Review the evidence.</p>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </Card>

        <Card title="Patch details" subtitle="Across finished runs of this task">
          <div className="space-y-4 p-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                { label: "With patch", value: String(patches.runs_with_patch) },
                { label: "Without", value: String(patches.runs_without_patch) },
                { label: "Mean files", value: patches.mean_files_changed?.toString() ?? "—" },
                {
                  label: "Mean +/−",
                  value:
                    patches.mean_insertions === null
                      ? "—"
                      : `+${patches.mean_insertions} / −${patches.mean_deletions ?? "—"}`,
                },
              ].map((entry) => (
                <div key={entry.label}>
                  <p className="eyebrow">{entry.label}</p>
                  <p className="tnum mt-1 text-sm font-semibold">{entry.value}</p>
                </div>
              ))}
            </div>
            <Block label="Paths touched">
              {patches.paths_touched.length === 0 ? (
                <p className="subtle text-2xs">No patches captured.</p>
              ) : (
                <ul className="space-y-1.5">
                  {patches.paths_touched.map((entry) => (
                    <li key={entry.path} className="grid grid-cols-[minmax(0,1fr)_5rem_2.5rem] items-center gap-2">
                      <Mono className="truncate" >{entry.path}</Mono>
                      <span className="surface-3 h-1 overflow-hidden rounded-full">
                        <span
                          className="block h-full rounded-full"
                          style={{ width: `${(entry.runs / maxPathRuns) * 100}%`, backgroundColor: "rgb(var(--accent))" }}
                        />
                      </span>
                      <span className="tnum subtle text-right text-2xs">{entry.runs} run{entry.runs === 1 ? "" : "s"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Block>
          </div>
        </Card>
      </div>

      <Card title="Runs" subtitle={`Most recent ${history.runs.length} of ${history.total_runs}`}>
        {history.runs.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No runs" description="This task has not been included in any experiment yet." />
          </div>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Run</Th>
                <Th>Agent</Th>
                <Th>Provenance</Th>
                <Th>Outcome</Th>
                <Th>Failure</Th>
                <Th className="text-right">Duration</Th>
                <Th>Patch</Th>
                <Th className="text-right">Time</Th>
              </tr>
            </thead>
            <tbody>
              {history.runs.map((run) => {
                const stamp = run.finished_at ?? run.created_at;
                return (
                  <tr key={run.id} className="row-hover">
                    <Td>
                      <Link to={`/runs/${run.id}`} className="link font-mono text-2xs">
                        {run.id.slice(0, 10)}
                      </Link>
                    </Td>
                    <Td>
                      <Mono>{run.agent.label}</Mono>
                    </Td>
                    <Td>
                      <ProvenanceTag value={run.provenance} />
                    </Td>
                    <Td>
                      <OutcomeBadge outcome={run.outcome} />
                    </Td>
                    <Td>
                      {run.failure_category === null ? (
                        <span className="subtle">—</span>
                      ) : run.failure_category === "unknown" ? (
                        <Badge tone="neutral" title="Ambiguous — not classified">
                          unclassified
                        </Badge>
                      ) : (
                        <Mono>{run.failure_category}</Mono>
                      )}
                    </Td>
                    <Td className="tnum text-right">
                      {run.timings.total_s !== undefined ? formatDuration(run.timings.total_s) : "—"}
                    </Td>
                    <Td>
                      {run.patch_stats && run.patch_stats.files_changed > 0 ? (
                        <span className="tnum font-mono text-2xs">
                          {run.patch_stats.files_changed}f <span className="text-good">+{run.patch_stats.insertions}</span>{" "}
                          <span className="text-bad">−{run.patch_stats.deletions}</span>
                        </span>
                      ) : (
                        <span className="subtle">none</span>
                      )}
                    </Td>
                    <Td className="subtle text-right text-2xs" title={formatTime(stamp)}>
                      {formatRelative(stamp)}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}

// --------------------------------------------------------------------------- //
export default function TaskDetail() {
  const { taskKey } = useParams<{ taskKey: string }>();
  const task = useTask(taskKey);
  const history = useTaskHistory(taskKey);
  const validate = useValidateTask();
  const setEnabled = useSetTaskEnabled();

  if (task.isError) return <ErrorState error={task.error} onRetry={() => void task.refetch()} />;
  if (task.isLoading || !task.data) return <LoadingTiles count={4} />;

  const data = task.data;
  const report = validate.data ?? data.validation_report;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={
          <span className="flex flex-wrap items-center gap-1.5 normal-case tracking-normal">
            <Link to="/benchmarks" className="link">
              Benchmarks
            </Link>
            <span aria-hidden>/</span>
            <span className="font-mono">
              {data.benchmark_id}@{data.benchmark_version}
            </span>
          </span>
        }
        title={data.title}
        description={data.description || undefined}
        meta={
          <>
            <Mono className="muted">{data.key}</Mono>
            <ValidationBadge status={data.validation_status} />
            <Badge tone="neutral">{data.category}</Badge>
            <Badge tone="neutral">{data.difficulty}</Badge>
            {!data.enabled && <Badge tone="warn">disabled</Badge>}
            {data.tags.map((tag) => (
              <Badge key={tag} tone="neutral">
                {tag}
              </Badge>
            ))}
          </>
        }
        actions={
          <>
            <Button
              onClick={() => setEnabled.mutate({ taskKey: data.key, enabled: !data.enabled })}
              disabled={setEnabled.isPending}
            >
              {data.enabled ? "Disable task" : "Enable task"}
            </Button>
            <Button variant="primary" disabled={validate.isPending} onClick={() => validate.mutate(data.key)}>
              {validate.isPending ? "Validating in a container…" : "Validate environment"}
            </Button>
          </>
        }
      />

      {validate.isError && <ErrorState error={validate.error} />}
      {setEnabled.isError && <ErrorState error={setEnabled.error} />}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <SpecificationCard task={data} />
          <GradingCard task={data} />
        </div>
        <div className="space-y-6">
          <EnvironmentCard task={data} />
          <LimitsCard task={data} />
          <ValidationCard task={data} report={report ?? null} />
        </div>
      </div>

      <div className="space-y-1">
        <h2 className="text-sm font-semibold tracking-tight">Run history</h2>
        <p className="muted text-xs">
          Every run of <Mono>{data.key}</Mono> across agents and experiments. Simulated and offline results are
          labelled and are not model performance.
        </p>
      </div>
      {history.isError && <ErrorState error={history.error} onRetry={() => void history.refetch()} />}
      {history.isLoading && (
        <Card>
          <LoadingRows rows={5} label="Loading run history" />
        </Card>
      )}
      {history.data && <HistorySection history={history.data} taskId={data.task_id} />}
    </div>
  );
}
