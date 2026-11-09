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
    <section className={cx("surface rounded-lg border", className)}>
      {(title || actions) && (
        <header className="hairline flex items-start justify-between gap-4 border-b px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="truncate text-sm font-semibold">{title}</h2>}
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
  accent: "border border-indigo-500/30 bg-indigo-500/10 text-indigo-400",
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
        "inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-2xs font-medium",
        BADGE_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const BUTTON_VARIANTS = {
  primary: "accent-bg text-white hover:opacity-90 disabled:opacity-40",
  secondary: "surface-2 hairline border hover:opacity-80 disabled:opacity-40",
  danger: "border border-rose-500/40 bg-rose-500/10 text-rose-400 hover:bg-rose-500/20 disabled:opacity-40",
  ghost: "muted hover:opacity-70 disabled:opacity-40",
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
        "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed",
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
    <div className="surface hairline rounded-lg border p-4">
      <p className="muted text-2xs font-medium uppercase tracking-wide">{label}</p>
      <p
        className={cx(
          "tnum mt-1.5 text-2xl font-semibold",
          tone === "good" && "text-emerald-400",
          tone === "bad" && "text-rose-400",
          tone === "warn" && "text-amber-400",
        )}
      >
        {value}
      </p>
      {hint && <p className="muted mt-1 text-xs">{hint}</p>}
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className={cx("w-full border-collapse text-left text-xs", className)}>{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      scope="col"
      className={cx(
        "muted hairline whitespace-nowrap border-b px-3 py-2 text-2xs font-semibold uppercase tracking-wide",
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
    <div className="hairline flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-12 text-center">
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
      <span className="muted text-2xs font-semibold uppercase tracking-wide">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="muted mt-1 block text-2xs">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "surface-2 hairline w-full rounded-md border px-2.5 py-1.5 text-xs outline-none placeholder:opacity-50";

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
          <dt className="muted text-2xs font-semibold uppercase tracking-wide">{key}</dt>
          <dd className="tnum mt-0.5 break-words text-xs">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
