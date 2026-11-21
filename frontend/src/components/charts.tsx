/**
 * Chart helpers shared by every screen.
 *
 * - `useChartTheme` reads the CSS tokens so Recharts follows the theme.
 * - `RateBar` draws a rate *with* its 95% interval; there is no variant that
 *   hides the interval, because a bare bar invites over-reading tiny samples.
 * - `OutcomeStack` shows outcome counts with excluded outcomes visually set
 *   apart from the ones that count toward a success rate.
 */

import { useEffect, useState } from "react";

import type { Rate, RunOutcome } from "../api/types";
import { COUNTED_OUTCOMES, EXCLUDED_OUTCOMES, OUTCOME_META } from "./domain";
import { cx } from "./ui";

function readVar(name: string): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return raw ? `rgb(${raw.split(/\s+/).join(", ")})` : "#888";
}

export interface ChartTheme {
  grid: string;
  axis: string;
  text: string;
  accent: string;
  surface: string;
  border: string;
  tooltip: React.CSSProperties;
}

export function useChartTheme(): ChartTheme {
  const read = (): ChartTheme => ({
    grid: readVar("--border"),
    axis: readVar("--text-subtle"),
    text: readVar("--text"),
    accent: readVar("--accent"),
    surface: readVar("--surface-2"),
    border: readVar("--border-strong"),
    tooltip: {
      backgroundColor: readVar("--surface-2"),
      border: `1px solid ${readVar("--border-strong")}`,
      borderRadius: 8,
      fontSize: 11,
      color: readVar("--text"),
      boxShadow: "0 8px 24px -12px rgba(0,0,0,0.5)",
    },
  });
  const [theme, setTheme] = useState<ChartTheme>(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

export const AXIS_TICK = { fontSize: 11 } as const;

// --------------------------------------------------------------------------- //
/** A rate as a bar with its Wilson interval drawn as a whisker. */
export function RateBar({ rate, className }: { rate: Rate | null | undefined; className?: string }) {
  if (!rate || rate.value === null) {
    return <span className="subtle text-2xs">no attributable runs</span>;
  }
  const value = rate.value * 100;
  const low = (rate.ci_low ?? rate.value) * 100;
  const high = (rate.ci_high ?? rate.value) * 100;
  return (
    <div
      className={cx("relative h-4 w-full min-w-[96px]", className)}
      role="img"
      aria-label={`${rate.percent}% (${rate.numerator}/${rate.denominator}), 95% CI ${low.toFixed(0)}–${high.toFixed(0)}%`}
      title={`95% CI ${low.toFixed(1)}–${high.toFixed(1)}% (${rate.ci_method ?? "Wilson"})`}
    >
      <div className="surface-3 absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full" />
      <div
        className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full"
        style={{ left: 0, width: `${value}%`, backgroundColor: "rgb(var(--accent))" }}
      />
      {/* interval whisker */}
      <div
        className="absolute top-1/2 h-px -translate-y-1/2"
        style={{ left: `${low}%`, width: `${Math.max(0.5, high - low)}%`, backgroundColor: "rgb(var(--text-muted))" }}
      />
      <div className="absolute top-[3px] h-2.5 w-px" style={{ left: `${low}%`, backgroundColor: "rgb(var(--text-muted))" }} />
      <div className="absolute top-[3px] h-2.5 w-px" style={{ left: `${high}%`, backgroundColor: "rgb(var(--text-muted))" }} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
export function OutcomeStack({
  outcomes,
  showLegend = false,
}: {
  outcomes: Partial<Record<RunOutcome, number>>;
  showLegend?: boolean;
}) {
  const counted = COUNTED_OUTCOMES.map((o) => [o, outcomes[o] ?? 0] as const).filter(([, n]) => n > 0);
  const excluded = EXCLUDED_OUTCOMES.map((o) => [o, outcomes[o] ?? 0] as const).filter(([, n]) => n > 0);
  const total = [...counted, ...excluded].reduce((sum, [, n]) => sum + n, 0);
  if (total === 0) return <span className="subtle text-2xs">no finished runs</span>;

  const segment = ([outcome, count]: readonly [RunOutcome, number]) => (
    <div
      key={outcome}
      title={`${OUTCOME_META[outcome].label}: ${count}${OUTCOME_META[outcome].counted ? "" : " (excluded from rates)"}`}
      style={{ width: `${(count / total) * 100}%`, backgroundColor: OUTCOME_META[outcome].color }}
      className="h-full first:rounded-l-full last:rounded-r-full"
    />
  );

  return (
    <div className="min-w-[120px]">
      <div className="flex h-1.5 w-full gap-px overflow-hidden rounded-full" role="img" aria-label="Outcome distribution">
        {counted.map(segment)}
        {excluded.length > 0 && counted.length > 0 && <div className="w-0.5" />}
        {excluded.map(segment)}
      </div>
      {showLegend && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
          {[...counted, ...excluded].map(([outcome, count]) => (
            <span key={outcome} className="muted inline-flex items-center gap-1.5 text-2xs">
              <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: OUTCOME_META[outcome].color }} />
              {OUTCOME_META[outcome].label}
              <span className="tnum subtle">{count}</span>
              {!OUTCOME_META[outcome].counted && <span className="subtle">· excluded</span>}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Equal-width histogram bins for a list of durations (seconds). */
export function binDurations(values: number[], bins = 8): { label: string; from: number; to: number; count: number }[] {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return [{ label: formatShort(min), from: min, to: max, count: values.length }];
  const width = (max - min) / bins;
  const out = Array.from({ length: bins }, (_, index) => ({
    from: min + index * width,
    to: min + (index + 1) * width,
    count: 0,
    label: "",
  }));
  for (const value of values) {
    const index = Math.min(bins - 1, Math.floor((value - min) / width));
    out[index].count += 1;
  }
  return out.map((bin) => ({ ...bin, label: `${formatShort(bin.from)}–${formatShort(bin.to)}` }));
}

function formatShort(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${(seconds / 60).toFixed(seconds < 600 ? 1 : 0)}m`;
}
