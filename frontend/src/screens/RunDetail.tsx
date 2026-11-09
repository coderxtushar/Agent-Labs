import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { API_BASE_URL, downloadUrl } from "../api/client";
import { useAnnotateRun, useCapabilities, useRun, useRunEvents, useRunPatch } from "../api/hooks";
import type { GradingLayer, RunEvent } from "../api/types";
import {
  DiffView,
  LayerBadge,
  OptionalNumber,
  OutcomeBadge,
  SimulatedBadge,
  StateBadge,
  formatDuration,
  formatTime,
} from "../components/domain";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  Field,
  JsonBlock,
  KeyValue,
  LinkButton,
  LoadingRows,
  LoadingTiles,
  Mono,
  Table,
  Tabs,
  Td,
  Th,
  inputClass,
} from "../components/ui";

/** Live tail of new events for a run that is still executing. */
function useLiveEvents(runId: string | undefined, enabled: boolean, afterSeq: number) {
  const [live, setLive] = useState<RunEvent[]>([]);

  useEffect(() => {
    if (!runId || !enabled) {
      setLive([]);
      return;
    }
    const source = new EventSource(`${API_BASE_URL}/runs/${runId}/events/stream?after_seq=${afterSeq}`);
    source.addEventListener("event", (message) => {
      const parsed = JSON.parse((message as MessageEvent<string>).data) as RunEvent;
      setLive((current) =>
        current.some((entry) => entry.seq === parsed.seq) ? current : [...current, parsed],
      );
    });
    source.addEventListener("end", () => source.close());
    return () => source.close();
  }, [runId, enabled, afterSeq]);

  return live;
}

const EVENT_TONE: Record<string, "good" | "bad" | "warn" | "info" | "accent" | "neutral"> = {
  "run.finalized": "accent",
  "run.terminated": "warn",
  error: "bad",
  retry: "warn",
  "eval.end": "accent",
  "agent.start": "info",
  "agent.end": "info",
  "patch.generated": "good",
  "sandbox.ready": "info",
  "sandbox.destroy": "neutral",
};

