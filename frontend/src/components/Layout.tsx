import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";

import { useHealth, useLocalModels } from "../api/hooks";
import { SNAPSHOT_MODE, snapshotDate, snapshotManifest } from "../api/snapshot";
import { GitHubLink, GitHubMark } from "./GitHubLink";
import { cx } from "./ui";

// --------------------------------------------------------------------------- //
const ICONS: Record<string, ReactNode> = {
  overview: <path d="M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v7H9zM2.5 9h4.5v4.5H2.5zM9 11.5h4.5v2H9z" />,
  leaderboard: <path d="M2.5 13.5v-4h3v4M6.5 13.5v-9h3v9M10.5 13.5v-6.5h3v6.5M1.5 13.5h13" />,
  experiments: <path d="M6 1.75v4.5L2.25 12.5A1.25 1.25 0 0 0 3.33 14.25h9.34a1.25 1.25 0 0 0 1.08-1.75L10 6.25v-4.5M5 1.75h6M4 10h8" />,
  compare: <path d="M5.5 2v12M10.5 2v12M2 5h3.5M10.5 11H14M2 11h3.5M10.5 5H14" />,
  benchmarks: <path d="M2.5 3.5h11M2.5 8h11M2.5 12.5h7" />,
  trajectories: <path d="M3 2.5v11M3 4h6.5M3 8h9M3 12h5M9.5 4a1 1 0 1 0 2 0 1 1 0 1 0-2 0M12 8a1 1 0 1 0 2 0 1 1 0 1 0-2 0" />,
  failures: <path d="M8 2 1.75 13.25h12.5L8 2zM8 6.5v3M8 11.25v.25" />,
  analytics: <path d="M2 13.5 6 8.5l3 2.5 5-7M10.5 4H14v3.5" />,
  published: <path d="M8 1.75a6.25 6.25 0 1 1 0 12.5 6.25 6.25 0 0 1 0-12.5zM1.75 8h12.5M8 1.75c1.7 1.8 2.5 3.9 2.5 6.25S9.7 12.45 8 14.25C6.3 12.45 5.5 10.35 5.5 8S6.3 3.55 8 1.75z" />,
  models: <path d="M8 1.75 13.5 5v6L8 14.25 2.5 11V5L8 1.75zM2.5 5 8 8.25 13.5 5M8 8.25v6" />,
  agents: <path d="M8 2.25a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM3 13.75c.5-2.6 2.4-4 5-4s4.5 1.4 5 4" />,
  system: <path d="M2 3.5h12v7H2zM5.5 13.5h5M8 10.5v3M4 7.5l1.75-1.75L7.5 8l2-2.5L12 7.5" />,
  settings: <path d="M8 5.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />,
};

function Icon({ name }: { name: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      className="h-4 w-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.35"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {ICONS[name]}
    </svg>
  );
}

/** `live`: describes the running machine, so a static snapshot leaves it out. */
const NAV: { section: string; items: { to: string; label: string; icon: string; end?: boolean; live?: boolean }[] }[] = [
  {
    section: "Workspace",
    items: [
      { to: "/overview", label: "Overview", icon: "overview" },
      { to: "/leaderboard", label: "Leaderboard", icon: "leaderboard" },
      { to: "/experiments", label: "Experiments", icon: "experiments" },
      { to: "/compare", label: "Compare", icon: "compare" },
      { to: "/benchmarks", label: "Benchmarks", icon: "benchmarks" },
    ],
  },
  {
    section: "Analysis",
    items: [
      { to: "/trajectories", label: "Trajectories", icon: "trajectories" },
      { to: "/failures", label: "Failures", icon: "failures" },
      { to: "/analytics", label: "Analytics", icon: "analytics" },
      { to: "/published", label: "Published results", icon: "published" },
    ],
  },
  {
    section: "System",
    items: [
      { to: "/models", label: "Local Models", icon: "models", live: true },
      { to: "/agents", label: "Agent Configs", icon: "agents" },
      { to: "/system", label: "System Monitor", icon: "system", live: true },
      { to: "/settings", label: "Settings", icon: "settings" },
    ],
  },
];

