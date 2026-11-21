import { Fragment, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useExperiments, useFailures } from "../api/hooks";
import type { FailureRow } from "../api/types";
import { LayerBadge, OutcomeBadge, SimulatedBadge, formatRelative, formatTime } from "../components/domain";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  JsonBlock,
  LoadingRows,
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  SearchInput,
  Select,
  Table,
  Td,
  Th,
  cx,
} from "../components/ui";

// --------------------------------------------------------------------------- //
// buckets — presentation grouping only; the raw category is always shown too
// --------------------------------------------------------------------------- //
interface Bucket {
  id: string;
  label: string;
  description: string;
  categories: string[];
  excluded?: boolean;
  color: string;
}

const BUCKETS: Bucket[] = [
  { id: "timeouts", label: "Timeouts", description: "Agent ran out of time", categories: ["timeout"], color: "--warn" },
  {
    id: "incorrect",
    label: "Incorrect patches",
    description: "A patch was produced but the target tests still fail",
    categories: ["incorrect_implementation", "tests_failing_after_patch"],
    color: "--bad",
  },
  {
    id: "no_patch",
    label: "No patch produced",
    description: "The workspace was left unchanged",
    categories: ["no_patch_produced"],
    color: "--bad",
  },
  {
    id: "regressions",
    label: "Regressions",
    description: "Previously passing tests broke",
    categories: ["existing_regression"],
    color: "--bad",
  },
  {
    id: "tool_errors",
    label: "Tool errors",
    description: "Tool invocations failed",
    categories: ["tool_invocation_error"],
    color: "--bad",
  },
  {
    id: "tampering",
    label: "Test tampering",
    description: "Patch touched forbidden or grading-integrity files",
    categories: ["forbidden_files_modified"],
    color: "--warn",
  },
  {
    id: "infra",
    label: "Infrastructure errors",
    description: "Sandbox, environment or dependency failures",
    categories: ["infrastructure_failure", "environment_setup_failure", "missing_dependency"],
    excluded: true,
    color: "--text-subtle",
  },
  {
    id: "grading",
    label: "Grading errors",
    description: "Grading could not reach a verdict",
    categories: ["grading_failure"],
    excluded: true,
    color: "--text-subtle",
  },
  {
    id: "other",
    label: "Other",
    description: "Context or instruction issues, invalid tasks",
    categories: ["context_or_instruction_issue", "invalid_task"],
    color: "--info",
  },
  {
    id: "unclassified",
    label: "Unclassified",
    description: "Ambiguous — not classified; review evidence",
    categories: ["unknown"],
    color: "--text-subtle",
  },
];

/** Categories whose runs are excluded from success-rate denominators. */
const EXCLUDED_CATEGORIES = new Set([
  "infrastructure_failure",
  "environment_setup_failure",
  "missing_dependency",
  "grading_failure",
  "invalid_task",
]);

const KNOWN = new Set(BUCKETS.flatMap((bucket) => bucket.categories));

function bucketOf(category: string): Bucket {
  return (
    BUCKETS.find((bucket) => bucket.categories.includes(category)) ??
    (BUCKETS.find((bucket) => bucket.id === "other") as Bucket)
  );
}

const PAGE_SIZE = 50;

function CategoryLabel({ category }: { category: string }) {
  if (category === "unknown") {
    return (
      <Badge tone="neutral" title="Ambiguous — not classified; review the evidence">
        unclassified <span className="opacity-60">· unknown</span>
      </Badge>
    );
  }
  return (
    <Badge tone={EXCLUDED_CATEGORIES.has(category) ? "neutral" : "bad"}>
      {category}
      {EXCLUDED_CATEGORIES.has(category) && <span className="opacity-60">· excluded</span>}
    </Badge>
  );
}

