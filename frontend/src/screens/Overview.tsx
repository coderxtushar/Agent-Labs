import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useOverview, useStats } from "../api/hooks";
import type { RunOutcome } from "../api/types";
import {
  COUNTED_OUTCOMES,
  EXCLUDED_OUTCOMES,
  OUTCOME_META,
  RateDisplay,
  SimulatedBadge,
  formatRelative,
} from "../components/domain";
import {
  Badge,
  Card,
  CodeBlock,
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadingTiles,
  Mono,
  StatTile,
  Table,
  Td,
  Th,
} from "../components/ui";

const ONBOARDING = `# 1. create the schema, import the bundled benchmark and demo agents
agentbench bootstrap

# 2. prove each task is executable AND falsifiable (builds real containers)
agentbench benchmark validate

# 3. run the bundled benchmark with the known-good reference agent
agentbench experiment create --name demo --agent reference-solution
agentbench experiment run`;

function OutcomeChart({ byOutcome }: { byOutcome: Partial<Record<RunOutcome, number>> }) {
  const data = [...COUNTED_OUTCOMES, ...EXCLUDED_OUTCOMES]
    .map((outcome) => ({
      outcome,
      label: OUTCOME_META[outcome].label,
      count: byOutcome[outcome] ?? 0,
      color: OUTCOME_META[outcome].color,
      counted: OUTCOME_META[outcome].counted,
    }))
    .filter((entry) => entry.count > 0);

  if (data.length === 0) {
    return <p className="muted p-4 text-xs">No finished runs yet.</p>;
  }

  return (
    <div className="p-3">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10, fill: "currentColor" }} />
          <YAxis
            type="category"
            dataKey="label"
            width={92}
            tick={{ fontSize: 10, fill: "currentColor" }}
          />
          <Tooltip
            contentStyle={{ fontSize: 11, borderRadius: 6 }}
            formatter={(value, _name, item) => {
              const counted = (item as { payload?: { counted?: boolean } }).payload?.counted;
              return [
                `${String(value)} run(s)`,
                counted ? "counts toward success rate" : "excluded from success rates",
              ];
            }}
          />
          <Bar dataKey="count" radius={[0, 3, 3, 0]} isAnimationActive={false}>
            {data.map((entry) => (
              <Cell key={entry.outcome} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <p className="muted mt-1 px-1 text-2xs">
        Saturated bars count toward the agent-attributable success rate. Grey bars are excluded
        because they are not attributable to the agent.
      </p>
    </div>
  );
}

function FailureChart({ categories }: { categories: Record<string, number> }) {
  const data = Object.entries(categories)
    .map(([category, count]) => ({ category: category.replace(/_/g, " "), count }))
    .sort((a, b) => b.count - a.count);

  if (data.length === 0) return <p className="muted p-4 text-xs">No classified failures yet.</p>;

  return (
    <div className="p-3">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10, fill: "currentColor" }} />
          <YAxis
            type="category"
            dataKey="category"
            width={150}
            tick={{ fontSize: 10, fill: "currentColor" }}
          />
          <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
          <Bar dataKey="count" fill="#818cf8" radius={[0, 3, 3, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function Overview() {
  const stats = useStats();
  const overview = useOverview();

  if (stats.isError) return <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />;
  if (stats.isLoading || !stats.data) return <LoadingTiles count={4} />;

  if (stats.data.is_empty) {
    return (
      <div className="space-y-4">
        <h1 className="text-lg font-semibold">Overview</h1>
        <EmptyState
          title="No benchmarks or experiments yet"
          description={
            <div className="space-y-3 text-left">
              <p>
                Nothing has been imported or executed. Run these commands from the repository root
                to import the bundled <Mono>textkit</Mono> benchmark and execute it with the
                deterministic reference agent.
              </p>
              <CodeBlock maxHeight="max-h-80">{ONBOARDING}</CodeBlock>
              <p className="muted">
                The benchmark image must exist first: <Mono>agentbench images build</Mono>. Images
                are never pulled during a run, so benchmarks stay offline-safe.
              </p>
            </div>
          }
        />
      </div>
    );
  }

  if (overview.isError) {
    return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  }
  if (overview.isLoading || !overview.data) return <LoadingTiles count={4} />;

  const data = overview.data;
  const rate = data.runs.overall_success_rate_attributable;

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-lg font-semibold">Overview</h1>
        <span className="muted text-2xs">computed {formatRelative(data.computed_at)}</span>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Experiments"
          value={data.experiments.total}
          hint={Object.entries(data.experiments.by_state)
            .map(([state, count]) => `${count} ${state}`)
            .join(" · ")}
        />
        <StatTile
          label="Runs"
          value={data.runs.total}
          hint={`${stats.data.tasks} task(s) · ${stats.data.agents} agent(s)`}
        />
        <StatTile
          label="Active runs"
          value={data.runs.active}
          tone={data.runs.active > 0 ? "warn" : undefined}
          hint={data.runs.active > 0 ? "work in flight" : "idle"}
        />
        <div className="surface hairline rounded-lg border p-4">
          <RateDisplay
            rate={rate}
            label="Success rate (agent-attributable)"
            size="lg"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Run outcomes" subtitle="All finished runs, by attribution">
          <OutcomeChart byOutcome={data.runs.by_outcome} />
        </Card>
        <Card
          title="Failure categories"
          subtitle="Effective classification per failed run"
          actions={
            <Link to="/failures" className="accent-text text-2xs font-medium">
              Inspect →
            </Link>
          }
        >
          <FailureChart categories={data.failure_categories} />
        </Card>
      </div>

      <Card
        title="Recent experiments"
        subtitle={`${data.experiments.recent.length} most recent`}
        actions={
          <Link to="/experiments" className="accent-text text-2xs font-medium">
            All experiments →
          </Link>
        }
      >
        {data.experiments.recent.length === 0 ? (
          <LoadingRows rows={1} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>State</Th>
                <Th>Agent</Th>
                <Th>Benchmark</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {data.experiments.recent.map((experiment) => (
                <tr key={experiment.id} className="hover:surface-2">
                  <Td>
                    <Link
                      to={`/experiments/${experiment.id}`}
                      className="accent-text font-medium hover:underline"
                    >
                      {experiment.name}
                    </Link>
                    <Mono className="muted ml-2">{experiment.id.slice(0, 8)}</Mono>
                  </Td>
                  <Td>
                    <Badge tone={experiment.state === "completed" ? "accent" : "info"}>
                      {experiment.state}
                    </Badge>
                  </Td>
                  <Td>
                    <div className="flex items-center gap-1.5">
                      <Mono>{experiment.agent}</Mono>
                      {experiment.is_simulated && <SimulatedBadge compact />}
                    </div>
                  </Td>
                  <Td>
                    <Mono>{experiment.benchmark}</Mono>
                  </Td>
                  <Td className="muted">{formatRelative(experiment.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