export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="8" fill="rgb(var(--surface-3))" />
      <rect x="0.5" y="0.5" width="31" height="31" rx="7.5" fill="none" stroke="rgb(var(--border-strong))" />
      <rect x="8" y="17" width="4" height="7" rx="1" fill="rgb(var(--accent))" opacity="0.45" />
      <rect x="14" y="13" width="4" height="11" rx="1" fill="rgb(var(--accent))" opacity="0.7" />
      <rect x="20" y="8" width="4" height="16" rx="1" fill="rgb(var(--accent))" />
    </svg>
  );
}

// --------------------------------------------------------------------------- //
function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try {
      return window.localStorage.getItem("agentbench-theme") === "light" ? "light" : "dark";
    } catch {
      return "dark";
    }
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem("agentbench-theme", theme);
    } catch {
      // Storage can be unavailable (private mode); the theme still applies.
    }
  }, [theme]);

  return { theme, toggle: () => setTheme((current) => (current === "dark" ? "light" : "dark")) };
}

function Dot({ tone }: { tone: "good" | "warn" | "bad" | "idle" }) {
  const color =
    tone === "good" ? "var(--good)" : tone === "warn" ? "var(--warn)" : tone === "bad" ? "var(--bad)" : "var(--text-subtle)";
  return <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: `rgb(${color})` }} />;
}

function StatusRow({ label, value, tone, title }: { label: string; value: string; tone: "good" | "warn" | "bad" | "idle"; title?: string }) {
  return (
    <div className="flex items-center justify-between gap-2 text-2xs" title={title}>
      <span className="subtle">{label}</span>
      <span className="muted inline-flex min-w-0 items-center gap-1.5">
        <Dot tone={tone} />
        <span className="truncate">{value}</span>
      </span>
    </div>
  );
}

/** In a static snapshot there is no API, sandbox or Ollama to check. */
function SnapshotStatus() {
  const runs = snapshotManifest()?.counts.runs;
  return (
    <div className="space-y-1.5">
      <StatusRow label="Data" value="static snapshot" tone="idle" title="Read-only export; nothing here is live" />
      <StatusRow label="Exported" value={snapshotDate() || "—"} tone="idle" />
      <StatusRow label="Runs" value={runs === undefined ? "—" : String(runs)} tone="idle" />
    </div>
  );
}

function SnapshotBanner() {
  return (
    <div className="hairline surface-2 border-b px-4 py-2 text-2xs sm:px-6 lg:px-8">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-3 gap-y-1">
        <span className="accent-text font-medium uppercase tracking-[0.08em]">Read-only snapshot</span>
        <span className="muted">
          Real results recorded by a local Agent Labs instance{snapshotDate() ? `, exported ${snapshotDate()}` : ""}.
          Nothing here is live, and nothing can be launched or changed.
        </span>
        <GitHubLink className="link ml-auto inline-flex items-center gap-1.5">
          <GitHubMark className="h-3.5 w-3.5" />
          Run it yourself
        </GitHubLink>
      </div>
    </div>
  );
}

