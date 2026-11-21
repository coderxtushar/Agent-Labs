import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { API_BASE_URL, downloadUrl } from "../api/client";
import { useAnnotateRun, useCapabilities, useRun, useRunEvents, useRunPatch } from "../api/hooks";
import type { Grading, GradingLayer, ResourceSummary, RunDetail as RunDetailData, RunEvent } from "../api/types";
import {
  LayerBadge,
  OptionalNumber,
  OutcomeBadge,
  PatchViewer,
  SimulatedBadge,
  StateBadge,
  formatDuration,
  formatTime,
} from "../components/domain";
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
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  SearchInput,
  Segmented,
  Table,
  Tabs,
  Td,
  Th,
  cx,
  inputClass,
  type BadgeTone,
} from "../components/ui";

const EMPTY_EVENTS: RunEvent[] = [];

/** Live tail of new events for a run that is still executing. */
function useLiveEvents(runId: string | undefined, enabled: boolean, afterSeq: number) {
  // Scoped to the run they were received for, so switching runs never leaks events
  // and nothing has to be reset synchronously inside the effect.
  const [live, setLive] = useState<{ runId: string | undefined; events: RunEvent[] }>({
    runId,
    events: EMPTY_EVENTS,
  });

  useEffect(() => {
    if (!runId || !enabled) return;
    const source = new EventSource(`${API_BASE_URL}/runs/${runId}/events/stream?after_seq=${afterSeq}`);
    source.addEventListener("event", (message) => {
      const parsed = JSON.parse((message as MessageEvent<string>).data) as RunEvent;
      setLive((current) => {
        const events = current.runId === runId ? current.events : EMPTY_EVENTS;
        if (events.some((entry) => entry.seq === parsed.seq)) {
          return current.runId === runId ? current : { runId, events };
        }
        return { runId, events: [...events, parsed] };
      });
    });
    source.addEventListener("end", () => source.close());
    return () => source.close();
  }, [runId, enabled, afterSeq]);

  return enabled && live.runId === runId ? live.events : EMPTY_EVENTS;
}

// --------------------------------------------------------------------------- //
// payload readers — payloads are untyped JSON, so every read is defensive
// --------------------------------------------------------------------------- //
const str = (value: unknown): string | undefined => {
  if (value === undefined || value === null) return undefined;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
};
const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const rec = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const strList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

function firstLine(text: string, max = 110): string {
  const line = text.split("\n").find((entry) => entry.trim() !== "") ?? "";
  const trimmed = line.trim();
  const multi = text.trim().includes("\n");
  if (trimmed.length > max) return `${trimmed.slice(0, max)}…`;
  return multi ? `${trimmed} …` : trimmed;
}

function compactNames(names: string[]): string {
  const counts = new Map<string, number>();
  for (const name of names) counts.set(name, (counts.get(name) ?? 0) + 1);
  return [...counts.entries()].map(([name, count]) => (count > 1 ? `${name} ×${count}` : name)).join(", ");
}

function parseTs(ts: string | null): number | null {
  if (!ts) return null;
  const value = Date.parse(ts);
  return Number.isNaN(value) ? null : value;
}

