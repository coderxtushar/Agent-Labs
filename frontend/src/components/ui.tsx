/** Reusable primitives. Hand-written to keep the dependency surface small. */

import { useEffect, type ReactNode } from "react";

import { ApiError } from "../api/client";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// --------------------------------------------------------------------------- //
export function Card({
  children,
  className,
  title,
  subtitle,
  actions,
}: {
  children?: ReactNode;
  className?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className={cx("surface elevated min-w-0 rounded-xl border", className)}>
      {(title || actions) && (
        <header className="hairline flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="truncate text-[13px] font-semibold tracking-tight">{title}</h2>}
            {subtitle && <p className="muted mt-0.5 text-xs">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

const BADGE_TONES = {
  neutral: "surface-2 muted hairline border",
  accent: "accent-soft-bg accent-text border border-transparent",
  good: "border border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  bad: "border border-rose-500/30 bg-rose-500/10 text-rose-400",
  warn: "border border-amber-500/30 bg-amber-500/10 text-amber-400",
  info: "border border-sky-500/30 bg-sky-500/10 text-sky-400",
} as const;

export type BadgeTone = keyof typeof BADGE_TONES;

export function Badge({
  children,
  tone = "neutral",
  className,
  title,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-2xs font-medium leading-4",
        BADGE_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const BUTTON_VARIANTS = {
  primary: "accent-bg hover:opacity-90 disabled:opacity-40",
  secondary: "surface-2 hairline border hover:border-[rgb(var(--border-strong))] disabled:opacity-40",
  danger: "border border-rose-500/40 bg-rose-500/10 text-rose-400 hover:bg-rose-500/20 disabled:opacity-40",
  ghost: "muted hover:bg-[rgb(var(--surface-2))] disabled:opacity-40",
} as const;

export function Button({
  children,
  onClick,
  variant = "secondary",
  disabled,
  type = "button",
  className,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: keyof typeof BUTTON_VARIANTS;
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
  title?: string;
}) {
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors duration-150 disabled:cursor-not-allowed",
        BUTTON_VARIANTS[variant],
        className,
      )}
    >
      {children}
    </button>
  );
}

export function LinkButton({
  href,
  children,
  title,
}: {
  href: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <a
      href={href}
      title={title}
      className="surface-2 hairline inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition hover:opacity-80"
    >
      {children}
    </a>
  );
}

// --------------------------------------------------------------------------- //
export function StatTile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: BadgeTone;
}) {
  return (
    <div className="surface elevated hairline rounded-xl border p-4">
      <p className="eyebrow">{label}</p>
      <p
        className={cx(
          "tnum mt-2 text-[26px] font-semibold leading-none tracking-tight",
          tone === "good" && "text-good",
          tone === "bad" && "text-bad",
          tone === "warn" && "text-warn",
        )}
      >
        {value}
      </p>
      {hint && <div className="muted mt-2 text-xs">{hint}</div>}
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    // `relative` keeps absolutely-positioned descendants (sr-only labels) inside
    // the scroll container; otherwise they widen the whole page on mobile.
    <div className="relative overflow-x-auto">
      <table className={cx("w-full border-collapse text-left text-xs", className)}>{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      scope="col"
      className={cx(
        "subtle hairline whitespace-nowrap border-b px-3 py-2 text-2xs font-medium uppercase tracking-[0.06em]",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className,
  colSpan,
  title,
}: {
  children?: ReactNode;
  className?: string;
  colSpan?: number;
  title?: string;
}) {
  return (
    <td
      colSpan={colSpan}
      title={title}
      className={cx("hairline border-b px-3 py-2 align-top", className)}
    >
      {children}
    </td>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("surface-2 animate-pulse rounded", className)} />;
}

export function LoadingRows({ rows = 5, label }: { rows?: number; label?: string }) {
  return (
    <div className="space-y-2 p-4" role="status" aria-label={label ?? "Loading"}>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-7 w-full" />
      ))}
    </div>
  );
}

export function LoadingTiles({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" role="status" aria-label="Loading">
      {Array.from({ length: count }, (_, index) => (
        <Skeleton key={index} className="h-24" />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const isApi = error instanceof ApiError;
  const message = error instanceof Error ? error.message : "Unknown error";
  return (
    <div
      role="alert"
      className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-4 text-xs text-rose-300"
    >
      <p className="font-semibold">
        {isApi ? `Request failed (${(error as ApiError).status} ${(error as ApiError).code})` : "Request failed"}
      </p>
      <p className="mt-1 break-words">{message}</p>
      {isApi && (error as ApiError).fields.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {(error as ApiError).fields.map((field) => (
            <li key={`${field.location}-${field.message}`}>
              <code className="font-mono">{field.location}</code>: {field.message}
            </li>
          ))}
        </ul>
      )}
      {onRetry && (
        <div className="mt-3">
          <Button onClick={onRetry} variant="secondary">
            Retry
          </Button>
        </div>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="hairline flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-12 text-center">
      <p className="text-sm font-medium">{title}</p>
      {description && <div className="muted max-w-xl text-xs">{description}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

// --------------------------------------------------------------------------- //
export function Modal({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-6">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cx("surface hairline my-8 w-full rounded-xl border shadow-2xl", wide ? "max-w-4xl" : "max-w-xl")}
      >
        <header className="hairline flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <Button variant="ghost" onClick={onClose} title="Close (Escape)">
            Close
          </Button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="muted mt-1 block text-2xs">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "surface-2 hairline w-full rounded-md border px-2.5 py-1.5 text-xs outline-none transition-colors placeholder:opacity-50 hover:border-[rgb(var(--border-strong))] focus:border-[rgb(var(--accent)/0.6)]";

export function CodeBlock({
  children,
  className,
  maxHeight = "max-h-96",
}: {
  children: ReactNode;
  className?: string;
  maxHeight?: string;
}) {
  return (
    <pre
      className={cx(
        "surface-2 hairline overflow-auto rounded-md border p-3 font-mono text-2xs leading-relaxed",
        maxHeight,
        className,
      )}
    >
      {children}
    </pre>
  );
}

export function JsonBlock({ value, maxHeight }: { value: unknown; maxHeight?: string }) {
  return <CodeBlock maxHeight={maxHeight}>{JSON.stringify(value, null, 2)}</CodeBlock>;
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: string; label: string; count?: number }[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div role="tablist" aria-label="Sections" className="hairline flex gap-1 overflow-x-auto border-b px-2">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          aria-selected={active === tab.id}
          onClick={() => onChange(tab.id)}
          className={cx(
            "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-xs font-medium transition",
            active === tab.id
              ? "accent-text border-current"
              : "muted border-transparent hover:opacity-70",
          )}
        >
          {tab.label}
          {tab.count !== undefined && <span className="tnum ml-1.5 opacity-60">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function ProgressBar({ percent, muted }: { percent: number; muted?: boolean }) {
  return (
    <div className="surface-2 h-1.5 w-full overflow-hidden rounded-full">
      <div
        className={cx("h-full rounded-full", muted ? "bg-zinc-500" : "accent-bg")}
        style={{ width: `${Math.max(0, Math.min(100, percent))}%` }}
      />
    </div>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("font-mono text-2xs", className)}>{children}</span>;
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
      {rows.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="eyebrow">{key}</dt>
          <dd className="tnum mt-0.5 break-words text-xs">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// --------------------------------------------------------------------------- //
export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0 max-w-3xl">
        {eyebrow && <div className="eyebrow mb-1.5">{eyebrow}</div>}
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="muted mt-1 text-[13px] leading-relaxed">{description}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-1.5">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: ReactNode; title?: string }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="surface-2 hairline inline-flex rounded-lg border p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          title={option.title}
          onClick={() => onChange(option.value)}
          className={cx(
            "rounded-md px-2.5 py-1 text-xs font-medium transition-colors duration-150",
            value === option.value ? "surface elevated" : "muted hover:text-[rgb(var(--text))]",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search",
  label = "Search",
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
  className?: string;
}) {
  return (
    <div className={cx("relative", className)}>
      <svg
        aria-hidden
        viewBox="0 0 16 16"
        className="subtle pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      >
        <circle cx="7" cy="7" r="4.5" />
        <path d="m10.5 10.5 3 3" strokeLinecap="round" />
      </svg>
      <input
        type="search"
        aria-label={label}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={cx(inputClass, "pl-8")}
      />
    </div>
  );
}

export function Select({
  value,
  onChange,
  options,
  label,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  label: string;
  className?: string;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={cx(inputClass.replace("w-full", "w-auto max-w-full"), "cursor-pointer pr-7", className)}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/** A quiet explanatory line, for methodology notes that must stay visible. */
export function Note({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warn" | "info" }) {
  return (
    <p
      className={cx(
        "flex gap-2 text-xs leading-relaxed",
        tone === "warn" ? "text-warn" : tone === "info" ? "text-info" : "muted",
      )}
    >
      <svg aria-hidden viewBox="0 0 16 16" className="mt-0.5 h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="8" cy="8" r="6.25" />
        <path d="M8 7.25v4M8 4.75v.5" strokeLinecap="round" />
      </svg>
      <span>{children}</span>
    </p>
  );
}

/** Horizontal meter. `value` is 0-100; `null` renders an explicit "not available". */
export function Meter({
  value,
  tone = "accent",
  label,
}: {
  value: number | null | undefined;
  tone?: "accent" | "good" | "warn" | "bad";
  label: string;
}) {
  if (value === null || value === undefined) {
    return <div className="subtle text-2xs">not available</div>;
  }
  const color =
    tone === "good" ? "var(--good)" : tone === "warn" ? "var(--warn)" : tone === "bad" ? "var(--bad)" : "var(--accent)";
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value)}
      className="surface-3 h-1.5 w-full overflow-hidden rounded-full"
    >
      <div
        className="h-full rounded-full transition-[width] duration-500 ease-out"
        style={{ width: `${Math.max(0, Math.min(100, value))}%`, backgroundColor: `rgb(${color})` }}
      />
    </div>
  );
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}
