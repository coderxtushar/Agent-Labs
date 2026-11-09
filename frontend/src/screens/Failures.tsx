import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useCapabilities, useExperiments, useFailures, type FailureFilters } from "../api/hooks";
import type { FailureRow } from "../api/types";
import { LayerBadge, OutcomeBadge, formatRelative } from "../components/domain";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  JsonBlock,
  LoadingRows,
  Mono,
  Table,
  Td,
  Th,
  inputClass,
} from "../components/ui";

const EMPTY: FailureFilters = { limit: 100, offset: 0 };

function DistributionChart({ distribution }: { distribution: Record<string, number> }) {
  const data = Object.entries(distribution)
    .map(([category, count]) => ({ category: category.replace(/_/g, " "), count }))
    .sort((a, b) => b.count - a.count);

  if (data.length === 0) return <p className="muted p-4 text-xs">No classified failures.</p>;

  return (
    <div className="p-3">
      <ResponsiveContainer width="100%" height={Math.max(140, data.length * 28)}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10, fill: "currentColor" }} />
          <YAxis
            type="category"
            dataKey="category"
            width={170}
            tick={{ fontSize: 10, fill: "currentColor" }}
          />
          <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
          <Bar dataKey="count" fill="#fb7185" radius={[0, 3, 3, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function FailureDetail({ row }: { row: FailureRow }) {
  return (
    <div className="surface-2 space-y-3 px-3 py-3">
      <p className="text-xs">{row.annotation.rationale}</p>

      {row.grading_summary ? (
        <div className="space-y-1.5">
          <p className="muted text-2xs font-semibold uppercase tracking-wide">Grading layers</p>
          {row.grading_summary.layers.map((layer) => (
            <div key={layer.name} className="flex items-start gap-2 text-2xs">
              <LayerBadge status={layer.status} />
              <Mono className="font-semibold">{layer.name}</Mono>
              <span className="muted min-w-0 flex-1">{layer.summary}</span>
            </div>
          ))}
          {row.grading_summary.blocking_layers.length > 0 && (
            <p className="text-2xs text-rose-400">
              Blocking: {row.grading_summary.blocking_layers.join(", ")}
            </p>
          )}
          {row.grading_summary.errored_layers.length > 0 && (
            <p className="text-2xs text-amber-400">
              Errored: {row.grading_summary.errored_layers.join(", ")}
            </p>
          )}
        </div>
      ) : (
        <p className="muted text-2xs">
          No grading result — this run ended before grading could run.
        </p>
      )}

      <div>
        <p className="muted text-2xs font-semibold uppercase tracking-wide">Evidence references</p>
        <JsonBlock value={row.annotation.evidence_refs} maxHeight="max-h-56" />
      </div>

      <Link to={`/runs/${row.run.id}`} className="accent-text text-2xs font-medium">
        Open full run →
      </Link>
    </div>
  );
}

export default function Failures() {
  const [filters, setFilters] = useState<FailureFilters>(EMPTY);
  const [expanded, setExpanded] = useState<string | null>(null);
  const failures = useFailures(filters);
  const capabilities = useCapabilities();
  const experiments = useExperiments();

  const hasFilters = Boolean(
    filters.category || filters.experiment_id || filters.source || filters.search || filters.task_id,
  );
  const update = (patch: Partial<FailureFilters>) =>
    setFilters((current) => ({ ...current, ...patch, offset: 0 }));

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Failure analysis</h1>

      {failures.data && (
        <Card
          title="Category distribution"
          subtitle="Effective classification per failed run; human corrections supersede automatic ones"
        >
          <DistributionChart distribution={failures.data.distribution} />
        </Card>
      )}

      <Card
        title="Failures"
        subtitle={failures.data ? `${failures.data.pagination.total} total` : undefined}
        actions={
          hasFilters ? <Button onClick={() => setFilters(EMPTY)}>Clear filters</Button> : undefined
        }
      >
        <div className="hairline grid grid-cols-1 gap-2 border-b p-3 sm:grid-cols-2 lg:grid-cols-4">
          <input
            className={inputClass}
            placeholder="Search rationale or task…"
            value={filters.search ?? ""}
            onChange={(event) => update({ search: event.target.value })}
          />
          <select
            className={inputClass}
            value={filters.category ?? ""}
            onChange={(event) => update({ category: event.target.value || undefined })}
          >
            <option value="">All categories</option>
            {(capabilities.data?.vocabularies.failure_categories ?? []).map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
          <select
            className={inputClass}
            value={filters.experiment_id ?? ""}
            onChange={(event) => update({ experiment_id: event.target.value || undefined })}
          >
            <option value="">All experiments</option>
            {(experiments.data?.items ?? []).map((experiment) => (
              <option key={experiment.id} value={experiment.id}>
                {experiment.name}
              </option>
            ))}
          </select>
          <select
            className={inputClass}
            value={filters.source ?? ""}
            onChange={(event) => update({ source: event.target.value || undefined })}
          >
            <option value="">Any source</option>
            <option value="rule">rule</option>
            <option value="model">model (model-generated)</option>
            <option value="human">human</option>
          </select>
        </div>

        {failures.isError && (
          <div className="p-4">
            <ErrorState error={failures.error} onRetry={() => void failures.refetch()} />
          </div>
        )}
        {failures.isLoading && <LoadingRows rows={6} label="Loading failures" />}

        {failures.data && failures.data.items.length === 0 && (
          <div className="p-4">
            <EmptyState
              title={hasFilters ? "No failures match these filters" : "No failures recorded"}
              description={
                hasFilters
                  ? "Try widening the filters."
                  : "Every finished run either succeeded or has not been classified yet."
              }
              action={
                hasFilters ? <Button onClick={() => setFilters(EMPTY)}>Clear filters</Button> : undefined
              }
            />
          </div>
        )}

        {failures.data && failures.data.items.length > 0 && (
          <Table>
            <thead>
              <tr>
                <Th>Category</Th>
                <Th>Source</Th>
                <Th>Task</Th>
                <Th>Run</Th>
                <Th>Outcome</Th>
                <Th>Agent</Th>
                <Th>Recorded</Th>
              </tr>
            </thead>
            <tbody>
              {failures.data.items.map((row) => (
                <Fragment key={row.annotation.id}>
                  <tr
                    onClick={() =>
                      setExpanded(expanded === row.annotation.id ? null : row.annotation.id)
                    }
                    className="cursor-pointer hover:surface-2"
                  >
                    <Td>
                      <Badge tone="bad">{row.annotation.category}</Badge>
                    </Td>
                    <Td>
                      <Badge tone={row.annotation.model_generated ? "warn" : "neutral"}>
                        {row.annotation.source}
                      </Badge>
                    </Td>
                    <Td>
                      <Mono>{row.run.task_key}</Mono>
                    </Td>
                    <Td>
                      <Mono className="muted">{row.run.id.slice(0, 10)}</Mono>
                    </Td>
                    <Td>
                      <OutcomeBadge outcome={row.run.outcome} />
                    </Td>
                    <Td>
                      <Mono>{row.run.agent.label}</Mono>
                    </Td>
                    <Td className="muted text-2xs">{formatRelative(row.annotation.created_at)}</Td>
                  </tr>
                  {expanded === row.annotation.id && (
                    <tr>
                      <Td colSpan={7} className="p-0">
                        <FailureDetail row={row} />
                      </Td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