function SystemStatus() {
  const health = useHealth();
  const ollama = useLocalModels();
  const sandbox = health.data?.sandbox;
  const unsafe = sandbox?.provider === "local_unsafe";

  return (
    <div className="space-y-1.5">
      <StatusRow
        label="API"
        value={health.isError ? "unreachable" : health.data ? `v${health.data.version}` : "checking"}
        tone={health.isError ? "bad" : health.data ? "good" : "idle"}
      />
      <StatusRow
        label="Sandbox"
        value={
          !sandbox ? "—" : unsafe ? "local (no isolation)" : sandbox.available ? sandbox.provider : `${sandbox.provider} down`
        }
        tone={!sandbox ? "idle" : unsafe ? "bad" : sandbox.available ? "good" : "warn"}
        title={sandbox ? `${sandbox.detail} — isolation: ${sandbox.isolation}` : undefined}
      />
      <StatusRow
        label="Ollama"
        value={
          ollama.isError
            ? "unknown"
            : !ollama.data
              ? "checking"
              : ollama.data.reachable
                ? `${ollama.data.running_count} loaded`
                : "not running"
        }
        tone={!ollama.data ? "idle" : ollama.data.reachable ? "good" : "warn"}
      />
    </div>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { theme, toggle } = useTheme();
  return (
    <div className="flex h-full flex-col">
      <Link to="/" onClick={onNavigate} title="About Agent Labs" className="flex items-center gap-2.5 px-4 pb-4 pt-4">
        <Logo className="h-7 w-7" />
        <div className="min-w-0 leading-tight">
          <div className="text-[13px] font-semibold tracking-tight">Agent Labs</div>
          <div className="subtle text-2xs">Prove. Measure. Improve.</div>
        </div>
      </Link>

      <nav aria-label="Main" className="flex-1 space-y-5 overflow-y-auto px-2.5 pb-4">
        {NAV.map((group) => (
          <div key={group.section}>
            <div className="eyebrow px-2 pb-1.5">{group.section}</div>
            <ul className="space-y-px">
              {group.items.filter((item) => !(SNAPSHOT_MODE && item.live)).map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cx(
                        "flex items-center gap-2.5 rounded-md px-2 py-[5px] text-[13px] transition-colors duration-150",
                        isActive
                          ? "surface-3 font-medium text-[rgb(var(--text))]"
                          : "muted hover:bg-[rgb(var(--surface-2))] hover:text-[rgb(var(--text))]",
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span className={isActive ? "accent-text" : "subtle"}>
                          <Icon name={item.icon} />
                        </span>
                        {item.label}
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="hairline space-y-3 border-t px-4 py-3">
        {SNAPSHOT_MODE ? <SnapshotStatus /> : <SystemStatus />}
        <GitHubLink className="muted surface-2 hairline flex w-full items-center justify-between rounded-md border px-2 py-1 text-2xs transition-colors hover:text-[rgb(var(--text))]">
          <span className="inline-flex items-center gap-1.5">
            <GitHubMark className="h-3.5 w-3.5 transition-transform duration-300 group-hover:rotate-[-8deg]" />
            GitHub
          </span>
          <span className="subtle">Agent-Labs ↗</span>
        </GitHubLink>
        <button
          type="button"
          onClick={toggle}
          className="muted surface-2 hairline flex w-full items-center justify-between rounded-md border px-2 py-1 text-2xs transition-colors hover:text-[rgb(var(--text))]"
          title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        >
          <span>Theme</span>
          <span className="font-medium">{theme === "dark" ? "Dark" : "Light"}</span>
        </button>
      </div>
    </div>
  );
}

export default function Layout() {
  // The drawer closes from each link's onNavigate, so no route effect is needed.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open]);

  return (
    <div className="flex min-h-screen">
      <aside className="hairline surface sticky top-0 hidden h-screen w-[232px] shrink-0 border-r lg:block">
        <Sidebar />
      </aside>

      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button type="button" aria-label="Close navigation" className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <aside className="hairline surface fade-in absolute inset-y-0 left-0 w-[260px] border-r">
            <Sidebar onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="hairline surface sticky top-0 z-30 flex items-center gap-3 border-b px-4 py-2.5 lg:hidden">
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label="Open navigation"
            className="muted surface-2 hairline rounded-md border p-1.5"
          >
            <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" strokeLinecap="round" />
            </svg>
          </button>
          <Link to="/" className="flex items-center gap-2">
            <Logo className="h-6 w-6" />
            <span className="text-[13px] font-semibold tracking-tight">Agent Labs</span>
          </Link>
        </header>

        {SNAPSHOT_MODE && <SnapshotBanner />}
        <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <Outlet />
        </main>

        <footer className="hairline subtle border-t px-4 py-3 text-2xs sm:px-6 lg:px-8">
          Every rate shows its numerator and denominator. Infrastructure failures, broken task
          environments, grading errors and cancellations are excluded from success rates and
          reported separately.
        </footer>
      </div>
    </div>
  );
}
