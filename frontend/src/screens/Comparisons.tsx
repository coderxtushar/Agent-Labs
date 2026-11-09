import { useState } from "react";
import { Link } from "react-router-dom";

import { downloadUrl } from "../api/client";
import { useComparison, useExperiments } from "../api/hooks";
import type { TaskDelta } from "../api/types";
import {
  BlockerList,
  OptionalNumber,
  ProvenanceBadge,
  RateDisplay,
  WarningList,
  formatDuration,
} from "../components/domain";
import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  Field,
  KeyValue,
  LinkButton,
  LoadingTiles,
  Mono,
  Table,
  Td,
  Th,
  inputClass,
} from "../components/ui";

const MOVEMENT_TONES: Record<string, "good" | "bad" | "warn" | "neutral" | "info"> = {
  newly_solved: "good",
  improved: "good",
  unchanged: "neutral",
  degraded: "warn",
  newly_broken: "bad",
  not_comparable: "warn",
};

const MOVEMENT_ORDER = [
  "newly_solved",
  "improved",
  "unchanged",
  "degraded",
  "newly_broken",
  "not_comparable",
];

function signed(value: number | null, suffix = ""): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value}${suffix}`;
}

export default function Comparisons() {
  const experiments = useExperiments();
  const [baseline, setBaseline] = useState("");
  const [candidate, setCandidate] = useState("");
  const comparison = useComparison(baseline || undefined, candidate || undefined);

  const options = experiments.data?.items ?? [];

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Comparisons</h1>

      <Card
        title="Select two experiments"
        subtitle="Comparability is checked, not assumed: differing task sets, grading versions or simulated-vs-real agents are flagged."
      >
        <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
          <Field label="Baseline">
            <select
              className={inputClass}
              value={baseline}
              onChange={(event) => setBaseline(event.target.value)}
            >
              <option value="">Select baseline…</option>
              {options.map((experiment) => (
                <option key={experiment.id} value={experiment.id}>
                  {experiment.name} — {experiment.agent.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Candidate">
            <select
              className={inputClass}
              value={candidate}
              onChange={(event) => setCandidate(event.target.value)}
            >
              <option value="">Select candidate…</option>
              {options.map((experiment) => (
                <option key={experiment.id} value={experiment.id}>
                  {experiment.name} — {experiment.agent.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Card>

      {(!baseline || !candidate) && (
        <EmptyState
          title="Pick a baseline and a candidate"
          description="The comparison reports per-task movement, not just an aggregate delta — a net-zero success rate can hide three fixes and three breakages."
        />
      )}

      {baseline && candidate && baseline === candidate && (
        <ErrorState error={new Error("Baseline and candidate must be different experiments.")} />
      )}

      {comparison.isError && (
        <ErrorState error={comparison.error} onRetry={() => void comparison.refetch()} />
      )}
      {comparison.isLoading && baseline && candidate && <LoadingTiles count={4} />}

      {comparison.data && (
        <div className="space-y-4">
          <Card title="Summary">
            <div className="space-y-3 p-4">
              <p className="text-sm">{comparison.data.summary}</p>
              <BlockerList blockers={comparison.data.blockers} />
              <WarningList warnings={comparison.data.caveats} title="Needs more testing" />
              <LinkButton
                href={downloadUrl("/compare/report", { baseline, candidate })}
              >
                Download comparison report (.md)
              </LinkButton>
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {([comparison.data.baseline, comparison.data.candidate] as const).map((arm, index) => (
              <Card
                key={arm.experiment_id}
                title={index === 0 ? "Baseline" : "Candidate"}
                subtitle={arm.name}
                actions={
                  <Link
                    to={`/experiments/${arm.experiment_id}`}
                    className="accent-text text-2xs font-medium"
                  >
                    Open →
                  </Link>
                }
              >
                <div className="space-y-3 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Mono>{arm.agent}</Mono>
                    <ProvenanceBadge provenance={arm.provenance} compact />
                  </div>
                  <RateDisplay
                    rate={arm.success_rate_attributable}
                    label="Success rate (agent-attributable)"
                    size="lg"
                  />
                  <KeyValue
                    rows={[
                      ["Model", <Mono>{arm.model ?? "—"}</Mono>],
                      ["Solved", arm.outcomes.solved],
                      ["Unsolved", arm.outcomes.unsolved],
                      ["Timeout", arm.outcomes.timeout],
                      ["Agent error", arm.outcomes.agent_error],
                      ["Excluded (infra/invalid/grading/cancelled)",
                        arm.outcomes.infra_error +
                          arm.outcomes.invalid_task +
                          arm.outcomes.grading_error +
                          arm.outcomes.cancelled],
                      ["Runtime", formatDuration(arm.runtime.total_s)],
                      ["Cost (USD)", <OptionalNumber value={arm.usage.cost_usd_total} />],
                    ]}
                  />
                </div>
              </Card>
            ))}
          </div>

          <Card title="Delta">
            <div className="p-4">
              <KeyValue
                rows={[
                  [
                    "Success rate",
                    comparison.data.delta.success_rate === null
                      ? "—"
                      : `${signed(Math.round(comparison.data.delta.success_rate * 1000) / 10, "pp")}`,
                  ],
                  ["Solved", signed(comparison.data.delta.solved)],
                  ["Regressions", signed(comparison.data.delta.regressions)],
                  ["Timeouts", signed(comparison.data.delta.timeouts)],
                  ["Agent errors", signed(comparison.data.delta.agent_errors)],
                  ["Runtime", signed(comparison.data.delta.runtime_total_s, "s")],
                  ["Cost (USD)", signed(comparison.data.delta.cost_usd)],
                ]}
              />
            </div>
          </Card>

          <Card title="Movement" subtitle="Which tasks changed verdict, not just the aggregate">
            <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
              {MOVEMENT_ORDER.map((bucket) => {
                const keys = comparison.data.movement[bucket] ?? [];
                return (
                  <div key={bucket} className="hairline rounded-md border p-3">
                    <div className="flex items-center gap-2">
                      <Badge tone={MOVEMENT_TONES[bucket] ?? "neutral"}>
                        {bucket.replace(/_/g, " ")}
                      </Badge>
                      <span className="tnum muted text-2xs">{keys.length}</span>
                    </div>
                    <ul className="mt-2 space-y-0.5">
                      {keys.length === 0 && <li className="muted text-2xs">none</li>}
                      {keys.map((key) => (
                        <li key={key}>
                          <Mono>{key}</Mono>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card title="Per-task delta">
            <Table>
              <thead>
                <tr>
                  <Th>Task</Th>
                  <Th>Category</Th>
                  <Th className="text-right">Baseline</Th>
                  <Th className="text-right">Candidate</Th>
                  <Th className="text-right">Delta</Th>
                  <Th>Movement</Th>
                </tr>
              </thead>
              <tbody>
                {comparison.data.task_deltas.map((delta: TaskDelta) => (
                  <tr key={delta.task_key} className="hover:surface-2">
                    <Td>
                      <Mono className="font-semibold">{delta.task_key}</Mono>
                      <p className="muted mt-0.5 text-2xs">{delta.title}</p>
                    </Td>
                    <Td>
                      <Badge tone="neutral">{delta.category}</Badge>
                    </Td>
                    <Td className="tnum text-right">
                      {delta.baseline.solved}/{delta.baseline.attempts}
                    </Td>
                    <Td className="tnum text-right">
                      {delta.candidate.solved}/{delta.candidate.attempts}
                    </Td>
                    <Td className="tnum text-right">
                      {delta.delta === null ? "—" : signed(Math.round(delta.delta * 100), "pp")}
                    </Td>
                    <Td>
                      <Badge tone={MOVEMENT_TONES[delta.movement] ?? "neutral"}>
                        {delta.movement.replace(/_/g, " ")}
                      </Badge>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>

          <Card title="Statistical note">
            <div className="p-4 text-xs">
              <p className="muted">
                {comparison.data.statistical_note.reason ?? comparison.data.statistical_note.note}
              </p>
              {comparison.data.statistical_note.p_value !== undefined && (
                <KeyValue
                  rows={[
                    ["Test", comparison.data.statistical_note.test ?? "—"],
                    ["z", comparison.data.statistical_note.z ?? "—"],
                    ["p-value", comparison.data.statistical_note.p_value],
                    ["Below α=0.05", String(comparison.data.statistical_note.below_alpha)],
                  ]}
                />
              )}
              <p className="mt-2 text-2xs text-amber-400">
                This platform makes no claim of statistical significance.
              </p>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
