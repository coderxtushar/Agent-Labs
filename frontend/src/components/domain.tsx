/**
 * Domain components.
 *
 * These exist to make the honesty rules structural rather than a convention
 * someone has to remember:
 *
 * - `RateDisplay` cannot render a bare percentage. It always prints the
 *   numerator and denominator, and surfaces the API's small-sample warning.
 * - `OutcomeBadge` colours the four outcomes that count toward a success rate
 *   differently from the four that are excluded, and says which it is.
 * - `SimulatedBadge` marks results produced by a non-model agent.
 */

import { useState, type ReactNode } from "react";

import type {
  LayerStatus,
  PatchStats,
  Provenance,
  ProvenanceValue,
  Rate,
  RunOutcome,
  RunState,
} from "../api/types";
import { Badge, cx, type BadgeTone } from "./ui";

// --------------------------------------------------------------------------- //
export const OUTCOME_META: Record<
  RunOutcome,
  { label: string; tone: BadgeTone; counted: boolean; color: string }
> = {
  solved: { label: "solved", tone: "good", counted: true, color: "#34d399" },
  unsolved: { label: "unsolved", tone: "bad", counted: true, color: "#fb7185" },
  timeout: { label: "timeout", tone: "warn", counted: true, color: "#fbbf24" },
  agent_error: { label: "agent error", tone: "warn", counted: true, color: "#f97316" },
  infra_error: { label: "infra error", tone: "neutral", counted: false, color: "#a1a1aa" },
  invalid_task: { label: "invalid task", tone: "neutral", counted: false, color: "#71717a" },
  grading_error: { label: "grading error", tone: "neutral", counted: false, color: "#8b8b94" },
  cancelled: { label: "cancelled", tone: "neutral", counted: false, color: "#52525b" },
};

export const COUNTED_OUTCOMES: RunOutcome[] = ["solved", "unsolved", "timeout", "agent_error"];
export const EXCLUDED_OUTCOMES: RunOutcome[] = [
  "infra_error",
  "invalid_task",
  "grading_error",
  "cancelled",
];

export function OutcomeBadge({ outcome }: { outcome: RunOutcome | null }) {
  if (!outcome) return <Badge tone="neutral">not finished</Badge>;
  const meta = OUTCOME_META[outcome];
  return (
    <Badge
      tone={meta.tone}
      title={
        meta.counted
          ? "Counts toward the agent-attributable success rate"
          : "Excluded from success rates: not attributable to the agent"
      }
    >
      {meta.label}
      {!meta.counted && <span className="opacity-60">· excluded</span>}
    </Badge>
  );
}

const STATE_TONES: Partial<Record<RunState, BadgeTone>> = {
  pending: "neutral",
  completed: "accent",
  cancelled: "neutral",
};

export function StateBadge({ state }: { state: RunState }) {
  const active = !["pending", "completed", "cancelled"].includes(state);
  return (
    <Badge tone={active ? "info" : (STATE_TONES[state] ?? "neutral")}>
      {active && (
        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" aria-hidden />
      )}
      {state.replace(/_/g, " ")}
    </Badge>
  );
}

const PROVENANCE_TONES: Record<ProvenanceValue, BadgeTone> = {
  simulated: "warn",
  offline_adapter_test: "warn",
  live_model: "good",
  external_agent: "info",
};

/**
 * The single most misreadable thing on screen is a pipeline demonstration that
 * looks like a model score, so provenance is always shown with its caveat.
 */
export function ProvenanceBadge({
  provenance,
  compact,
}: {
  provenance: Provenance | null | undefined;
  compact?: boolean;
}) {
  if (!provenance) return null;
  return (
    <Badge tone={PROVENANCE_TONES[provenance.value] ?? "neutral"} title={provenance.caveat}>
      {compact ? provenance.value.replace(/_/g, " ") : provenance.label}
      {!provenance.is_model_result && !compact && (
        <span className="opacity-70">· not a model score</span>
      )}
    </Badge>
  );
}

export function ProvenanceNotice({ provenance }: { provenance: Provenance | null | undefined }) {
  if (!provenance) return null;
  const good = provenance.is_model_result;
  return (
    <div
      className={cx(
        "rounded-lg border p-3 text-xs",
        good
          ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-200/90"
          : "border-amber-500/30 bg-amber-500/5 text-amber-200/90",
      )}
    >
      <span className={cx("font-semibold", good ? "text-emerald-400" : "text-amber-400")}>
        {provenance.label}.
      </span>{" "}
      {provenance.caveat}
    </div>
  );
}

export function SimulatedBadge({ compact }: { compact?: boolean }) {
  return (
    <Badge
      tone="warn"
      title="This agent is deterministic and does not call a language model. Its results validate the evaluation pipeline and are not a model benchmark result."
    >
      {compact ? "simulated" : "Simulated agent — not a model benchmark result"}
    </Badge>
  );
}