function Timeline({ events }: { events: RunEvent[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggle = (seq: number) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });

  if (events.length === 0) return <p className="muted p-4 text-xs">No events recorded.</p>;

  return (
    <ol className="divide-y divide-[rgb(var(--border))]">
      {events.map((event) => (
        <li key={event.seq} className="px-3 py-2">
          <button
            type="button"
            onClick={() => toggle(event.seq)}
            className="flex w-full items-start gap-3 text-left"
          >
            <span className="tnum muted w-10 shrink-0 font-mono text-2xs">{event.seq}</span>
            <Badge tone={EVENT_TONE[event.event_type] ?? "neutral"}>{event.event_type}</Badge>
            <span className="muted min-w-0 flex-1 truncate text-2xs">
              {summarise(event)}
            </span>
            {event.truncated && (
              <Badge tone="warn" title="Payload was truncated for retention">
                truncated
              </Badge>
            )}
            <span className="muted shrink-0 font-mono text-2xs">
              {event.ts ? new Date(event.ts).toLocaleTimeString() : "—"}
            </span>
          </button>
          {expanded.has(event.seq) && (
            <div className="mt-2 pl-10">
              <JsonBlock value={event.payload} maxHeight="max-h-72" />
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

function summarise(event: RunEvent): string {
  const payload = event.payload;
  const pick = (key: string): string | undefined => {
    const value = payload[key];
    if (value === undefined || value === null) return undefined;
    return typeof value === "string" ? value : JSON.stringify(value);
  };
  return (
    pick("summary") ??
    pick("message") ??
    pick("command") ??
    pick("path") ??
    pick("tool") ??
    pick("layer") ??
    pick("outcome") ??
    pick("reason") ??
    pick("text") ??
    Object.keys(payload).slice(0, 4).join(", ")
  );
}

function LayerCard({ layer }: { layer: GradingLayer }) {
  const [open, setOpen] = useState(layer.status !== "passed");
  return (
    <div className="hairline rounded-md border">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <LayerBadge status={layer.status} />
        <Mono className="font-semibold">{layer.name}</Mono>
        {!layer.required && <Badge tone="neutral">advisory</Badge>}
        <span className="muted min-w-0 flex-1 truncate text-2xs">{layer.summary}</span>
        {layer.exit_code !== null && (
          <Mono className="muted">exit {layer.exit_code}</Mono>
        )}
        <Mono className="muted">{formatDuration(layer.duration_s)}</Mono>
      </button>
      {open && Object.keys(layer.evidence).length > 0 && (
        <div className="px-3 pb-3">
          <JsonBlock value={layer.evidence} maxHeight="max-h-72" />
        </div>
      )}
    </div>
  );
}

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const run = useRun(runId);
  const live = !(run.data?.is_terminal ?? true);
  const events = useRunEvents(runId, false);
  const [tab, setTab] = useState("timeline");
  const patch = useRunPatch(runId, tab === "patch");
  const capabilities = useCapabilities();
  const annotate = useAnnotateRun();

  const [category, setCategory] = useState("");
  const [rationale, setRationale] = useState("");
  const [author, setAuthor] = useState("");

  const storedEvents = events.data?.events ?? [];
  const liveEvents = useLiveEvents(runId, live, storedEvents.at(-1)?.seq ?? 0);
  const allEvents = useMemo(() => {
    const merged = new Map<number, RunEvent>();
    for (const event of [...storedEvents, ...liveEvents]) merged.set(event.seq, event);
    return [...merged.values()].sort((a, b) => a.seq - b.seq);
  }, [storedEvents, liveEvents]);

  if (run.isError) return <ErrorState error={run.error} onRetry={() => void run.refetch()} />;
  if (run.isLoading || !run.data) return <LoadingTiles count={4} />;

  const data = run.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-mono text-base font-semibold">{data.id.slice(0, 12)}</h1>
            <StateBadge state={data.state} />
            <OutcomeBadge outcome={data.outcome} />
            {data.agent.is_simulated && <SimulatedBadge compact />}
            {live && <Badge tone="info">live</Badge>}
          </div>
          <p className="muted mt-1 text-2xs">
            <Mono>{data.task_key}</Mono> attempt {data.attempt_index} ·{" "}
            <Link to={`/experiments/${data.experiment_id}`} className="accent-text hover:underline">
              experiment
            </Link>{" "}
            · agent <Mono>{data.agent.label}</Mono>
          </p>
        </div>
        {data.has_patch && (
          <LinkButton href={downloadUrl(`/runs/${data.id}/patch`)}>Download diff</LinkButton>
        )}
      </div>

      {data.termination_reason && (
        <div className="surface hairline rounded-lg border p-3">
          <p className="muted text-2xs font-semibold uppercase tracking-wide">Termination reason</p>
          <p className="mt-1 break-words text-xs">{data.termination_reason}</p>
          <p className="muted mt-2 text-2xs">
            {data.outcome === "solved"
              ? "Counted in the success-rate denominator, as a success."
              : data.counts_toward_success_rate
                ? "Counted in the success-rate denominator, as a failure."
                : "Excluded from the success-rate denominator entirely."}{" "}
            {data.attributable_to_agent === false &&
              "This outcome is not attributable to the agent."}
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="surface hairline rounded-lg border p-4">
          <p className="muted text-2xs font-semibold uppercase tracking-wide">Timings</p>
          <div className="mt-1 space-y-0.5 text-2xs">
            {Object.entries(data.timings).map(([phase, seconds]) => (
              <div key={phase} className="flex justify-between gap-2">
                <span className="muted">{phase.replace(/_s$/, "")}</span>
                <span className="tnum">{formatDuration(seconds)}</span>
              </div>
            ))}
            {Object.keys(data.timings).length === 0 && <span className="muted">not started</span>}
          </div>
        </div>

        <div className="surface hairline rounded-lg border p-4">
          <p className="muted text-2xs font-semibold uppercase tracking-wide">Patch</p>
          {data.patch_stats ? (
            <p className="tnum mt-1.5 text-sm">
              {data.patch_stats.files_changed} file(s){" "}
              <span className="text-emerald-400">+{data.patch_stats.insertions}</span>{" "}
              <span className="text-rose-400">-{data.patch_stats.deletions}</span>
            </p>
          ) : (
            <p className="muted mt-1.5 text-sm">no diff captured</p>
          )}
        </div>

        <div className="surface hairline rounded-lg border p-4">
          <p className="muted text-2xs font-semibold uppercase tracking-wide">Usage</p>
          <div className="mt-1 space-y-0.5 text-2xs">
            <div className="flex justify-between gap-2">
              <span className="muted">input</span>
              <OptionalNumber value={data.usage?.input_tokens ?? null} />
            </div>
            <div className="flex justify-between gap-2">
              <span className="muted">output</span>
              <OptionalNumber value={data.usage?.output_tokens ?? null} />
            </div>
            <div className="flex justify-between gap-2">
              <span className="muted">steps</span>
              <span className="tnum">{data.usage?.steps ?? "—"}</span>
            </div>
          </div>
        </div>

        <div className="surface hairline rounded-lg border p-4">
          <p className="muted text-2xs font-semibold uppercase tracking-wide">Cost</p>
          <p className="tnum mt-1.5 text-sm">
            <OptionalNumber value={data.cost_usd} />
          </p>
          <p className="muted mt-1 text-2xs">
            {data.cost_source ? `source: ${data.cost_source}` : "no cost reported"}
          </p>
        </div>
      </div>

      <Tabs
        tabs={[
          { id: "timeline", label: "Timeline", count: data.event_count },
          { id: "grading", label: "Grading" },
          { id: "patch", label: "Patch" },
          { id: "failure", label: "Failure" },
          { id: "context", label: "Context" },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === "timeline" && (
        <Card
          title="Event timeline"
          subtitle={`${allEvents.length} event(s), ordered by sequence number — timestamps can collide`}
        >
          {events.isError && (
            <div className="p-4">
              <ErrorState error={events.error} onRetry={() => void events.refetch()} />
            </div>
          )}
          {events.isLoading && <LoadingRows rows={8} label="Loading events" />}
          {events.data && <Timeline events={allEvents} />}
        </Card>
      )}

      {tab === "grading" && (
        <div className="space-y-4">
          {!data.grading && (
            <Card title="Not graded">
              <p className="muted p-4 text-xs">
                This run ended as <Mono>{data.outcome}</Mono>, so grading never ran. Grading only
                executes for an agent that finished normally.
              </p>
            </Card>
          )}
          {data.grading && (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <div className="surface hairline rounded-lg border p-4">
                  <p className="muted text-2xs font-semibold uppercase tracking-wide">Verdict</p>
                  <p className="mt-1.5 text-sm font-semibold">
                    {data.grading.passed === true && (
                      <span className="text-emerald-400">passed</span>
                    )}
                    {data.grading.passed === false && <span className="text-rose-400">failed</span>}
                    {data.grading.passed === null && (
                      <span className="text-amber-400">no verdict</span>
                    )}
                  </p>
                  <p className="muted mt-1 text-2xs">grader v{data.grading.grader_version}</p>
                </div>
                <div className="surface hairline rounded-lg border p-4">
                  <p className="muted text-2xs font-semibold uppercase tracking-wide">
                    Required layers passed
                  </p>
                  {(() => {
                    const required = data.grading.layers.filter((layer) => layer.required);
                    const passed = required.filter((layer) => layer.status === "passed").length;
                    return (
                      <>
                        <p className="tnum mt-1.5 text-sm font-semibold">
                          {data.grading.score === null
                            ? "—"
                            : `${Math.round(data.grading.score * 100)}%`}
                          <span className="muted ml-1.5 text-2xs font-normal">
                            ({passed}/{required.length})
                          </span>
                        </p>
                        <p className="muted mt-1 text-2xs">
                          {required.map((layer) => layer.name).join(", ")}
                        </p>
                      </>
                    );
                  })()}
                </div>
                <div className="surface hairline rounded-lg border p-4">
                  <p className="muted text-2xs font-semibold uppercase tracking-wide">Tests</p>
                  <p className="tnum mt-1.5 text-xs">
                    <span className="text-emerald-400">{data.grading.tests.passed} passed</span>
                    {" · "}
                    <span className="text-rose-400">{data.grading.tests.failed} failed</span>
                    {" · "}
                    {data.grading.tests.errored} errored
                  </p>
                </div>
                <div className="surface hairline rounded-lg border p-4">
                  <p className="muted text-2xs font-semibold uppercase tracking-wide">
                    Task validated
                  </p>
                  <p className="mt-1.5 text-xs">{data.task_validation_status}</p>
                </div>
              </div>

              <Card title="Grading layers" subtitle="Evaluated in order; a failed required layer stops the verdict">
                <div className="space-y-2 p-3">
                  {data.grading.layers.map((layer) => (
                    <LayerCard key={layer.name} layer={layer} />
                  ))}
                </div>
              </Card>

              <Card
                title="Model-assisted review"
                subtitle="Recorded separately — never folded into the pass/fail verdict"
              >
                <div className="p-4">
                  {data.grading.judge ? (
                    <JsonBlock value={data.grading.judge} maxHeight="max-h-48" />
                  ) : (
                    <p className="muted text-xs">Not enabled for this task.</p>
                  )}
                  <p className="muted mt-2 text-2xs">{data.grading.judge_note}</p>
                </div>
              </Card>

              <Card title="Grading evidence">
                <div className="p-4">
                  <JsonBlock value={data.grading.evidence} maxHeight="max-h-80" />
                </div>
              </Card>
            </>
          )}
        </div>
      )}

      {tab === "patch" && (
        <Card
          title="Unified diff"
          subtitle={
            data.patch_stats
              ? `${data.patch_stats.files_changed} file(s), +${data.patch_stats.insertions}/-${data.patch_stats.deletions}`
              : "no changes captured"
          }
        >
          {!data.has_patch && (
            <p className="muted p-4 text-xs">
              The agent left the workspace unchanged, so there is no diff.
            </p>
          )}
          {data.has_patch && patch.isLoading && <LoadingRows rows={8} label="Loading diff" />}
          {data.has_patch && patch.isError && (
            <div className="p-4">
              <ErrorState error={patch.error} onRetry={() => void patch.refetch()} />
            </div>
          )}
          {patch.data && (
            <div className="p-3">
              <DiffView patch={patch.data} />
            </div>
          )}
        </Card>
      )}

      {tab === "failure" && (
        <div className="space-y-4">
          <Card title="Effective classification">
            <div className="p-4">
              {data.failure ? (
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="bad">{data.failure.category}</Badge>
                    <Badge tone={data.failure.model_generated ? "warn" : "neutral"}>
                      source: {data.failure.source}
                      {data.failure.model_generated && " (model-generated)"}
                    </Badge>
                    {data.failure.confidence !== null && (
                      <Badge tone="neutral">confidence {data.failure.confidence}</Badge>
                    )}
                  </div>
                  <p className="text-xs">{data.failure.rationale}</p>
                  <JsonBlock value={data.failure.evidence_refs} maxHeight="max-h-64" />
                </div>
              ) : (
                <p className="muted text-xs">
                  No failure classification — this run was not a failure.
                </p>
              )}
            </div>
          </Card>

          {data.failure_history.length > 1 && (
            <Card title="Classification history" subtitle="Append-only; corrections supersede rather than overwrite">
              <Table>
                <thead>
                  <tr>
                    <Th>Category</Th>
                    <Th>Source</Th>
                    <Th>Author</Th>
                    <Th>Rationale</Th>
                    <Th>Recorded</Th>
                    <Th>Status</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.failure_history.map((entry) => (
                    <tr key={entry.id}>
                      <Td>{entry.category}</Td>
                      <Td>{entry.source}</Td>
                      <Td>{entry.author ?? "—"}</Td>
                      <Td className="max-w-md">{entry.rationale}</Td>
                      <Td className="muted text-2xs">{formatTime(entry.created_at)}</Td>
                      <Td>
                        {entry.superseded ? (
                          <Badge tone="neutral">superseded</Badge>
                        ) : (
                          <Badge tone="accent">effective</Badge>
                        )}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          )}

          <Card
            title="Correct the classification"
            subtitle="Appends a human annotation. Raw evidence is never modified."
          >
            <div className="space-y-3 p-4">
              <Field label="Category">
                <select
                  className={inputClass}
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                >
                  <option value="">Select a category…</option>
                  {(capabilities.data?.vocabularies.failure_categories ?? []).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Rationale">
                <textarea
                  rows={3}
                  className={inputClass}
                  value={rationale}
                  onChange={(event) => setRationale(event.target.value)}
                />
              </Field>
              <Field label="Author">
                <input
                  className={inputClass}
                  value={author}
                  onChange={(event) => setAuthor(event.target.value)}
                />
              </Field>
              {annotate.isError && <ErrorState error={annotate.error} />}
              <Button
                variant="primary"
                disabled={!category || annotate.isPending}
                onClick={() =>
                  annotate.mutate(
                    { runId: data.id, category, rationale, author },
                    { onSuccess: () => { setCategory(""); setRationale(""); } },
                  )
                }
              >
                {annotate.isPending ? "Saving…" : "Record annotation"}
              </Button>
            </div>
          </Card>
        </div>
      )}

      {tab === "context" && (
        <div className="space-y-4">
          <Card title="Run metadata">
            <div className="p-4">
              <KeyValue
                rows={[
                  ["Sandbox provider", <Mono>{data.sandbox_provider ?? "—"}</Mono>],
                  ["Infra retries", data.infra_attempts],
                  ["Seed", data.seed ?? "none"],
                  ["Claimed by", <Mono>{data.claimed_by ?? "—"}</Mono>],
                  ["Created", formatTime(data.created_at)],
                  ["Started", formatTime(data.started_at)],
                  ["Finished", formatTime(data.finished_at)],
                  ["Limits", <Mono>{JSON.stringify(data.limits)}</Mono>],
                ]}
              />
            </div>
          </Card>

          {data.attempt_history.length > 0 && (
            <Card
              title="Abandoned attempts"
              subtitle="Retained so an infrastructure retry never erases the original failure"
            >
              <Table>
                <thead>
                  <tr>
                    <Th>#</Th>
                    <Th>Outcome</Th>
                    <Th>Reason</Th>
                    <Th>Recorded</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.attempt_history.map((attempt) => (
                    <tr key={attempt.attempt_no}>
                      <Td className="tnum">{attempt.attempt_no}</Td>
                      <Td>{attempt.outcome}</Td>
                      <Td className="max-w-lg break-words">{attempt.reason}</Td>
                      <Td className="muted text-2xs">{formatTime(attempt.recorded_at)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          )}

          <Card title="Task snapshot" subtitle="Frozen at run creation">
            <div className="p-4">
              <JsonBlock value={data.task_snapshot} maxHeight="max-h-96" />
            </div>
          </Card>

          <Card title="Agent snapshot" subtitle="Credential-shaped values are redacted server-side">
            <div className="p-4">
              <JsonBlock value={data.agent_snapshot} maxHeight="max-h-96" />
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