function FailureDetail({ row }: { row: FailureRow }) {
  const annotation = row.annotation;
  return (
    <div className="surface-2 space-y-3 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-2xs">
        <span className="muted">
          Classified by <span className="font-medium text-[rgb(var(--text))]">{annotation.source}</span>
          {annotation.author && ` (${annotation.author})`}
          {annotation.model_generated && " — model-generated, treat as a suggestion"}
        </span>
        {annotation.confidence !== null && <Badge tone="neutral">confidence {annotation.confidence}</Badge>}
        {annotation.supersedes_id && <Badge tone="info">supersedes an earlier classification</Badge>}
        <span className="subtle">{formatTime(annotation.created_at)}</span>
      </div>

      <div>
        <p className="eyebrow">Rationale</p>
        <p className="mt-1 text-xs">{annotation.rationale || <span className="subtle">No rationale recorded.</span>}</p>
      </div>

      {row.grading_summary ? (
        <div className="space-y-1.5">
          <p className="eyebrow">
            Grading ·{" "}
            {row.grading_summary.passed === true
              ? "passed"
              : row.grading_summary.passed === false
                ? "failed"
                : "no verdict"}{" "}
            <span className="tnum normal-case tracking-normal">
              · tests {row.grading_summary.tests.passed} passed, {row.grading_summary.tests.failed} failed,{" "}
              {row.grading_summary.tests.errored} errored
            </span>
          </p>
          {row.grading_summary.layers.map((layer) => (
            <div key={layer.name} className="flex items-start gap-2 text-2xs">
              <LayerBadge status={layer.status} />
              <Mono className="font-semibold">{layer.name}</Mono>
              <span className="muted min-w-0 flex-1">{layer.summary}</span>
            </div>
          ))}
          {row.grading_summary.blocking_layers.length > 0 && (
            <p className="text-bad text-2xs">Blocking: {row.grading_summary.blocking_layers.join(", ")}</p>
          )}
          {row.grading_summary.errored_layers.length > 0 && (
            <p className="text-warn text-2xs">Errored: {row.grading_summary.errored_layers.join(", ")}</p>
          )}
        </div>
      ) : (
        <p className="muted text-2xs">No grading result — this run ended before grading could run.</p>
      )}

      {row.run.termination_reason && (
        <div>
          <p className="eyebrow">Termination reason</p>
          <p className="muted mt-1 break-words text-2xs">{row.run.termination_reason}</p>
        </div>
      )}

      <div>
        <p className="eyebrow mb-1">Evidence references</p>
        <JsonBlock value={annotation.evidence_refs} maxHeight="max-h-56" />
      </div>

      <div className="flex flex-wrap gap-4">
        <Link to={`/runs/${row.run.id}`} className="accent-text text-2xs font-medium">
          Open trajectory →
        </Link>
        <Link to={`/runs/${row.run.id}?tab=failure`} className="accent-text text-2xs font-medium">
          Correct the classification →
        </Link>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
export default function Failures() {
  const [params, setParams] = useSearchParams();
  const category = params.get("category") ?? "";
  const bucketParam = params.get("bucket") ?? "";
  const experimentId = params.get("experiment") ?? params.get("experiment_id") ?? "";
  const source = params.get("source") ?? "";
  const search = params.get("q") ?? "";
  const taskId = params.get("task_id") ?? "";
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  const [expanded, setExpanded] = useState<string | null>(null);

  // A category always implies its bucket; a bucket alone means "all its categories".
  const activeBucket = category ? bucketOf(category) : BUCKETS.find((bucket) => bucket.id === bucketParam);
  const bucketOnly = !category && activeBucket !== undefined;

  const update = (patch: Record<string, string | null>, resetOffset = true) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("experiment_id");
        for (const [key, value] of Object.entries(patch)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        if (resetOffset) next.delete("offset");
        return next;
      },
      { replace: true },
    );

  // A bucket asks the server for all of its categories at once (`category=a,b`),
  // so every page of a multi-category bucket is complete and correctly counted.
  const serverCategory = category || (bucketOnly && activeBucket ? activeBucket.categories.join(",") : "");

  const failures = useFailures({
    category: serverCategory || undefined,
    experiment_id: experimentId || undefined,
    source: source || undefined,
    search: search || undefined,
    task_id: taskId || undefined,
    limit: PAGE_SIZE,
    offset,
  });
  const experiments = useExperiments();

  const distribution = useMemo(() => failures.data?.distribution ?? {}, [failures.data]);
  const totalClassified = Object.values(distribution).reduce((sum, count) => sum + count, 0);

  const buckets = useMemo(() => {
    const extra = Object.keys(distribution).filter((name) => !KNOWN.has(name));
    return BUCKETS.map((bucket) => {
      const categories = bucket.id === "other" ? [...bucket.categories, ...extra] : bucket.categories;
      const entries = categories.map((name) => [name, distribution[name] ?? 0] as const);
      return { ...bucket, categories, entries, count: entries.reduce((sum, [, count]) => sum + count, 0) };
    });
  }, [distribution]);
  const activeBucketView = activeBucket ? buckets.find((bucket) => bucket.id === activeBucket.id) : undefined;

  const items = failures.data?.items ?? [];

  const hasFilters = Boolean(category || bucketParam || experimentId || source || search || taskId);
  const clearAll = () =>
    update({ category: null, bucket: null, experiment: null, source: null, q: null, task_id: null });
  const pagination = failures.data?.pagination;
  const attributable = buckets.filter((bucket) => !bucket.excluded && bucket.id !== "unclassified");
  const attributableCount = attributable.reduce((sum, bucket) => sum + bucket.count, 0);
  const excludedCount = buckets.filter((bucket) => bucket.excluded).reduce((sum, bucket) => sum + bucket.count, 0);
  const unclassifiedCount = distribution.unknown ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Failure analysis"
        description="Why runs did not succeed, from the effective classification of each failed run. Rule- and model-generated labels can be corrected by a human; corrections supersede, never delete."
        meta={
          failures.data ? (
            <span className="muted tnum text-2xs">
              {totalClassified} classified · {attributableCount} agent-side · {excludedCount} excluded from rates ·{" "}
              {unclassifiedCount} unclassified
            </span>
          ) : undefined
        }
      />

      {failures.isLoading && <LoadingTiles count={8} />}

      {failures.data && (
        <section aria-label="Failure buckets" className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {buckets.map((bucket) => {
              const active = activeBucket?.id === bucket.id;
              const share = totalClassified > 0 ? (bucket.count / totalClassified) * 100 : 0;
              return (
                <button
                  key={bucket.id}
                  type="button"
                  aria-pressed={active}
                  disabled={bucket.count === 0 && !active}
                  onClick={() =>
                    active
                      ? update({ bucket: null, category: null })
                      : update({
                          bucket: bucket.id,
                          category: bucket.categories.length === 1 ? bucket.categories[0] : null,
                        })
                  }
                  className={cx(
                    "surface elevated flex flex-col rounded-xl border p-3.5 text-left transition-colors duration-150 disabled:cursor-default disabled:opacity-50",
                    active ? "border-[rgb(var(--accent)/0.6)]" : "hairline enabled:hover:border-[rgb(var(--border-strong))]",
                    bucket.id === "unclassified" && "border-dashed",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-xs font-semibold tracking-tight">{bucket.label}</p>
                    <p className="tnum text-lg font-semibold leading-none">{bucket.count}</p>
                  </div>
                  <p className="subtle mt-1 text-2xs leading-snug">{bucket.description}</p>
                  <div className="surface-3 mt-2.5 h-1 overflow-hidden rounded-full" aria-hidden>
                    <div className="h-full rounded-full" style={{ width: `${share}%`, backgroundColor: `rgb(var(${bucket.color}))` }} />
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-2 gap-y-0.5">
                    {bucket.entries.map(([name, count]) => (
                      <span key={name} className="subtle font-mono text-2xs">
                        {name} <span className="tnum">{count}</span>
                      </span>
                    ))}
                  </div>
                  <p className="subtle tnum mt-auto pt-2 text-2xs">
                    {totalClassified > 0 ? `${bucket.count} of ${totalClassified} classified` : "no classified failures"}
                    {bucket.excluded && <span className="text-[rgb(var(--text-muted))]"> · excluded from rates</span>}
                  </p>
                </button>
              );
            })}
          </div>
          <Note>
            Buckets group raw categories for reading; the raw category is always shown. Infrastructure and grading
            errors are not agent failures and are excluded from success-rate denominators. "Unclassified" means the
            evidence was ambiguous and nothing was guessed. Counts respect every filter except the category you pick.
          </Note>
        </section>
      )}

      <Card
        title={activeBucketView ? `Failures · ${activeBucketView.label}` : "Failures"}
        subtitle={
          pagination ? `${pagination.total} matching` : undefined
        }
        actions={hasFilters ? <Button variant="ghost" onClick={clearAll}>Clear filters</Button> : undefined}
      >
        {activeBucketView && activeBucketView.categories.length > 1 && (
          <div className="hairline flex flex-wrap items-center gap-1.5 border-b px-3 py-2.5" role="group" aria-label="Categories in bucket">
            <span className="eyebrow mr-1">Category</span>
            <button
              type="button"
              aria-pressed={!category}
              onClick={() => update({ bucket: activeBucketView.id, category: null })}
              className={cx(
                "hairline rounded-md border px-2 py-1 text-2xs font-medium transition-colors",
                !category ? "accent-soft-bg accent-text border-transparent" : "muted hover:text-[rgb(var(--text))]",
              )}
            >
              All in bucket <span className="tnum opacity-60">{activeBucketView.count}</span>
            </button>
            {activeBucketView.entries.map(([name, count]) => (
              <button
                key={name}
                type="button"
                aria-pressed={category === name}
                disabled={count === 0 && category !== name}
                onClick={() => update({ bucket: activeBucketView.id, category: name })}
                className={cx(
                  "hairline rounded-md border px-2 py-1 font-mono text-2xs transition-colors disabled:opacity-40",
                  category === name ? "accent-soft-bg accent-text border-transparent" : "muted hover:text-[rgb(var(--text))]",
                )}
              >
                {name} <span className="tnum opacity-60">{count}</span>
              </button>
            ))}
          </div>
        )}

        <div className="hairline flex flex-wrap items-center gap-2 border-b p-3">
          <SearchInput
            value={search}
            onChange={(value) => update({ q: value || null })}
            placeholder="Search rationale, task or reason…"
            label="Search failures"
            className="min-w-[14rem] flex-1"
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
          <Select
            label="Source"
            value={source}
            onChange={(value) => update({ source: value || null })}
            options={[
              { value: "", label: "Any source" },
              { value: "rule", label: "rule" },
              { value: "model", label: "model (model-generated)" },
              { value: "human", label: "human" },
            ]}
          />
          {taskId && (
            <Badge tone="accent">
              task <span className="font-mono">{taskId}</span>
              <button
                type="button"
                aria-label="Remove task filter"
                onClick={() => update({ task_id: null })}
                className="ml-0.5 opacity-70 hover:opacity-100"
              >
                ×
              </button>
            </Badge>
          )}
        </div>

        {failures.isError && (
          <div className="p-4">
            <ErrorState error={failures.error} onRetry={() => void failures.refetch()} />
          </div>
        )}
        {failures.isLoading && <LoadingRows rows={6} label="Loading failures" />}

        {failures.data && items.length === 0 && (
          <div className="p-4">
            <EmptyState
              title={hasFilters ? "No failures match these filters" : "No failures recorded"}
              description={
                hasFilters ? "Try widening the filters." : "Every finished run either succeeded or has not been classified yet."
              }
              action={hasFilters ? <Button onClick={clearAll}>Clear filters</Button> : undefined}
            />
          </div>
        )}

        {items.length > 0 && (
          <Table>
            <thead>
              <tr>
                <Th>Category</Th>
                <Th>Source</Th>
                <Th>Task</Th>
                <Th>Run</Th>
                <Th>Outcome</Th>
                <Th>Agent</Th>
                <Th className="text-right">Recorded</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => {
                const open = expanded === row.annotation.id;
                const toggle = () => setExpanded(open ? null : row.annotation.id);
                return (
                  <Fragment key={row.annotation.id}>
                    <tr
                      onClick={(event) => {
                        if ((event.target as HTMLElement).closest("a,button")) return;
                        toggle();
                      }}
                      className={cx("row-hover cursor-pointer", open && "surface-2")}
                    >
                      <Td>
                        <button
                          type="button"
                          aria-expanded={open}
                          aria-label={`${open ? "Hide" : "Show"} details for ${row.annotation.category}`}
                          onClick={toggle}
                          className="flex items-center gap-1.5 text-left"
                        >
                          <svg
                            aria-hidden
                            viewBox="0 0 16 16"
                            className={cx("subtle h-3 w-3 shrink-0 transition-transform duration-150", open && "rotate-90")}
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.8"
                          >
                            <path d="m6 4 4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                          <CategoryLabel category={row.annotation.category} />
                        </button>
                        <p className="subtle ml-[18px] mt-0.5 text-2xs">{bucketOf(row.annotation.category).label}</p>
                      </Td>
                      <Td>
                        <Badge tone={row.annotation.model_generated ? "warn" : row.annotation.source === "human" ? "info" : "neutral"}>
                          {row.annotation.source}
                        </Badge>
                      </Td>
                      <Td className="max-w-[14rem]">
                        <Link
                          to={`/benchmarks/tasks/${encodeURIComponent(row.run.task_key)}`}
                          className="link block truncate font-mono text-2xs"
                        >
                          {row.run.task_key}
                        </Link>
                      </Td>
                      <Td>
                        <Link to={`/runs/${row.run.id}`} className="link font-mono text-2xs">
                          {row.run.id.slice(0, 10)}
                        </Link>
                      </Td>
                      <Td>
                        <OutcomeBadge outcome={row.run.outcome} />
                      </Td>
                      <Td>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Mono>{row.run.agent.label}</Mono>
                          {row.run.agent.is_simulated && <SimulatedBadge compact />}
                        </div>
                      </Td>
                      <Td className="subtle whitespace-nowrap text-right text-2xs" title={formatTime(row.annotation.created_at)}>
                        {formatRelative(row.annotation.created_at)}
                      </Td>
                    </tr>
                    {open && (
                      <tr>
                        <Td colSpan={7} className="p-0">
                          <FailureDetail row={row} />
                        </Td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </Table>
        )}

        {pagination && pagination.total > PAGE_SIZE && (
          <div className="hairline flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2.5">
            <p className="subtle tnum text-2xs">
              {pagination.offset + 1}–{Math.min(pagination.offset + pagination.limit, pagination.total)} of{" "}
              {pagination.total}
            </p>
            <div className="flex items-center gap-1.5">
              <Button
                disabled={offset === 0}
                onClick={() => update({ offset: offset - PAGE_SIZE > 0 ? String(offset - PAGE_SIZE) : null }, false)}
              >
                Previous
              </Button>
              <Button disabled={!pagination.has_more} onClick={() => update({ offset: String(offset + PAGE_SIZE) }, false)}>
                Next
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