function formatOffset(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `+${seconds.toFixed(seconds < 10 ? 2 : 1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `+${minutes}m ${Math.round(seconds % 60)}s`;
}

// --------------------------------------------------------------------------- //
// trajectory model — derived only from recorded events, never reconstructed
// --------------------------------------------------------------------------- //
type Kind = "model" | "tools" | "files" | "errors" | "grading" | "system";
type Phase = "setup" | "agent" | "grading" | "finalize";

const KIND_LABEL: Record<Kind, string> = {
  model: "Model",
  tools: "Tools",
  files: "Files & commands",
  errors: "Errors & retries",
  grading: "Grading",
  system: "System",
};
const KINDS: Kind[] = ["model", "tools", "files", "errors", "grading", "system"];

const PHASE_LABEL: Record<Phase, string> = {
  setup: "Setup",
  agent: "Agent",
  grading: "Patch capture & grading",
  finalize: "Finalize",
};

function kindOf(type: string): Kind {
  if (type.startsWith("model.") || type === "agent.message") return "model";
  if (type.startsWith("tool.")) return "tools";
  if (type.startsWith("file.") || type === "command.exec") return "files";
  if (type === "error" || type === "retry" || type === "run.terminated") return "errors";
  if (type.startsWith("eval.") || type === "patch.generated") return "grading";
  return "system";
}

/** Phase boundaries are event types; everything in between inherits the current phase. */
function nextPhase(type: string, current: Phase): Phase {
  if (type === "run.claimed") return "setup";
  if (type === "agent.start") return "agent";
  if (type === "patch.generated" || type.startsWith("eval.")) return "grading";
  if (type === "run.finalized" || type === "run.terminated") return "finalize";
  return current;
}

function isProblem(event: RunEvent, phase: Phase): boolean {
  const payload = event.payload;
  switch (event.event_type) {
    case "error":
    case "retry":
    case "run.terminated":
      return true;
    case "tool.result":
      return payload.ok === false;
    case "env.setup_end":
      return payload.ok === false;
    case "command.exec":
      // A non-zero exit while grading usually just means tests failed — that is a
      // verdict, not an error.
      return (
        payload.timed_out === true ||
        (phase !== "grading" && num(payload.exit_code) !== null && payload.exit_code !== 0)
      );
    default:
      return false;
  }
}

function toneOf(event: RunEvent, problem: boolean): BadgeTone {
  const payload = event.payload;
  switch (event.event_type) {
    case "error":
      return "bad";
    case "retry":
    case "run.terminated":
      return "warn";
    case "tool.result":
      return payload.ok === false ? "bad" : "neutral";
    case "command.exec":
    case "env.setup_end":
      return problem ? "warn" : "neutral";
    case "eval.layer":
      return payload.status === "passed"
        ? "good"
        : payload.status === "failed"
          ? "bad"
          : payload.status === "errored"
            ? "warn"
            : "neutral";
    case "eval.end":
      return payload.passed === true ? "good" : payload.passed === false ? "bad" : "warn";
    case "patch.generated":
      return payload.empty === true ? "warn" : "good";
    case "model.response":
      return payload.finish_reason === "length" ? "warn" : "accent";
    case "model.request":
    case "agent.message":
      return "accent";
    case "run.finalized":
      return "info";
    default:
      return "neutral";
  }
}

interface TrajectoryItem {
  event: RunEvent;
  kind: Kind;
  phase: Phase;
  attempt: number;
  problem: boolean;
  tone: BadgeTone;
  offset: number | null;
  /** tool.call → its tool.result, only when the pairing is unambiguous. */
  result?: RunEvent;
  /** tool.result → the seq of the call it answers. */
  callSeq?: number;
  /** How the pair was made: an exact recorded call id, or event order (older runs). */
  pairedBy?: "id" | "order";
  /** model.response → seconds since the preceding model.request. */
  latency?: number | null;
}

interface TrajectorySummary {
  steps: number | null;
  modelRequests: number;
  modelResponses: number;
  toolCalls: number;
  toolsByName: [string, number][];
  failedToolResults: number;
  retries: number;
  errors: number;
  tokens: number | null;
  reportedResponses: number;
  spanSeconds: number | null;
  attempts: number;
}

function buildTrajectory(events: RunEvent[]): { items: TrajectoryItem[]; summary: TrajectorySummary } {
  const firstTs = events.length > 0 ? parseTs(events[0].ts) : null;
  let phase: Phase = "setup";
  let attempt = 1;
  let claimed = 0;
  let lastRequestTs: number | null = null;

  const items: TrajectoryItem[] = events.map((event) => {
    if (event.event_type === "run.claimed") {
      claimed += 1;
      attempt = Math.max(1, claimed);
    }
    phase = nextPhase(event.event_type, phase);
    const ts = parseTs(event.ts);
    const problem = isProblem(event, phase);
    const item: TrajectoryItem = {
      event,
      kind: kindOf(event.event_type),
      phase,
      attempt,
      problem,
      tone: toneOf(event, problem),
      offset: ts !== null && firstTs !== null ? Math.max(0, (ts - firstTs) / 1000) : null,
    };
    if (event.event_type === "model.request") lastRequestTs = ts;
    if (event.event_type === "model.response") {
      item.latency = ts !== null && lastRequestTs !== null ? (ts - lastRequestTs) / 1000 : null;
      lastRequestTs = null;
    }
    return item;
  });

  // Runs recorded since tool-call ids were added carry a `call_id` on both the
  // call and its result: pair those exactly. Older runs have no ids, so fall back
  // to pairing a tool.call with a tool.result of the same tool that follows it
  // before any other tool.call or model.request. Anything less clear stays unpaired.
  const callsById = new Map<string, number>();
  items.forEach((item, index) => {
    const id = item.event.payload.call_id;
    if (item.event.event_type === "tool.call" && typeof id === "string" && id) callsById.set(id, index);
  });
  items.forEach((item) => {
    const id = item.event.payload.call_id;
    if (item.event.event_type !== "tool.result" || typeof id !== "string" || !id) return;
    const callIndex = callsById.get(id);
    if (callIndex === undefined || items[callIndex].result) return;
    items[callIndex].result = item.event;
    items[callIndex].pairedBy = "id";
    item.callSeq = items[callIndex].event.seq;
    item.pairedBy = "id";
  });
  items.forEach((item, index) => {
    if (item.event.event_type !== "tool.call" || item.result) return;
    if (typeof item.event.payload.call_id === "string" && item.event.payload.call_id) return;
    const tool = str(item.event.payload.tool);
    for (let next = index + 1; next < items.length; next += 1) {
      const candidate = items[next].event;
      if (candidate.event_type === "tool.call" || candidate.event_type === "model.request") break;
      if (candidate.event_type === "tool.result") {
        if (str(candidate.payload.tool) === tool && items[next].callSeq === undefined && !candidate.payload.call_id) {
          item.result = candidate;
          item.pairedBy = "order";
          items[next].callSeq = item.event.seq;
          items[next].pairedBy = "order";
        }
        break;
      }
    }
  });

  const byTool = new Map<string, number>();
  let steps: number | null = null;
  let tokens: number | null = null;
  let reportedResponses = 0;
  const summary: TrajectorySummary = {
    steps: null,
    modelRequests: 0,
    modelResponses: 0,
    toolCalls: 0,
    toolsByName: [],
    failedToolResults: 0,
    retries: 0,
    errors: 0,
    tokens: null,
    reportedResponses: 0,
    spanSeconds: null,
    attempts: Math.max(1, claimed),
  };
  for (const { event } of items) {
    const payload = event.payload;
    switch (event.event_type) {
      case "model.request":
        summary.modelRequests += 1;
        break;
      case "model.response": {
        summary.modelResponses += 1;
        const usage = rec(payload.usage);
        if (usage && usage.reported_by_provider === true) {
          const total =
            num(usage.total_tokens) ??
            (num(usage.input_tokens) !== null && num(usage.output_tokens) !== null
              ? (num(usage.input_tokens) as number) + (num(usage.output_tokens) as number)
              : null);
          if (total !== null) {
            tokens = (tokens ?? 0) + total;
            reportedResponses += 1;
          }
        }
        break;
      }
      case "tool.call": {
        summary.toolCalls += 1;
        const tool = str(payload.tool) ?? "unknown";
        byTool.set(tool, (byTool.get(tool) ?? 0) + 1);
        break;
      }
      case "tool.result":
        if (payload.ok === false) summary.failedToolResults += 1;
        break;
      case "retry":
        summary.retries += 1;
        break;
      case "error":
        summary.errors += 1;
        break;
      case "agent.end":
        steps = num(payload.steps) ?? steps;
        break;
      default:
        break;
    }
  }
  summary.steps = steps;
  summary.tokens = tokens;
  summary.reportedResponses = reportedResponses;
  summary.toolsByName = [...byTool.entries()].sort((a, b) => b[1] - a[1]);
  const offsets = items.map((item) => item.offset).filter((value): value is number => value !== null);
  summary.spanSeconds = offsets.length > 0 ? Math.max(...offsets) : null;
  return { items, summary };
}

// --------------------------------------------------------------------------- //
// one-line summaries
// --------------------------------------------------------------------------- //
function argSummary(args: unknown): string {
  const value = rec(args);
  if (!value) return str(args) ?? "";
  const command = str(value.command);
  if (command !== undefined) return firstLine(command, 80);
  const path = str(value.path);
  const content = typeof value.content === "string" ? value.content : undefined;
  if (path !== undefined) {
    return content !== undefined ? `${path}, ${content.length.toLocaleString()} chars` : path;
  }
  const json = JSON.stringify(value);
  return json.length > 80 ? `${json.slice(0, 80)}…` : json;
}

function genericSummary(payload: Record<string, unknown>): string {
  for (const key of ["summary", "message", "command", "path", "tool", "layer", "outcome", "reason", "text", "note"]) {
    const value = str(payload[key]);
    if (value !== undefined && value !== "") return firstLine(value, 140);
  }
  return Object.keys(payload).slice(0, 5).join(", ");
}

function join(parts: (string | null | undefined | false)[]): string {
  return parts.filter((part): part is string => Boolean(part)).join(" · ");
}

function summarise(event: RunEvent): string {
  const p = event.payload;
  switch (event.event_type) {
    case "model.request":
      return join([
        str(p.model),
        num(p.message_count) !== null && `${p.message_count as number} messages`,
        num(p.tool_count) !== null && `${p.tool_count as number} tools`,
        (num(p.attempt) ?? 1) > 1 && `attempt ${p.attempt as number}`,
        num(p.max_tokens) !== null && `max ${(p.max_tokens as number).toLocaleString()} tok`,
      ]);
    case "model.response": {
      const usage = rec(p.usage);
      const total = num(usage?.total_tokens);
      const names = strList(p.tool_names);
      const text = str(p.assistant_text)?.trim();
      return join([
        text ? `"${firstLine(text, 70)}"` : null,
        str(p.finish_reason) && `finish ${str(p.finish_reason)}`,
        names.length > 0 ? `→ ${compactNames(names)}` : `${num(p.tool_call_count) ?? 0} tool calls`,
        usage?.reported_by_provider === true && total !== null
          ? `${total.toLocaleString()} tok`
          : "tokens not reported",
      ]);
    }
    case "tool.call":
      return `${str(p.tool) ?? "tool"}(${argSummary(p.arguments)})`;
    case "tool.result":
      return join([
        str(p.tool),
        p.ok === true ? "ok" : p.ok === false ? "failed" : null,
        num(p.result_chars) !== null && `${(p.result_chars as number).toLocaleString()} chars`,
        typeof p.result_preview === "string" && p.result_preview.trim() !== "" && firstLine(p.result_preview, 60),
      ]);
    case "command.exec":
      return join([
        str(p.phase) && `[${str(p.phase)}]`,
        str(p.command) && firstLine(str(p.command) as string, 80),
        num(p.exit_code) !== null && `exit ${p.exit_code as number}`,
        p.timed_out === true && "timed out",
        num(p.duration_s) !== null && formatDuration(p.duration_s as number),
      ]);
    case "file.read":
      return join([str(p.path), num(p.chars) !== null && `${(p.chars as number).toLocaleString()} chars`]);
    case "file.write":
      return join([
        str(p.path),
        num(p.bytes) !== null && `${(p.bytes as number).toLocaleString()} bytes`,
        num(p.lines) !== null && `${p.lines as number} lines`,
      ]);
    case "retry":
      return join([
        `${str(p.scope) ?? ""} retry ${str(p.attempt) ?? "?"}/${str(p.of) ?? "?"}`.trim(),
        num(p.delay_s) !== null && `after ${p.delay_s as number}s`,
        firstLine(str(p.error) ?? str(p.reason) ?? "", 90),
      ]);
    case "error":
      return join([str(p.message), str(p.detail) && firstLine(str(p.detail) as string, 90)]);
    case "resource.summary": {
      if (p.supported !== true) return `host memory not available: ${str(p.reason) ?? "unsupported"}`;
      const memory = (p.memory ?? {}) as Record<string, unknown>;
      const swap = (p.swap ?? {}) as Record<string, unknown>;
      const growth = num(swap.growth_bytes);
      return join([
        `peak RAM ${num(memory.peak_used_percent) ?? "?"}%`,
        growth !== null && `swap ${growth >= 0 ? "+" : ""}${Math.round(growth / 1024 / 1024)} MB`,
        p.host_constrained === true ? "host constrained — timings may be inflated" : "host not constrained",
      ]);
    }
    case "patch.generated":
      return p.empty === true
        ? "no changes captured"
        : join([
            `${num(p.files_changed) ?? "?"} file(s) +${num(p.insertions) ?? "?"} −${num(p.deletions) ?? "?"}`,
            p.diff_truncated === true && "diff truncated",
          ]);
    case "eval.start":
      return join([
        str(p.grader_version) && `grader v${str(p.grader_version)}`,
        p.task_validated === false && "task not validated",
      ]);
    case "eval.layer":
      return join([str(p.layer), str(p.status), str(p.summary) ?? str(p.note)]);
    case "eval.end": {
      const tests = rec(p.tests);
      return join([
        p.passed === true ? "verdict passed" : p.passed === false ? "verdict failed" : "no verdict",
        num(p.score) !== null && `score ${p.score as number}`,
        tests &&
          `tests ${num(tests.passed) ?? 0} passed, ${num(tests.failed) ?? 0} failed, ${num(tests.errored) ?? 0} errored`,
      ]);
    }
    case "agent.start":
      return join([
        str(p.adapter),
        str(p.model),
        num(p.max_steps) !== null && `max ${p.max_steps as number} steps`,
        p.simulated === true && "simulated",
      ]);
    case "agent.end":
      return join([
        num(p.steps) !== null && `${p.steps as number} steps`,
        p.finished_explicitly === true && "finished explicitly",
        p.finished_explicitly === false && "did not call finish",
        p.hit_step_cap === true && "hit step cap",
        (num(p.compactions) ?? 0) > 0 && `${p.compactions as number} compactions`,
      ]);
    case "agent.message": {
      const text = str(p.text)?.trim();
      return text ? `"${firstLine(text, 100)}"` : (str(p.note) ?? "empty message");
    }
    case "task.context_supplied":
      return join([
        str(p.transport),
        `${strList(p.brief_keys).length} brief fields`,
        p.hints_included === true ? "hints included" : p.hints_included === false ? "hints withheld" : null,
        num(p.prompt_chars) !== null && `${(p.prompt_chars as number).toLocaleString()} prompt chars`,
      ]);
    case "env.setup_start":
      return join([
        num(p.command_count) !== null && `${p.command_count as number} setup command(s)`,
        num(p.timeout_s) !== null && `timeout ${p.timeout_s as number}s`,
      ]);
    case "env.setup_end":
      return join([p.ok === false ? "failed" : p.ok === true ? "ok" : null, str(p.summary)]);
    case "run.claimed":
      return join([
        `attempt ${(num(p.infra_attempts) ?? 0) + 1}`,
        str(p.adapter),
        str(p.worker_id) && `worker ${str(p.worker_id)}`,
      ]);
    case "run.finalized":
      return join([str(p.outcome), str(p.reason)]);
    case "sandbox.create":
      return join([
        str(p.provider),
        str(p.image),
        num(p.memory_mb) !== null && `${p.memory_mb as number} MB`,
        num(p.cpus) !== null && `${p.cpus as number} CPU`,
        str(p.network) && `network ${str(p.network)}`,
      ]);
    default:
      return genericSummary(p);
  }
}

// --------------------------------------------------------------------------- //
// visual pieces
// --------------------------------------------------------------------------- //
const TONE_VAR: Record<BadgeTone, string> = {
  good: "--good",
  bad: "--bad",
  warn: "--warn",
  info: "--info",
  accent: "--accent",
  neutral: "--text-subtle",
};

function glyphFor(type: string): string {
  if (type.startsWith("model.") || type === "agent.message") return "model";
  if (type === "tool.call") return "tool";
  if (type === "tool.result") return "result";
  if (type === "file.read") return "file";
  if (type === "file.write") return "edit";
  if (type === "command.exec") return "terminal";
  if (type === "error") return "alert";
  if (type === "retry") return "retry";
  if (type === "patch.generated") return "diff";
  if (type.startsWith("eval.")) return "check";
  if (type.startsWith("agent.")) return "agent";
  return "dot";
}

const GLYPHS: Record<string, ReactNode> = {
  model: <path d="M3 4.5A1.5 1.5 0 0 1 4.5 3h7A1.5 1.5 0 0 1 13 4.5v5a1.5 1.5 0 0 1-1.5 1.5H7.5L4.5 13.25V11A1.5 1.5 0 0 1 3 9.5z" />,
  tool: <path d="M10.25 2.75a3 3 0 0 0-2.8 4.05L3 11.25 4.75 13l4.45-4.45a3 3 0 0 0 4.05-2.8l-1.75 1.5-1.75-.5-.5-1.75z" />,
  result: <path d="M4 3.5v4A2.5 2.5 0 0 0 6.5 10H12M9.5 7.5 12 10l-2.5 2.5" />,
  file: <path d="M4.5 2.5h4.25L11.5 5.25v8.25h-7zM8.5 2.5v3h3" />,
  edit: <path d="M10.25 3.25 12.75 5.75 6 12.5H3.5V10zM8.75 4.75l2.5 2.5" />,
  terminal: <path d="M2.5 3.5h11v9h-11zM5 6.75 6.75 8.25 5 9.75M8.25 10h2.75" />,
  alert: <path d="M8 2.75 13.75 13H2.25zM8 6.75v2.75M8 11.1v.15" />,
  retry: <path d="M3.25 8a4.75 4.75 0 1 0 1.4-3.36M3.25 2.75v2.5h2.5" />,
  diff: <path d="M8 3v5M5.5 5.5h5M5.5 11.5h5" />,
  check: <path d="m3.75 8.5 2.75 2.75 5.75-6.5" />,
  agent: <path d="M5 13V9.5M11 13V9.5M5.5 9.5h5A2.5 2.5 0 0 0 13 7V5.5A2.5 2.5 0 0 0 10.5 3h-5A2.5 2.5 0 0 0 3 5.5V7a2.5 2.5 0 0 0 2.5 2.5z" />,
  dot: <circle cx="8" cy="8" r="2" />,
};

function EventIcon({ type, tone }: { type: string; tone: BadgeTone }) {
  const color = TONE_VAR[tone];
  return (
    <span
      aria-hidden
      className="flex h-[22px] w-[22px] items-center justify-center rounded-full border"
      style={{
        color: `rgb(var(${color}))`,
        backgroundColor: `rgb(var(${color}) / 0.1)`,
        borderColor: `rgb(var(${color}) / 0.3)`,
      }}
    >
      <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        {GLYPHS[glyphFor(type)]}
      </svg>
    </span>
  );
}

const row = (label: string, value: ReactNode): [string, ReactNode] => [label, value];

function Fields({ rows }: { rows: [string, ReactNode][] }) {
  const visible = rows.filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (visible.length === 0) return null;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 lg:grid-cols-5">
      {visible.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="eyebrow">{label}</dt>
          <dd className="tnum mt-0.5 break-words text-2xs">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="eyebrow mb-1">{label}</p>
      {children}
    </div>
  );
}

function ShellBlock({ command }: { command: string }) {
  return (
    <CodeBlock maxHeight="max-h-72" className="whitespace-pre-wrap break-words">
      <span className="subtle select-none">$ </span>
      {command}
    </CodeBlock>
  );
}

function TextBlock({ text, maxHeight = "max-h-72" }: { text: string; maxHeight?: string }) {
  return (
    <CodeBlock maxHeight={maxHeight} className="whitespace-pre-wrap break-words">
      {text}
    </CodeBlock>
  );
}

function ProblemBox({ tone, children }: { tone: "bad" | "warn"; children: ReactNode }) {
  const color = TONE_VAR[tone];
  return (
    <div
      className="space-y-2 rounded-md border p-3 text-xs"
      style={{ borderColor: `rgb(var(${color}) / 0.35)`, backgroundColor: `rgb(var(${color}) / 0.06)` }}
    >
      {children}
    </div>
  );
}

const yesNo = (value: unknown): string | undefined =>
  value === true ? "yes" : value === false ? "no" : undefined;

function UsageFields({ usage }: { usage: Record<string, unknown> | null }) {
  if (!usage) return <p className="subtle text-2xs">No usage block recorded.</p>;
  const reported = usage.reported_by_provider === true;
  return (
    <div className="space-y-1.5">
      <Fields
        rows={[
          row("input", <OptionalNumber key="i" value={reported ? num(usage.input_tokens) : null} />),
          row("output", <OptionalNumber key="o" value={reported ? num(usage.output_tokens) : null} />),
          row("cached input", <OptionalNumber key="c" value={reported ? num(usage.cached_input_tokens) : null} />),
          row("reasoning", <OptionalNumber key="r" value={reported ? num(usage.reasoning_tokens) : null} />),
          row("total", <OptionalNumber key="t" value={reported ? num(usage.total_tokens) : null} />),
        ]}
      />
      <p className="subtle text-2xs">
        {reported ? "Reported by the provider." : "The provider did not report usage for this response."}
      </p>
    </div>
  );
}

function PayloadToggle({ event }: { event: RunEvent }) {
  return (
    <details className="group">
      <summary className="subtle cursor-pointer select-none text-2xs hover:text-[rgb(var(--text))]">
        Payload JSON
      </summary>
      <div className="mt-1.5">
        <JsonBlock value={event.payload} maxHeight="max-h-72" />
      </div>
    </details>
  );
}

function EventDetails({ item }: { item: TrajectoryItem }) {
  const { event } = item;
  const p = event.payload;
  let body: ReactNode = null;
  let structured = true;

  switch (event.event_type) {
    case "model.request": {
      const sampling = rec(p.sampling);
      body = (
        <>
          <Fields
            rows={[
              row("model", str(p.model) && <Mono>{str(p.model)}</Mono>),
              row("messages", str(p.message_count)),
              row("tools offered", str(p.tool_count)),
              row("attempt", str(p.attempt)),
              row("max tokens", num(p.max_tokens)?.toLocaleString()),
            ]}
          />
          {sampling && Object.keys(sampling).length > 0 && (
            <Section label="Sampling">
              <JsonBlock value={sampling} maxHeight="max-h-40" />
            </Section>
          )}
        </>
      );
      break;
    }
    case "model.response": {
      const text = str(p.assistant_text) ?? "";
      const names = strList(p.tool_names);
      body = (
        <>
          <Fields
            rows={[
              row("model", str(p.model) && <Mono>{str(p.model)}</Mono>),
              row(
                "finish reason",
                str(p.finish_reason) && (
                  <span className={p.finish_reason === "length" ? "text-warn" : undefined}>
                    {str(p.finish_reason)}
                    {p.finish_reason === "length" && " (output cap reached)"}
                  </span>
                ),
              ),
              row("tool calls", str(p.tool_call_count)),
              row("latency", item.latency != null ? formatDuration(item.latency) : undefined),
            ]}
          />
          <Section label="Assistant text (provider-visible)">
            {text.trim() ? (
              <TextBlock text={text} />
            ) : (
              <p className="subtle text-2xs">No visible assistant text in this response.</p>
            )}
          </Section>
          {names.length > 0 && (
            <Section label="Requested tools">
              <div className="flex flex-wrap gap-1">
                {names.map((name, index) => (
                  <Badge key={`${name}-${index}`} tone="neutral">
                    <span className="font-mono">{name}</span>
                  </Badge>
                ))}
              </div>
            </Section>
          )}
          <Section label="Token usage">
            <UsageFields usage={rec(p.usage)} />
          </Section>
          {str(p.note) && <Note>{str(p.note)}</Note>}
        </>
      );
      break;
    }
    case "tool.call": {
      const args = rec(p.arguments);
      const command = typeof args?.command === "string" ? args.command : undefined;
      const content = typeof args?.content === "string" ? args.content : undefined;
      const rest = args
        ? Object.fromEntries(Object.entries(args).filter(([key]) => key !== "command" && key !== "content"))
        : null;
      const result = item.result;
      body = (
        <>
          <Fields
            rows={[
              row("tool", str(p.tool) && <Mono>{str(p.tool)}</Mono>),
              row("step", str(p.step)),
              row("path", typeof args?.path === "string" ? <Mono>{args.path}</Mono> : undefined),
            ]}
          />
          {command !== undefined && (
            <Section label="Command">
              <ShellBlock command={command} />
            </Section>
          )}
          {content !== undefined && (
            <Section label={`Content (${content.length.toLocaleString()} chars)`}>
              <TextBlock text={content} maxHeight="max-h-80" />
            </Section>
          )}
          {rest && Object.keys(rest).filter((key) => key !== "path").length > 0 && (
            <Section label="Arguments">
              <JsonBlock value={rest} maxHeight="max-h-48" />
            </Section>
          )}
          {!args && p.arguments !== undefined && (
            <Section label="Arguments">
              <JsonBlock value={p.arguments} maxHeight="max-h-48" />
            </Section>
          )}
          <Section label={result ? `Result · #${result.seq}` : "Result"}>
            {result ? (
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2 text-2xs">
                  <Badge tone={result.payload.ok === false ? "bad" : result.payload.ok === true ? "good" : "neutral"}>
                    {result.payload.ok === false ? "failed" : result.payload.ok === true ? "ok" : "unknown"}
                  </Badge>
                  {num(result.payload.result_chars) !== null && (
                    <span className="subtle tnum">
                      {(result.payload.result_chars as number).toLocaleString()} chars returned to the model
                    </span>
                  )}
                </div>
                {typeof result.payload.result_preview === "string" && result.payload.result_preview !== "" && (
                  <TextBlock text={result.payload.result_preview} maxHeight="max-h-56" />
                )}
              </div>
            ) : (
              <p className="subtle text-2xs">
                {typeof item.event.payload.call_id === "string"
                  ? `No tool.result with call id ${item.event.payload.call_id} was recorded. Nothing is inferred.`
                  : "No tool.result for this call was recorded directly after it. Nothing is inferred."}
              </p>
            )}
          </Section>
        </>
      );
      break;
    }
    case "tool.result": {
      const preview = typeof p.result_preview === "string" ? p.result_preview : "";
      const chars = num(p.result_chars);
      body = (
        <>
          <Fields
            rows={[
              row("tool", str(p.tool) && <Mono>{str(p.tool)}</Mono>),
              row("ok", yesNo(p.ok)),
              row("chars", chars?.toLocaleString()),
              row(
                "answers",
                item.callSeq !== undefined
                  ? `tool.call #${item.callSeq} (${item.pairedBy === "id" ? "matched by call id" : "matched by event order"})`
                  : "unpaired",
              ),
            ]}
          />
          <Section label="Output preview">
            {preview ? <TextBlock text={preview} /> : <p className="subtle text-2xs">No preview recorded.</p>}
          </Section>
          {chars !== null && preview && chars > preview.length && (
            <p className="subtle text-2xs">
              Preview holds {preview.length.toLocaleString()} of {chars.toLocaleString()} characters; the
              rest was not retained in the event.
            </p>
          )}
        </>
      );
      break;
    }
    case "command.exec": {
      const stdout = str(p.stdout_tail) ?? "";
      const stderr = str(p.stderr_tail) ?? "";
      body = (
        <>
          {str(p.command) && (
            <Section label="Command">
              <ShellBlock command={str(p.command) as string} />
            </Section>
          )}
          <Fields
            rows={[
              row("phase", str(p.phase)),
              row(
                "exit code",
                num(p.exit_code) !== null ? (
                  <span className={item.problem ? "text-warn" : undefined}>{p.exit_code as number}</span>
                ) : undefined,
              ),
              row("timed out", p.timed_out === true ? <span className="text-warn">yes</span> : yesNo(p.timed_out)),
              row("duration", num(p.duration_s) !== null ? formatDuration(p.duration_s as number) : undefined),
              row("output truncated", yesNo(p.output_truncated)),
            ]}
          />
          {stdout.trim() && (
            <Section label="stdout (tail)">
              <TextBlock text={stdout} />
            </Section>
          )}
          {stderr.trim() && (
            <Section label="stderr (tail)">
              <TextBlock text={stderr} />
            </Section>
          )}
        </>
      );
      break;
    }
    case "file.read":
    case "file.write":
      body = (
        <Fields
          rows={[
            row("path", str(p.path) && <Mono>{str(p.path)}</Mono>),
            row("chars", num(p.chars)?.toLocaleString()),
            row("bytes", num(p.bytes)?.toLocaleString()),
            row("lines", num(p.lines)?.toLocaleString()),
            row("via tool", str(p.tool) && <Mono>{str(p.tool)}</Mono>),
          ]}
        />
      );
      break;
    case "retry":
      body = (
        <ProblemBox tone="warn">
          <Fields
            rows={[
              row("scope", str(p.scope)),
              row("attempt", `${str(p.attempt) ?? "?"} of ${str(p.of) ?? "?"}`),
              row("delay", num(p.delay_s) !== null ? `${p.delay_s as number}s` : undefined),
            ]}
          />
          {(str(p.error) ?? str(p.reason)) && <TextBlock text={(str(p.error) ?? str(p.reason)) as string} />}
          {str(p.note) && <p className="muted text-2xs">{str(p.note)}</p>}
        </ProblemBox>
      );
      break;
    case "error":
      body = (
        <ProblemBox tone="bad">
          <p className="text-bad font-medium">{str(p.message) ?? "error"}</p>
          {str(p.detail) && <TextBlock text={str(p.detail) as string} />}
        </ProblemBox>
      );
      break;
    case "patch.generated": {
      const paths = strList(p.paths);
      body = (
        <>
          <Fields
            rows={[
              row("empty", yesNo(p.empty)),
              row("files", str(p.files_changed)),
              row("insertions", num(p.insertions) !== null ? <span className="text-good">+{p.insertions as number}</span> : undefined),
              row("deletions", num(p.deletions) !== null ? <span className="text-bad">−{p.deletions as number}</span> : undefined),
              row("diff truncated", p.diff_truncated === true ? <span className="text-warn">yes</span> : yesNo(p.diff_truncated)),
            ]}
          />
          {paths.length > 0 && (
            <Section label="Paths">
              <ul className="space-y-0.5">
                {paths.map((path) => (
                  <li key={path}>
                    <Mono>{path}</Mono>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          <p className="subtle text-2xs">Captured host-side from the copied-out workspace, before hidden tests were injected.</p>
        </>
      );
      break;
    }
    case "eval.layer": {
      const files = strList(p.files);
      const status = str(p.status);
      body = (
        <>
          <Fields
            rows={[
              row("layer", str(p.layer) && <Mono>{str(p.layer)}</Mono>),
              row(
                "status",
                status && ["passed", "failed", "skipped", "errored"].includes(status) ? (
                  <LayerBadge status={status as GradingLayer["status"]} />
                ) : (
                  status
                ),
              ),
              row("required", yesNo(p.required)),
              row("exit code", str(p.exit_code)),
              row("duration", num(p.duration_s) !== null ? formatDuration(p.duration_s as number) : undefined),
            ]}
          />
          {str(p.summary) && <p className="text-xs">{str(p.summary)}</p>}
          {files.length > 0 && (
            <Section label="Files">
              <ul className="space-y-0.5">
                {files.map((file) => (
                  <li key={file}>
                    <Mono>{file}</Mono>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {str(p.note) && <Note>{str(p.note)}</Note>}
        </>
      );
      break;
    }
    case "eval.end": {
      const tests = rec(p.tests);
      const layers = Array.isArray(p.layers) ? p.layers.map(rec).filter((layer) => layer !== null) : [];
      body = (
        <>
          <Fields
            rows={[
              row(
                "verdict",
                p.passed === true ? (
                  <span className="text-good">passed</span>
                ) : p.passed === false ? (
                  <span className="text-bad">failed</span>
                ) : (
                  <span className="text-warn">no verdict (inconclusive)</span>
                ),
              ),
              row("score", str(p.score)),
              row("tests passed", str(tests?.passed)),
              row("tests failed", str(tests?.failed)),
              row("tests errored", str(tests?.errored)),
            ]}
          />
          {layers.length > 0 && (
            <Section label="Layers">
              <div className="flex flex-wrap gap-1.5">
                {layers.map((layer, index) => {
                  const status = str(layer.status);
                  const tone: BadgeTone =
                    status === "passed" ? "good" : status === "failed" ? "bad" : status === "errored" ? "warn" : "neutral";
                  return (
                    <Badge key={`${str(layer.name)}-${index}`} tone={tone}>
                      <span className="font-mono">{str(layer.name)}</span>
                      <span className="opacity-70">{status}</span>
                    </Badge>
                  );
                })}
              </div>
            </Section>
          )}
        </>
      );
      break;
    }
    case "agent.end":
      body = (
        <>
          <Fields
            rows={[
              row("steps", str(p.steps)),
              row("compactions", str(p.compactions)),
              row("finished explicitly", yesNo(p.finished_explicitly)),
              row("hit step cap", p.hit_step_cap === true ? <span className="text-warn">yes</span> : yesNo(p.hit_step_cap)),
            ]}
          />
          {rec(p.usage) && (
            <Section label="Agent usage (as reported)">
              <UsageFields usage={rec(p.usage)} />
            </Section>
          )}
        </>
      );
      break;
    case "agent.message": {
      const text = str(p.text) ?? "";
      body = (
        <>
          <Section label="Message">
            {text.trim() ? <TextBlock text={text} /> : <p className="subtle text-2xs">Empty message.</p>}
          </Section>
          {str(p.note) && <Note>{str(p.note)}</Note>}
        </>
      );
      break;
    }
    default:
      structured = false;
  }

  return (
    <div className="space-y-3">
      {event.truncated && (
        <Note tone="warn">This payload was truncated for retention; some fields may be incomplete.</Note>
      )}
      {structured ? (
        <>
          {body}
          <PayloadToggle event={event} />
        </>
      ) : (
        <JsonBlock value={event.payload} maxHeight="max-h-72" />
      )}
    </div>
  );
}

function EventRow({
  item,
  open,
  onToggle,
}: {
  item: TrajectoryItem;
  open: boolean;
  onToggle: () => void;
}) {
  const { event } = item;
  const color = TONE_VAR[item.tone];
  const paired = item.callSeq !== undefined;
  return (
    <li className={cx("relative pb-0.5", paired ? "pl-14" : "pl-9")}>
      <span aria-hidden className="absolute bottom-0 left-[10.5px] top-0 w-px bg-[rgb(var(--border))]" />
      {paired && (
        <span
          aria-hidden
          className="absolute left-[11px] top-[15px] h-px w-7 bg-[rgb(var(--border-strong))]"
        />
      )}
      <span className={cx("absolute top-1.5", paired ? "left-[34px]" : "left-0")}>
        <EventIcon type={event.event_type} tone={item.tone} />
      </span>
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className={cx(
          "row-hover flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md px-2 py-1.5 text-left transition-colors duration-100 sm:flex-nowrap",
          open && "surface-2",
          item.problem && "bg-[rgb(var(--bad)/0.05)]",
        )}
      >
        <span className="tnum subtle w-9 shrink-0 font-mono text-2xs">#{event.seq}</span>
        <span className="shrink-0 font-mono text-2xs font-medium" style={{ color: item.tone === "neutral" ? undefined : `rgb(var(${color}))` }}>
          {event.event_type}
        </span>
        <span className="muted order-last min-w-0 basis-full truncate text-2xs sm:order-none sm:basis-auto sm:flex-1">
          {paired && <span className="subtle">↳ #{item.callSeq} · </span>}
          {summarise(event)}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5 sm:ml-0">
          {event.truncated && (
            <Badge tone="warn" title="Payload was truncated for retention">
              truncated
            </Badge>
          )}
          {item.latency != null && (
            <span className="subtle tnum font-mono text-2xs" title="Time since the preceding model.request">
              {formatDuration(item.latency)}
            </span>
          )}
          <span
            className="subtle tnum w-16 text-right font-mono text-2xs"
            title={event.ts ? `${new Date(event.ts).toLocaleString()} — offset from the first event` : "no timestamp"}
          >
            {formatOffset(item.offset)}
          </span>
        </span>
      </button>
      {open && (
        <div className="fade-in hairline mb-2 ml-2 mt-1 rounded-md border p-3">
          <EventDetails item={item} />
        </div>
      )}
    </li>
  );
}

function SummaryCell({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="surface hairline min-w-0 rounded-lg border px-3 py-2.5">
      <p className="eyebrow">{label}</p>
      <div className="tnum mt-1 text-sm font-semibold">{children}</div>
      {hint && <div className="subtle mt-0.5 text-2xs">{hint}</div>}
    </div>
  );
}

function SummaryStrip({ summary, run }: { summary: TrajectorySummary; run: RunDetailData }) {
  const steps = summary.steps ?? run.usage?.steps ?? null;
  const passed = run.grading?.passed;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
      <SummaryCell label="Steps" hint={summary.steps === null && steps !== null ? "from run usage" : undefined}>
        {steps ?? <span className="muted font-normal">—</span>}
      </SummaryCell>
      <SummaryCell
        label="Model requests"
        hint={summary.modelResponses !== summary.modelRequests ? `${summary.modelResponses} responses` : undefined}
      >
        {summary.modelRequests}
      </SummaryCell>
      <SummaryCell
        label="Tool calls"
        hint={
          summary.toolsByName.length > 0
            ? summary.toolsByName.map(([tool, count]) => `${tool} ${count}`).join(" · ")
            : undefined
        }
      >
        {summary.toolCalls}
        {summary.failedToolResults > 0 && (
          <span className="text-bad ml-1.5 text-2xs font-normal">{summary.failedToolResults} failed</span>
        )}
      </SummaryCell>
      <SummaryCell label="Retries · errors">
        <span className={summary.retries > 0 ? "text-warn" : undefined}>{summary.retries}</span>
        <span className="subtle font-normal"> · </span>
        <span className={summary.errors > 0 ? "text-bad" : undefined}>{summary.errors}</span>
      </SummaryCell>
      <SummaryCell
        label="Total tokens"
        hint={
          summary.tokens !== null
            ? `sum over ${summary.reportedResponses} of ${summary.modelResponses} reported responses`
            : summary.modelResponses > 0
              ? "no response reported usage"
              : undefined
        }
      >
        {summary.tokens !== null ? (
          summary.tokens.toLocaleString()
        ) : (
          <span className="muted text-xs font-normal">not reported</span>
        )}
      </SummaryCell>
      <SummaryCell
        label="Wall time"
        hint={summary.spanSeconds !== null ? `event span ${formatDuration(summary.spanSeconds)}` : undefined}
      >
        {run.timings.total_s !== undefined ? formatDuration(run.timings.total_s) : <span className="muted font-normal">—</span>}
      </SummaryCell>
      <SummaryCell
        label="Result"
        hint={
          run.grading
            ? passed === true
              ? "grading passed"
              : passed === false
                ? "grading failed"
                : "grading inconclusive"
            : "not graded"
        }
      >
        <OutcomeBadge outcome={run.outcome} />
      </SummaryCell>
    </div>
  );
}

function TrajectoryExplorer({ events, run }: { events: RunEvent[]; run: RunDetailData }) {
  const [view, setView] = useState<"structured" | "raw">("structured");
  const [kind, setKind] = useState<Kind | "all">("all");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set());

  const { items, summary } = useMemo(() => buildTrajectory(events), [events]);

  const kindCounts = useMemo(() => {
    const counts: Record<Kind, number> = { model: 0, tools: 0, files: 0, errors: 0, grading: 0, system: 0 };
    for (const item of items) {
      counts[item.kind] += 1;
      if (item.problem && item.kind !== "errors") counts.errors += 1;
    }
    return counts;
  }, [items]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((item) => {
      if (kind !== "all" && item.kind !== kind && !(kind === "errors" && item.problem)) return false;
      if (!needle) return true;
      return (
        item.event.event_type.toLowerCase().includes(needle) ||
        JSON.stringify(item.event.payload).toLowerCase().includes(needle)
      );
    });
  }, [items, kind, query]);

  const segments = useMemo(() => {
    const out: { key: string; phase: Phase; attempt: number; items: TrajectoryItem[] }[] = [];
    for (const item of visible) {
      const last = out.at(-1);
      if (last && last.phase === item.phase && last.attempt === item.attempt) last.items.push(item);
      else out.push({ key: `${item.event.seq}`, phase: item.phase, attempt: item.attempt, items: [item] });
    }
    return out;
  }, [visible]);

  const toggle = (seq: number) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });

  const truncatedSeqs = events.filter((event) => event.truncated).map((event) => event.seq);

  return (
    <div className="space-y-4">
      <SummaryStrip summary={summary} run={run} />

      <Card
        title="Trajectory"
        subtitle={`${events.length} recorded event(s), in sequence order${summary.attempts > 1 ? ` · ${summary.attempts} infrastructure attempts` : ""}`}
        actions={
          <Segmented
            label="Trajectory view"
            value={view}
            onChange={setView}
            options={[
              { value: "structured", label: "Structured" },
              { value: "raw", label: "Raw" },
            ]}
          />
        }
      >
        <div className="hairline space-y-3 border-b px-4 py-3">
          <Note>
            Provider-visible output only — hidden model reasoning is not observable to this platform.
            Events are ordered by <Mono>seq</Mono>, never re-sorted by timestamp. Phases are inferred from
            event-type boundaries (<Mono>agent.start</Mono>, <Mono>patch.generated</Mono>/<Mono>eval.*</Mono>,{" "}
            <Mono>run.finalized</Mono>).
          </Note>
          {view === "structured" && (
            <div className="flex flex-wrap items-center gap-2">
              <div role="group" aria-label="Filter by kind" className="flex flex-wrap gap-1">
                {(["all", ...KINDS] as const).map((value) => {
                  const count = value === "all" ? items.length : kindCounts[value];
                  const active = kind === value;
                  return (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={active}
                      disabled={value !== "all" && count === 0}
                      onClick={() => setKind(value)}
                      className={cx(
                        "hairline inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-2xs font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40",
                        active ? "accent-soft-bg accent-text border-transparent" : "muted hover:text-[rgb(var(--text))]",
                      )}
                    >
                      {value === "all" ? "All" : KIND_LABEL[value]}
                      <span className="tnum opacity-60">{count}</span>
                    </button>
                  );
                })}
              </div>
              <SearchInput
                value={query}
                onChange={setQuery}
                placeholder="Search events…"
                label="Search events"
                className="min-w-[12rem] flex-1"
              />
              <div className="flex items-center gap-1">
                <Button variant="ghost" onClick={() => setExpanded(new Set(visible.map((item) => item.event.seq)))}>
                  Expand all
                </Button>
                <Button variant="ghost" onClick={() => setExpanded(new Set())}>
                  Collapse all
                </Button>
              </div>
            </div>
          )}
        </div>

        {view === "raw" && (
          <div className="space-y-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="muted text-2xs">
                All {events.length} events exactly as returned by the API. Filters do not apply here.
              </p>
              <LinkButton href={downloadUrl(`/runs/${run.id}/events`, { limit: 2000 })}>Open JSON</LinkButton>
            </div>
            {truncatedSeqs.length > 0 && (
              <Note tone="warn">
                {truncatedSeqs.length} payload(s) were truncated for retention: #{truncatedSeqs.join(", #")}.
              </Note>
            )}
            <JsonBlock value={events} maxHeight="max-h-[40rem]" />
          </div>
        )}

        {view === "structured" && events.length === 0 && (
          <div className="p-4">
            <EmptyState title="No events recorded" description="This run has not emitted any events yet." />
          </div>
        )}

        {view === "structured" && events.length > 0 && visible.length === 0 && (
          <div className="p-4">
            <EmptyState
              title="No events match"
              description="Try a different kind or search term."
              action={
                <Button
                  onClick={() => {
                    setKind("all");
                    setQuery("");
                  }}
                >
                  Reset filters
                </Button>
              }
            />
          </div>
        )}

        {view === "structured" && visible.length > 0 && (
          <div className="space-y-4 px-3 py-4 sm:px-4">
            {segments.map((segment) => {
              const first = segment.items[0].offset;
              const last = segment.items.at(-1)?.offset ?? null;
              return (
                <section key={segment.key} aria-label={PHASE_LABEL[segment.phase]}>
                  <header className="mb-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 pl-1">
                    <h3 className="text-xs font-semibold tracking-tight">{PHASE_LABEL[segment.phase]}</h3>
                    {summary.attempts > 1 && <span className="eyebrow">attempt {segment.attempt}</span>}
                    <span className="subtle tnum text-2xs">
                      {segment.items.length} event{segment.items.length === 1 ? "" : "s"}
                      {first !== null && last !== null && ` · ${formatDuration(last - first)}`}
                    </span>
                  </header>
                  <ol>
                    {segment.items.map((item) => (
                      <EventRow
                        key={item.event.seq}
                        item={item}
                        open={expanded.has(item.event.seq)}
                        onToggle={() => toggle(item.event.seq)}
                      />
                    ))}
                  </ol>
                </section>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
function PatchValidation({ grading }: { grading: Grading | null }) {
  const passed = grading?.passed;
  const tone: "good" | "bad" | "warn" = passed === true ? "good" : passed === false ? "bad" : "warn";
  const label =
    passed === true
      ? "Validated by grading (hidden tests passed)"
      : passed === false
        ? "Rejected by grading"
        : "Not validated (grading inconclusive or not run)";
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2 text-xs"
      style={{ borderColor: `rgb(var(${TONE_VAR[tone]}) / 0.35)`, backgroundColor: `rgb(var(${TONE_VAR[tone]}) / 0.06)` }}
    >
      <span className="font-medium" style={{ color: `rgb(var(${TONE_VAR[tone]}))` }}>
        {label}
      </span>
      {grading && (
        <span className="muted tnum text-2xs">
          grader v{grading.grader_version} · tests {grading.tests.passed} passed, {grading.tests.failed} failed,{" "}
          {grading.tests.errored} errored
        </span>
      )}
    </div>
  );
}

function LayerCard({ layer }: { layer: GradingLayer }) {
  const [open, setOpen] = useState(layer.status !== "passed");
  return (
    <div className="hairline rounded-md border">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <LayerBadge status={layer.status} />
        <Mono className="font-semibold">{layer.name}</Mono>
        {!layer.required && <Badge tone="neutral">advisory</Badge>}
        <span className="muted min-w-0 flex-1 truncate text-2xs">{layer.summary}</span>
        {layer.exit_code !== null && <Mono className="muted">exit {layer.exit_code}</Mono>}
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

function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="surface elevated hairline rounded-xl border p-4">
      <p className="eyebrow">{label}</p>
      {children}
    </div>
  );
}

function Sparkline({ values, tone }: { values: number[]; tone: string }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values
    .map((value, index) => `${(index / (values.length - 1)) * 100},${28 - ((value - min) / span) * 26}`)
    .join(" ");
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="mt-2 h-7 w-full" aria-hidden>
      <polyline points={points} fill="none" stroke={tone} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function HostResourcesTile({ resources }: { resources: ResourceSummary | null | undefined }) {
  if (!resources) {
    return (
      <Tile label="Host memory">
        <p className="muted mt-1.5 text-2xs">not recorded — this run predates per-run sampling</p>
      </Tile>
    );
  }
  if (!resources.supported) {
    return (
      <Tile label="Host memory">
        <p className="muted mt-1.5 text-2xs">not available: {resources.reason}</p>
      </Tile>
    );
  }
  const mb = (bytes: number | null) => (bytes === null ? "—" : `${Math.round(bytes / 1024 / 1024).toLocaleString()} MB`);
  const growth = resources.swap.growth_bytes;
  return (
    <Tile label="Host memory">
      <div className="mt-1.5 space-y-0.5 text-2xs" title={resources.note}>
        <div className="flex justify-between gap-2">
          <span className="muted">peak RAM</span>
          <span className="tnum">{resources.memory.peak_used_percent}%</span>
        </div>
        <div className="flex justify-between gap-2">
          <span className="muted">swap growth</span>
          <span className={cx("tnum", growth !== null && growth > 0 && resources.host_constrained && "text-warn")}>
            {growth === null ? "—" : `${growth >= 0 ? "+" : ""}${mb(growth)}`}
          </span>
        </div>
        <div className="flex justify-between gap-2">
          <span className="muted">pressure</span>
          <span>{resources.pressure.worst ?? "not reported"}</span>
        </div>
      </div>
      <Sparkline values={resources.series.map((point) => point[1])} tone={resources.host_constrained ? "rgb(var(--warn))" : "rgb(var(--accent))"} />
      <p className={cx("mt-1 text-2xs", resources.host_constrained ? "text-warn" : "subtle")}>
        {resources.host_constrained
          ? `Host was constrained (${resources.reasons.join("; ")}); timings may be inflated.`
          : `${resources.samples} samples every ${resources.interval_s}s; host not constrained.`}
      </p>
    </Tile>
  );
}

const TAB_IDS = ["trajectory", "patch", "grading", "failure", "context"] as const;
type TabId = (typeof TAB_IDS)[number];

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab");
  const tab: TabId = (TAB_IDS as readonly string[]).includes(rawTab ?? "") ? (rawTab as TabId) : "trajectory";
  const setTab = (next: string) =>
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current);
        if (next === "trajectory") params.delete("tab");
        else params.set("tab", next);
        return params;
      },
      { replace: true },
    );

  const run = useRun(runId);
  const live = !(run.data?.is_terminal ?? true);
  const events = useRunEvents(runId, false);
  const patch = useRunPatch(runId, tab === "patch");
  const capabilities = useCapabilities();
  const annotate = useAnnotateRun();

  const [category, setCategory] = useState("");
  const [rationale, setRationale] = useState("");
  const [author, setAuthor] = useState("");

  const storedEvents = useMemo(() => events.data?.events ?? EMPTY_EVENTS, [events.data]);
  const liveEvents = useLiveEvents(runId, live, storedEvents.at(-1)?.seq ?? 0);
  const allEvents = useMemo(() => {
    const merged = new Map<number, RunEvent>();
    for (const event of [...storedEvents, ...liveEvents]) merged.set(event.seq, event);
    return [...merged.values()].sort((a, b) => a.seq - b.seq);
  }, [storedEvents, liveEvents]);

  // When a live run finishes, reload the stored events once so the final
  // trajectory comes from the database rather than the stream tail.
  const wasLive = useRef(live);
  const refetchEvents = events.refetch;
  useEffect(() => {
    if (wasLive.current && !live) void refetchEvents();
    wasLive.current = live;
  }, [live, refetchEvents]);

  if (run.isError) return <ErrorState error={run.error} onRetry={() => void run.refetch()} />;
  if (run.isLoading || !run.data) return <LoadingTiles count={4} />;

  const data = run.data;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={
          <span className="flex flex-wrap items-center gap-1.5 normal-case tracking-normal">
            <Link to="/trajectories" className="link">
              Trajectories
            </Link>
            <span aria-hidden>/</span>
            <Link to={`/experiments/${data.experiment_id}`} className="link">
              Experiment
            </Link>
          </span>
        }
        title={<span className="font-mono">{data.id.slice(0, 12)}</span>}
        description={
          <>
            {data.task_title ?? data.task_key} — <Mono>{data.task_key}</Mono> · attempt {data.attempt_index} · agent{" "}
            <Mono>{data.agent.label}</Mono>
            {data.agent.model && (
              <>
                {" "}
                · model <Mono>{data.agent.model}</Mono>
              </>
            )}
          </>
        }
        meta={
          <>
            <StateBadge state={data.state} />
            <OutcomeBadge outcome={data.outcome} />
            {data.agent.is_simulated && <SimulatedBadge compact />}
            {live && <Badge tone="info">live</Badge>}
            {data.infra_attempts > 0 && <Badge tone="neutral">{data.infra_attempts} infra retr{data.infra_attempts === 1 ? "y" : "ies"}</Badge>}
          </>
        }
        actions={
          <>
            <Link
              to={`/benchmarks/tasks/${encodeURIComponent(data.task_key)}`}
              className="surface-2 hairline inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition hover:opacity-80"
            >
              Task
            </Link>
            {data.has_patch && <LinkButton href={downloadUrl(`/runs/${data.id}/patch`)}>Download diff</LinkButton>}
          </>
        }
      />

      {data.agent.is_simulated && (
        <Note tone="warn">
          This run used a simulated agent. It validates the evaluation pipeline and is not model performance.
        </Note>
      )}

      {data.termination_reason && (
        <div className="surface hairline rounded-xl border p-4">
          <p className="eyebrow">Termination reason</p>
          <p className="mt-1 break-words text-xs">{data.termination_reason}</p>
          <p className="muted mt-2 text-2xs">
            {data.outcome === "solved"
              ? "Counted in the success-rate denominator, as a success."
              : data.counts_toward_success_rate
                ? "Counted in the success-rate denominator, as a failure."
                : "Excluded from the success-rate denominator entirely."}{" "}
            {data.attributable_to_agent === false && "This outcome is not attributable to the agent."}
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Tile label="Timings">
          <div className="mt-1.5 space-y-0.5 text-2xs">
            {Object.entries(data.timings).map(([phase, seconds]) => (
              <div key={phase} className="flex justify-between gap-2">
                <span className="muted">{phase.replace(/_s$/, "")}</span>
                <span className="tnum">{formatDuration(seconds)}</span>
              </div>
            ))}
            {Object.keys(data.timings).length === 0 && <span className="muted">not started</span>}
          </div>
        </Tile>

        <Tile label="Patch">
          {data.patch_stats ? (
            <p className="tnum mt-1.5 text-sm">
              {data.patch_stats.files_changed} file(s) <span className="text-good">+{data.patch_stats.insertions}</span>{" "}
              <span className="text-bad">−{data.patch_stats.deletions}</span>
            </p>
          ) : (
            <p className="muted mt-1.5 text-sm">no diff captured</p>
          )}
        </Tile>

        <Tile label="Usage">
          <div className="mt-1.5 space-y-0.5 text-2xs">
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
        </Tile>

        <Tile label="Cost">
          <p className="tnum mt-1.5 text-sm">
            <OptionalNumber value={data.cost_usd} />
          </p>
          <p className="muted mt-1 text-2xs">{data.cost_source ? `source: ${data.cost_source}` : "no cost reported"}</p>
        </Tile>

        <HostResourcesTile resources={data.resources} />
      </div>

      <div className="space-y-4">
        <Tabs
          tabs={[
            { id: "trajectory", label: "Trajectory", count: Math.max(data.event_count, allEvents.length) },
            { id: "patch", label: "Patch", count: data.patch_stats?.files_changed ?? undefined },
            { id: "grading", label: "Grading" },
            { id: "failure", label: "Failure" },
            { id: "context", label: "Context" },
          ]}
          active={tab}
          onChange={setTab}
        />

        {tab === "trajectory" && (
          <>
            {events.isError && <ErrorState error={events.error} onRetry={() => void events.refetch()} />}
            {events.isLoading && (
              <Card>
                <LoadingRows rows={8} label="Loading events" />
              </Card>
            )}
            {events.data?.has_more && (
              <Note tone="warn">
                Only the first {events.data.events.length.toLocaleString()} of {events.data.total.toLocaleString()}{" "}
                events were loaded; later events are not shown or counted below.
              </Note>
            )}
            {events.data && <TrajectoryExplorer events={allEvents} run={data} />}
          </>
        )}

        {tab === "grading" && (
          <div className="space-y-4">
            {!data.grading && (
              <Card title="Not graded">
                <p className="muted p-4 text-xs">
                  This run ended as <Mono>{data.outcome ?? "not finished"}</Mono>, so grading never ran. Grading only
                  executes for an agent that finished normally.
                </p>
              </Card>
            )}
            {data.grading && (
              <>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <Tile label="Verdict">
                    <p className="mt-1.5 text-sm font-semibold">
                      {data.grading.passed === true && <span className="text-good">passed</span>}
                      {data.grading.passed === false && <span className="text-bad">failed</span>}
                      {data.grading.passed === null && <span className="text-warn">no verdict</span>}
                    </p>
                    <p className="muted mt-1 text-2xs">grader v{data.grading.grader_version}</p>
                  </Tile>
                  <Tile label="Required layers passed">
                    {(() => {
                      const required = data.grading.layers.filter((layer) => layer.required);
                      const passed = required.filter((layer) => layer.status === "passed").length;
                      return (
                        <>
                          <p className="tnum mt-1.5 text-sm font-semibold">
                            {data.grading.score === null ? "—" : `${Math.round(data.grading.score * 100)}%`}
                            <span className="muted ml-1.5 text-2xs font-normal">
                              ({passed}/{required.length})
                            </span>
                          </p>
                          <p className="muted mt-1 text-2xs">{required.map((layer) => layer.name).join(", ")}</p>
                        </>
                      );
                    })()}
                  </Tile>
                  <Tile label="Tests">
                    <p className="tnum mt-1.5 text-xs">
                      <span className="text-good">{data.grading.tests.passed} passed</span>
                      {" · "}
                      <span className="text-bad">{data.grading.tests.failed} failed</span>
                      {" · "}
                      {data.grading.tests.errored} errored
                    </p>
                  </Tile>
                  <Tile label="Task validated">
                    <p className="mt-1.5 text-xs">{data.task_validation_status}</p>
                  </Tile>
                </div>

                <Card title="Grading layers" subtitle="Evaluated in order; a failed required layer stops the verdict">
                  <div className="space-y-2 p-3">
                    {data.grading.layers.map((layer) => (
                      <LayerCard key={layer.name} layer={layer} />
                    ))}
                  </div>
                </Card>

                <Card title="Model-assisted review" subtitle="Recorded separately — never folded into the pass/fail verdict">
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
            title="Generated patch — captured host-side from the workspace"
            subtitle="Diff of the pristine baseline against the tree copied out of the sandbox, taken before hidden tests are injected. Evidence only — never applied or merged anywhere."
            actions={
              data.has_patch ? <LinkButton href={downloadUrl(`/runs/${data.id}/patch`)}>Download .diff</LinkButton> : undefined
            }
          >
            <div className="space-y-3 p-4">
              <PatchValidation grading={data.grading} />
              {!data.has_patch && (
                <EmptyState
                  title="No patch"
                  description="The agent left the workspace unchanged, so there is no diff."
                />
              )}
              {data.has_patch && patch.isLoading && <LoadingRows rows={8} label="Loading diff" />}
              {data.has_patch && patch.isError && (
                <ErrorState error={patch.error} onRetry={() => void patch.refetch()} />
              )}
              {data.has_patch && patch.data !== undefined && patch.data.trim() === "" && (
                <p className="muted text-xs">The stored diff is empty.</p>
              )}
              {patch.data && patch.data.trim() !== "" && (
                <PatchViewer key={data.patch_stats?.diff_sha256 ?? data.id} patch={patch.data} stats={data.patch_stats} />
              )}
            </div>
          </Card>
        )}

        {tab === "failure" && (
          <div className="space-y-4">
            <Card title="Effective classification">
              <div className="p-4">
                {data.failure ? (
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={data.failure.category === "unknown" ? "neutral" : "bad"}>
                        {data.failure.category === "unknown" ? "unclassified (unknown)" : data.failure.category}
                      </Badge>
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
                  <p className="muted text-xs">No failure classification — this run was not a failure.</p>
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

            <Card title="Correct the classification" subtitle="Appends a human annotation. Raw evidence is never modified.">
              <div className="space-y-3 p-4">
                <Field label="Category">
                  <select className={inputClass} value={category} onChange={(event) => setCategory(event.target.value)}>
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
                  <input className={inputClass} value={author} onChange={(event) => setAuthor(event.target.value)} />
                </Field>
                {annotate.isError && <ErrorState error={annotate.error} />}
                <Button
                  variant="primary"
                  disabled={!category || annotate.isPending}
                  onClick={() =>
                    annotate.mutate(
                      { runId: data.id, category, rationale, author },
                      {
                        onSuccess: () => {
                          setCategory("");
                          setRationale("");
                        },
                      },
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
                    ["Sandbox provider", <Mono key="sandbox">{data.sandbox_provider ?? "—"}</Mono>],
                    ["Infra retries", data.infra_attempts],
                    ["Seed", data.seed ?? "none"],
                    ["Claimed by", <Mono key="claimed">{data.claimed_by ?? "—"}</Mono>],
                    ["Created", formatTime(data.created_at)],
                    ["Started", formatTime(data.started_at)],
                    ["Finished", formatTime(data.finished_at)],
                    ["Limits", <Mono key="limits">{JSON.stringify(data.limits)}</Mono>],
                  ]}
                />
              </div>
            </Card>

            {data.attempt_history.length > 0 && (
              <Card title="Abandoned attempts" subtitle="Retained so an infrastructure retry never erases the original failure">
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
    </div>
  );
}