const LAYER_TONES: Record<LayerStatus, BadgeTone> = {
  passed: "good",
  failed: "bad",
  errored: "warn",
  skipped: "neutral",
};

export function LayerBadge({ status }: { status: LayerStatus }) {
  return <Badge tone={LAYER_TONES[status]}>{status}</Badge>;
}

export function ValidationBadge({ status }: { status: string }) {
  const tone: BadgeTone =
    status === "valid" ? "good" : status === "invalid" ? "bad" : status === "error" ? "warn" : "neutral";
  return (
    <Badge
      tone={tone}
      title={
        status === "valid"
          ? "Environment builds, declared tests are collected, and the task is falsifiable"
          : "Not verified to be executable and falsifiable"
      }
    >
      {status}
    </Badge>
  );
}

// --------------------------------------------------------------------------- //
/** A rate, never shown without the numbers it was computed from. */
export function RateDisplay({
  rate,
  label,
  size = "md",
  showCi = true,
}: {
  rate: Rate | null | undefined;
  label?: string;
  size?: "sm" | "md" | "lg";
  showCi?: boolean;
}) {
  if (!rate) return <span className="muted text-xs">n/a</span>;

  const unavailable = rate.percent === null;
  const valueClass =
    size === "lg" ? "text-2xl font-semibold" : size === "md" ? "text-sm font-semibold" : "text-xs font-medium";

  return (
    <div className="min-w-0">
      {label && <p className="muted text-2xs font-semibold uppercase tracking-wide">{label}</p>}
      <p className={cx("tnum", valueClass, unavailable && "muted")}>
        {unavailable ? "no data" : `${rate.percent}%`}
        <span className="muted ml-1.5 text-2xs font-normal">
          ({rate.numerator}/{rate.denominator})
        </span>
      </p>
      {showCi && rate.ci_low !== null && rate.ci_high !== null && (
        <p className="muted tnum text-2xs" title={rate.ci_method ?? undefined}>
          95% CI {(rate.ci_low * 100).toFixed(1)}–{(rate.ci_high * 100).toFixed(1)}%
        </p>
      )}
      {rate.interpretation_warning && (
        <p className="mt-0.5 text-2xs text-amber-400">{rate.interpretation_warning}</p>
      )}
    </div>
  );
}

