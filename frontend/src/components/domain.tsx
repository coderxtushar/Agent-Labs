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

import type { ReactNode } from "react";

import type {
  LayerStatus,
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
export function DiffView({ patch }: { patch: string }) {
  const lines = patch.split("\n");
  return (
    <pre className="surface-2 hairline max-h-[32rem] overflow-auto rounded-md border font-mono text-2xs leading-relaxed">
      {lines.map((line, index) => {
        const isMeta = line.startsWith("diff --git") || line.startsWith("index ");
        const isHunk = line.startsWith("@@");
        const isFile = line.startsWith("+++") || line.startsWith("---");
        const isAdd = !isFile && line.startsWith("+");
        const isDel = !isFile && line.startsWith("-");
        return (
          <div
            key={index}
            className={cx(
              "px-3 whitespace-pre-wrap break-all",
              isMeta && "muted font-semibold",
              isFile && "muted",
              isHunk && "bg-indigo-500/10 text-indigo-300",
              isAdd && "bg-emerald-500/10 text-emerald-300",
              isDel && "bg-rose-500/10 text-rose-300",
            )}
          >
            {line === "" ? " " : line}
          </div>
        );
      })}
    </pre>
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
