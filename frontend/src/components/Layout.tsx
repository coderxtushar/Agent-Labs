import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useHealth } from "../api/hooks";
import { Badge, cx } from "./ui";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/benchmarks", label: "Benchmarks" },
  { to: "/agents", label: "Agents" },
  { to: "/experiments", label: "Experiments" },
  { to: "/comparisons", label: "Comparisons" },
  { to: "/failures", label: "Failure analysis" },
  { to: "/settings", label: "Settings" },
];

function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const stored = window.localStorage.getItem("agentbench-theme");
    return stored === "light" ? "light" : "dark";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("agentbench-theme", theme);
  }, [theme]);

  return { theme, toggle: () => setTheme((current) => (current === "dark" ? "light" : "dark")) };
}

function SandboxStatus() {
  const { data, isError } = useHealth();
  if (isError) return <Badge tone="bad">API unreachable</Badge>;
  if (!data) return <Badge tone="neutral">checking…</Badge>;

  const unsafe = data.sandbox.provider === "local_unsafe";
  return (
    <Badge
      tone={unsafe ? "bad" : data.sandbox.available ? "good" : "warn"}
      title={`${data.sandbox.detail} — isolation: ${data.sandbox.isolation}`}
    >
      sandbox: {data.sandbox.provider}
      {unsafe && " (no isolation)"}
      {!data.sandbox.available && " unavailable"}
    </Badge>
  );
}

export default function Layout() {
  const { theme, toggle } = useTheme();
  const { data: health } = useHealth();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="surface hairline sticky top-0 z-30 border-b">
        <div className="flex items-center gap-4 px-4 py-2.5">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold tracking-tight">AgentBench Lab</span>
            <span className="muted font-mono text-2xs">v{health?.version ?? "—"}</span>
          </div>

          <nav aria-label="Main" className="flex flex-1 items-center gap-0.5 overflow-x-auto">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cx(
                    "whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium transition",
                    isActive ? "surface-2 accent-text" : "muted hover:opacity-70",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex shrink-0 items-center gap-2">
            <SandboxStatus />
            <button
              type="button"
              onClick={toggle}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              className="muted surface-2 hairline rounded-md border px-2 py-1.5 text-2xs font-medium hover:opacity-80"
            >
              {theme === "dark" ? "Light" : "Dark"}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-5">
        <Outlet />
      </main>

      <footer className="hairline muted border-t px-4 py-3 text-2xs">
        Rates show their numerator and denominator. Infrastructure failures, broken task
        environments and cancellations are excluded from success rates and reported separately.
      </footer>
    </div>
  );
}