export function WarningList({
  warnings,
  title = "Caveats",
}: {
  warnings: string[];
  title?: string;
}) {
  if (warnings.length === 0) return null;
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
      <p className="text-2xs font-semibold uppercase tracking-wide text-amber-400">{title}</p>
      <ul className="mt-1.5 space-y-1 text-xs text-amber-200/90">
        {warnings.map((warning) => (
          <li key={warning} className="flex gap-1.5">
            <span aria-hidden>·</span>
            <span>{warning}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function BlockerList({ blockers }: { blockers: string[] }) {
  if (blockers.length === 0) return null;
  return (
    <div role="alert" className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-3">
      <p className="text-2xs font-semibold uppercase tracking-wide text-rose-400">
        Not directly comparable
      </p>
      <ul className="mt-1.5 space-y-1 text-xs text-rose-200/90">
        {blockers.map((blocker) => (
          <li key={blocker} className="flex gap-1.5">
            <span aria-hidden>·</span>
            <span>{blocker}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function PartialBanner({
  pending,
  active,
  total,
}: {
  pending: number;
  active: number;
  total: number;
}) {
  if (pending === 0 && active === 0) return null;
  return (
    <div className="rounded-lg border border-sky-500/30 bg-sky-500/5 p-3 text-xs text-sky-200/90">
      <span className="font-semibold text-sky-400">Partial results.</span> {pending} pending and {active} in
      flight of {total} runs. Every number below covers finished runs only.
    </div>
  );
}

// --------------------------------------------------------------------------- //
interface DiffLineInfo {
  text: string;
  kind: "meta" | "file" | "hunk" | "add" | "del" | "context";
  oldNo: number | null;
  newNo: number | null;
}

function classifyDiffLines(lines: string[]): DiffLineInfo[] {
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;
  return lines.map((text) => {
    if (text.startsWith("diff --git")) {
      inHunk = false;
      return { text, kind: "meta", oldNo: null, newNo: null };
    }
    if (text.startsWith("@@")) {
      const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
      if (match) {
        oldNo = Number(match[1]);
        newNo = Number(match[2]);
      }
      inHunk = true;
      return { text, kind: "hunk", oldNo: null, newNo: null };
    }
    if (!inHunk) {
      const isFile = text.startsWith("+++") || text.startsWith("---");
      return { text, kind: isFile ? "file" : "meta", oldNo: null, newNo: null };
    }
    if (text.startsWith("+")) {
      newNo += 1;
      return { text, kind: "add", oldNo: null, newNo: newNo - 1 };
    }
    if (text.startsWith("-")) {
      oldNo += 1;
      return { text, kind: "del", oldNo: oldNo - 1, newNo: null };
    }
    if (text.startsWith("\\")) return { text, kind: "meta", oldNo: null, newNo: null };
    oldNo += 1;
    newNo += 1;
    return { text, kind: "context", oldNo: oldNo - 1, newNo: newNo - 1 };
  });
}

const DIFF_LINE_CLASS: Record<DiffLineInfo["kind"], string> = {
  meta: "muted font-semibold",
  file: "muted",
  hunk: "bg-[rgb(var(--accent)/0.08)] accent-text",
  add: "bg-[rgb(var(--good)/0.1)] text-good",
  del: "bg-[rgb(var(--bad)/0.1)] text-bad",
  context: "",
};

/**
 * A unified diff, coloured by line kind. `numbered` adds old/new line-number
 * gutters derived from the hunk headers.
 */
export function DiffView({
  patch,
  numbered = false,
  maxHeight = "max-h-[32rem]",
  bare = false,
}: {
  patch: string;
  numbered?: boolean;
  maxHeight?: string;
  /** Drop the outer border/background, for embedding inside another frame. */
  bare?: boolean;
}) {
  const lines = classifyDiffLines(patch.split("\n"));
  return (
    <pre
      className={cx(
        "overflow-auto font-mono text-2xs leading-relaxed",
        !bare && "surface-2 hairline rounded-md border",
        maxHeight,
      )}
    >
      {lines.map((line, index) => (
        <div key={index} className={cx("flex", DIFF_LINE_CLASS[line.kind])}>
          {numbered && (
            <span
              aria-hidden
              className="subtle hairline tnum flex w-[5.5rem] shrink-0 select-none justify-between border-r px-2 text-right"
            >
              <span className="w-8">{line.oldNo ?? ""}</span>
              <span className="w-8">{line.newNo ?? ""}</span>
            </span>
          )}
          <span className="min-w-0 flex-1 whitespace-pre-wrap break-all px-3">
            {line.text === "" ? " " : line.text}
          </span>
        </div>
      ))}
    </pre>
  );
}

interface DiffFile {
  path: string;
  change: string;
  additions: number;
  deletions: number;
  text: string;
}

function splitDiffFiles(patch: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: (DiffFile & { lines: string[]; inHunk: boolean }) | null = null;
  const flush = () => {
    if (current) {
      files.push({
        path: current.path,
        change: current.change,
        additions: current.additions,
        deletions: current.deletions,
        text: current.lines.join("\n"),
      });
    }
  };
  for (const line of patch.split("\n")) {
    if (line.startsWith("diff --git ")) {
      flush();
      const match = /^diff --git a\/(.+?) b\/(.+)$/.exec(line);
      current = {
        path: match ? match[2] : line.slice("diff --git ".length),
        change: "modified",
        additions: 0,
        deletions: 0,
        text: "",
        lines: [],
        inHunk: false,
      };
    }
    if (!current) continue;
    current.lines.push(line);
    if (line.startsWith("@@")) {
      current.inHunk = true;
    } else if (!current.inHunk) {
      if (line.startsWith("new file mode")) current.change = "added";
      else if (line.startsWith("deleted file mode")) current.change = "deleted";
      else if (line.startsWith("rename from")) current.change = "renamed";
      else if (line.startsWith("Binary files")) current.change = "binary";
      else if (line.startsWith("+++ ") && !line.endsWith("/dev/null")) {
        current.path = line.slice(4).replace(/^b\//, "");
      }
    } else if (line.startsWith("+")) {
      current.additions += 1;
    } else if (line.startsWith("-")) {
      current.deletions += 1;
    }
  }
  flush();
  return files;
}

const CHANGE_TONES: Record<string, BadgeTone> = {
  added: "good",
  deleted: "bad",
  renamed: "info",
  binary: "warn",
  modified: "neutral",
};

/**
 * Per-file view of a captured patch: file list with +/- counts, collapsible
 * per-file diffs, and the capture metadata. Counts come from the recorded
 * `patch_stats` when available and are parsed from the diff text otherwise.
 */
export function PatchViewer({
  patch,
  stats,
}: {
  patch: string;
  stats?: PatchStats | null;
}) {
  const files = splitDiffFiles(patch);
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(files.length <= 3 ? files.map((file) => file.path) : []),
  );

  if (files.length === 0) {
    return <DiffView patch={patch} numbered />;
  }

  const statFor = (path: string) => stats?.paths.find((entry) => entry.path === path);
  const rows = files.map((file) => {
    const recorded = statFor(file.path);
    return {
      ...file,
      change: recorded?.change ?? file.change,
      plus: recorded ? recorded["+"] : file.additions,
      minus: recorded ? recorded["-"] : file.deletions,
      fromStats: Boolean(recorded),
    };
  });
  const totalPlus = stats?.insertions ?? rows.reduce((sum, row) => sum + row.plus, 0);
  const totalMinus = stats?.deletions ?? rows.reduce((sum, row) => sum + row.minus, 0);
  const toggle = (path: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="tnum text-xs">
          <span className="font-medium">
            {stats?.files_changed ?? rows.length} file{(stats?.files_changed ?? rows.length) === 1 ? "" : "s"} changed
          </span>
          <span className="text-good ml-2">+{totalPlus}</span>
          <span className="text-bad ml-1.5">−{totalMinus}</span>
        </p>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="muted rounded px-2 py-1 text-2xs font-medium hover:bg-[rgb(var(--surface-2))]"
            onClick={() => setOpen(new Set(rows.map((row) => row.path)))}
          >
            Expand all
          </button>
          <button
            type="button"
            className="muted rounded px-2 py-1 text-2xs font-medium hover:bg-[rgb(var(--surface-2))]"
            onClick={() => setOpen(new Set())}
          >
            Collapse all
          </button>
        </div>
      </div>

      <div className="space-y-2">
        {rows.map((row) => {
          const isOpen = open.has(row.path);
          const total = row.plus + row.minus;
          return (
            <div key={row.path} className="hairline overflow-hidden rounded-md border">
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() => toggle(row.path)}
                className="surface-2 flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[rgb(var(--surface-3))]"
              >
                <svg
                  aria-hidden
                  viewBox="0 0 16 16"
                  className={cx("subtle h-3 w-3 shrink-0 transition-transform duration-150", isOpen && "rotate-90")}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <path d="m6 4 4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <Badge tone={CHANGE_TONES[row.change] ?? "neutral"}>{row.change}</Badge>
                <span className="min-w-0 flex-1 truncate font-mono text-2xs font-medium" title={row.path}>
                  {row.path}
                </span>
                <span className="tnum shrink-0 font-mono text-2xs" title={row.fromStats ? "from recorded patch_stats" : "parsed from the diff text"}>
                  <span className="text-good">+{row.plus}</span>{" "}
                  <span className="text-bad">−{row.minus}</span>
                </span>
                <span aria-hidden className="hidden h-1.5 w-14 shrink-0 overflow-hidden rounded-full sm:flex">
                  {total > 0 && (
                    <>
                      <span style={{ width: `${(row.plus / total) * 100}%`, backgroundColor: "rgb(var(--good))" }} />
                      <span style={{ width: `${(row.minus / total) * 100}%`, backgroundColor: "rgb(var(--bad))" }} />
                    </>
                  )}
                </span>
              </button>
              {isOpen && (
                <div className="hairline border-t">
                  <DiffView patch={row.text} numbered bare maxHeight="max-h-[36rem]" />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {stats && (
        <dl className="subtle flex flex-wrap gap-x-5 gap-y-1 text-2xs">
          <div className="flex min-w-0 gap-1.5">
            <dt>diff sha256</dt>
            <dd className="muted min-w-0 truncate font-mono" title={stats.diff_sha256 ?? undefined}>
              {stats.diff_sha256 ? `${stats.diff_sha256.slice(0, 16)}…` : "—"}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt>truncated</dt>
            <dd className={stats.truncated ? "text-warn" : "muted"}>
              {stats.truncated ? "yes — stored diff is incomplete" : "no"}
            </dd>
          </div>
          {stats.binary_files.length > 0 && (
            <div className="flex min-w-0 gap-1.5">
              <dt>binary files</dt>
              <dd className="muted font-mono">{stats.binary_files.join(", ")}</dd>
            </div>
          )}
        </dl>
      )}
    </div>
  );
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const deltaSeconds = Math.round((Date.now() - then) / 1000);
  if (deltaSeconds < 60) return `${deltaSeconds}s ago`;
  if (deltaSeconds < 3600) return `${Math.floor(deltaSeconds / 60)}m ago`;
  if (deltaSeconds < 86400) return `${Math.floor(deltaSeconds / 3600)}h ago`;
  return `${Math.floor(deltaSeconds / 86400)}d ago`;
}

/** `null` means "not reported", which is different from zero. */
export function OptionalNumber({
  value,
  suffix,
  unavailableHint = "not reported",
}: {
  value: number | null | undefined;
  suffix?: string;
  unavailableHint?: string;
}): ReactNode {
  if (value === null || value === undefined) {
    return (
      <span className="muted" title={unavailableHint}>
        not reported
      </span>
    );
  }
  return (
    <span className="tnum">
      {value.toLocaleString()}
      {suffix}
    </span>
  );
}
