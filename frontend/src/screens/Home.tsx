/**
 * Public home page for Agent Labs.
 *
 * Honesty rules hold here too:
 * - The hero replays a real recorded run (run daef026b, bonsai-2 on
 *   textkit-wordwrap, 2026-11-20): event types, arguments and time offsets are
 *   copied from its stored events, condensed but not invented.
 * - The live numbers come from `/overview`; when the API is down the page says
 *   so instead of showing placeholders.
 *
 * Motion: one entrance on load, two purposeful loops (the run replay and the
 * pipeline pulses) behind a single pause switch, hover/press feedback. Under
 * prefers-reduced-motion everything rests in its final state.
 */

import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { useOverview } from "../api/hooks";
import { OutcomeStack, RateBar } from "../components/charts";
import { GitHubLink, GitHubMark } from "../components/GitHubLink";
import { Logo } from "../components/Layout";
import { Badge, cx } from "../components/ui";

// --------------------------------------------------------------------------- //
// hooks
// --------------------------------------------------------------------------- //
function subscribeReducedMotion(callback: () => void) {
  const query = window.matchMedia("(prefers-reduced-motion: reduce)");
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}

function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    () => false,
  );
}

/** Counts up once when scrolled into view; static under reduced motion. */
function CountUp({ value, decimals = 0, suffix = "" }: { value: number; decimals?: number; suffix?: string }) {
  const reduced = useReducedMotion();
  const ref = useRef<HTMLSpanElement>(null);
  const [shown, setShown] = useState(0);

  useEffect(() => {
    if (reduced || !ref.current) return;
    let frame = 0;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        observer.disconnect();
        const start = performance.now();
        const tick = (now: number) => {
          const t = Math.min(1, (now - start) / 1100);
          setShown(value * (1 - Math.pow(1 - t, 3)));
          if (t < 1) frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
      },
      { threshold: 0.4 },
    );
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [value, reduced]);

  const display = reduced ? value : shown;
  return (
    <span ref={ref} className="tnum">
      {display.toFixed(decimals)}
      {suffix}
    </span>
  );
}

// --------------------------------------------------------------------------- //
// small parts
// --------------------------------------------------------------------------- //
function Container({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("mx-auto w-full max-w-[1200px] px-5 sm:px-8", className)}>{children}</div>;
}

function Arrow() {
  return (
    <svg aria-hidden viewBox="0 0 16 16" className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M3 8h10M9 4l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PrimaryButton({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return (
    <Link to={to} className={cx("accent-bg press group inline-flex items-center justify-center gap-1.5 rounded-lg px-4 py-2.5 text-sm font-medium hover:opacity-95", className)}>
      {children} <Arrow />
    </Link>
  );
}

function SecondaryButton({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} className="surface-2 hairline press inline-flex items-center justify-center rounded-lg border px-4 py-2.5 text-sm font-medium hover:border-[rgb(var(--border-strong))]">
      {children}
    </a>
  );
}

function SectionHeading({ eyebrow, title, children, className }: { eyebrow: string; title: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <div className={cx("max-w-2xl", className)}>
      <p className="accent-text text-xs font-medium tracking-wide">{eyebrow}</p>
      <h2 className="mt-3 text-[28px] font-semibold leading-[1.15] tracking-[-0.02em] sm:text-[36px]">{title}</h2>
      {children && <p className="muted mt-4 text-[15px] leading-relaxed">{children}</p>}
    </div>
  );
}

function PauseButton({ paused, onToggle }: { paused: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={paused}
      aria-label={paused ? "Play animations" : "Pause animations"}
      className="subtle hairline press inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-2xs hover:text-[rgb(var(--text))]"
    >
      <svg aria-hidden viewBox="0 0 16 16" className="h-3 w-3" fill="currentColor">
        {paused ? <path d="M5 3.5v9l7-4.5z" /> : <path d="M4.5 3.5h2.5v9H4.5zM9 3.5h2.5v9H9z" />}
      </svg>
      {paused ? "Play" : "Pause"}
    </button>
  );
}

// --------------------------------------------------------------------------- //
// nav
// --------------------------------------------------------------------------- //
function TopNav() {
  const [scrolled, setScrolled] = useState(() => window.scrollY > 8);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={cx("sticky top-0 z-40 border-b backdrop-blur-md transition-colors duration-300", scrolled ? "hairline" : "border-transparent")}
      style={{ backgroundColor: scrolled ? "rgb(var(--bg) / 0.78)" : "transparent" }}
    >
      <Container className="flex h-16 items-center gap-8">
        <Link to="/" className="flex items-center gap-2.5" aria-label="Agent Labs home">
          <Logo className="h-7 w-7" />
          <span className="text-[15px] font-semibold tracking-tight">Agent Labs</span>
        </Link>
        <nav aria-label="Home sections" className="muted hidden items-center gap-6 text-[13px] md:flex">
          {[
            ["#how", "How it works"],
            ["#principles", "Principles"],
            ["#features", "Features"],
            ["#local", "Run locally"],
          ].map(([href, label]) => (
            <a key={href} href={href} className="draw py-1 transition-colors hover:text-[rgb(var(--text))]">
              {label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <GitHubLink className="muted hairline press inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-[13px] font-medium hover:border-[rgb(var(--border-strong))] hover:text-[rgb(var(--text))]">
            <GitHubMark className="h-4 w-4 transition-transform duration-300 group-hover:rotate-[-8deg] group-hover:scale-110" />
            <span className="hidden sm:inline">GitHub</span>
          </GitHubLink>
          <PrimaryButton to="/overview" className="px-3.5 py-2 text-[13px]">
            Open the lab
          </PrimaryButton>
        </div>
      </Container>
    </header>
  );
}

// --------------------------------------------------------------------------- //
// hero: replay of a real recorded run
// --------------------------------------------------------------------------- //
type Tone = "model" | "tool" | "file" | "warn" | "good" | "patch";

/** Condensed from the stored events of run daef026b84e0474b9f82b361933cdb91. */
const REPLAY: { t: string; kind: string; text: string; tone: Tone }[] = [
  { t: "+0:00", kind: "agent.start", text: "bonsai-2-27b-32k · 5 tools · max 60 steps", tone: "model" },
  { t: "+0:17", kind: "tool.call", text: "list_files()  →  ok · 225 chars", tone: "tool" },
  { t: "+0:18", kind: "tool.call", text: "read_file(src/textkit/wrap.py)  →  ok · 829 chars", tone: "tool" },
  { t: "+6:32", kind: "model.response", text: "“Now I'll implement `wrap_text` in `src/textkit/wrap.py`”", tone: "model" },
  { t: "+6:33", kind: "file.write", text: "src/textkit/wrap.py · 1,534 bytes · 54 lines", tone: "file" },
  { t: "+7:19", kind: "command.exec", text: "python3 -c \"from textkit import wrap_text …\"  →  exit 1", tone: "warn" },
  { t: "+8:19", kind: "command.exec", text: "PYTHONPATH=src python3 -c \"…\"  →  exit 1", tone: "warn" },
  { t: "+9:24", kind: "model.response", text: "“The algorithm is correct — my expected value in the test was wrong.”", tone: "model" },
  { t: "+9:25", kind: "command.exec", text: "PYTHONPATH=src python3 -c \"…\"  →  exit 0 · OK", tone: "good" },
  { t: "+10:37", kind: "tool.call", text: "finish(\"Implemented `wrap_text` …\")", tone: "tool" },
  { t: "+10:37", kind: "patch.generated", text: "captured on host · 1 file · +32 −3", tone: "patch" },
];

const GRADING: { layer: string; detail: string; injected?: boolean }[] = [
  { layer: "environment", detail: "setup succeeded" },
  { layer: "patch", detail: "1 file changed, +32/−3" },
  { layer: "hidden tests", detail: "injected after patch capture", injected: true },
  { layer: "fail_to_pass", detail: "9 passed, 0 failed" },
  { layer: "pass_to_pass", detail: "2 passed, 0 failed" },
];

const HOLD_TICKS = 6;
const TOTAL_TICKS = REPLAY.length + GRADING.length + 1;

const TONE_COLOR: Record<Tone, string> = {
  model: "rgb(var(--accent))",
  tool: "rgb(var(--text-muted))",
  file: "rgb(var(--info))",
  warn: "rgb(var(--warn))",
  good: "rgb(var(--good))",
  patch: "rgb(var(--good))",
};

function RunReplay({ paused, onTogglePause }: { paused: boolean; onTogglePause: () => void }) {
  const reduced = useReducedMotion();
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (reduced || paused) return;
    const id = window.setInterval(() => {
      setTick((current) => (current >= TOTAL_TICKS + HOLD_TICKS ? 0 : current + 1));
    }, 620);
    return () => window.clearInterval(id);
  }, [reduced, paused]);

  const step = reduced ? TOTAL_TICKS : Math.min(tick, TOTAL_TICKS);
  const rows = REPLAY.slice(0, Math.min(step, REPLAY.length));
  const gradingShown = Math.max(0, Math.min(GRADING.length, step - REPLAY.length));
  const verdict = step >= TOTAL_TICKS;

  return (
    <div className="surface hairline-strong overflow-hidden rounded-2xl border" style={{ boxShadow: "0 40px 100px -50px rgb(0 0 0 / 0.8), 0 0 0 1px rgb(var(--accent) / 0.06)" }}>
      {/* window chrome */}
      <div className="hairline surface-2 flex items-center gap-3 border-b px-4 py-2.5">
        <div className="flex gap-1.5" aria-hidden>
          {[0, 1, 2].map((dot) => (
            <span key={dot} className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: "rgb(var(--border-strong))" }} />
          ))}
        </div>
        <div className="min-w-0 flex-1 truncate font-mono text-2xs">
          <span className="subtle">run </span>daef026b
          <span className="subtle"> · bonsai-2-27b-local@1.0 · textkit-wordwrap@1.0</span>
        </div>
        {!reduced && <PauseButton paused={paused} onToggle={onTogglePause} />}
      </div>

      <div className="grid md:grid-cols-[1.8fr_1fr]">
        {/* trajectory */}
        <div className="min-w-0 p-4">
          <div className="mb-3 flex items-center justify-between">
            <span className="subtle text-2xs font-medium uppercase tracking-[0.08em]">Trajectory</span>
            <span className="subtle text-2xs">replay of recorded events · real time offsets</span>
          </div>
          <ol className="flex h-[354px] flex-col justify-end gap-0.5 overflow-hidden" aria-label="Recorded events of the run">
            {rows.map((row, index) => (
              <li key={`${row.kind}-${index}`} title={`${row.kind} ${row.text}`} className={cx("flex shrink-0 items-center gap-2.5 rounded-md px-2 py-[5px]", !reduced && "row-in", row.tone === "warn" && "bg-[rgb(var(--warn)/0.07)]")}>
                <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: TONE_COLOR[row.tone] }} />
                <span className="subtle tnum w-12 shrink-0 font-mono text-2xs leading-5">{row.t}</span>
                <span className="w-[112px] shrink-0 font-mono text-[11px] leading-5" style={{ color: TONE_COLOR[row.tone] }}>{row.kind}</span>
                <span className="muted min-w-0 truncate font-mono text-[11px] leading-5">{row.text}</span>
              </li>
            ))}
          </ol>
        </div>

        {/* grading */}
        <div className="hairline min-w-0 border-t p-4 md:border-l md:border-t-0">
          <span className="subtle text-2xs font-medium uppercase tracking-[0.08em]">Grading</span>
          <ul className="mt-3 space-y-2" aria-label="Grading layers">
            {GRADING.map((layer, index) => {
              const done = index < gradingShown;
              return (
                <li key={layer.layer} className={cx("flex items-start gap-2.5 transition-opacity duration-300", done ? "opacity-100" : "opacity-30")}>
                  <span
                    className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-colors duration-300"
                    style={{
                      borderColor: done ? (layer.injected ? "rgb(var(--info))" : "rgb(var(--good))") : "rgb(var(--border-strong))",
                      backgroundColor: done ? (layer.injected ? "rgb(var(--info) / 0.12)" : "rgb(var(--good) / 0.12)") : "transparent",
                    }}
                  >
                    {done && (
                      <svg aria-hidden viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="none" stroke={layer.injected ? "rgb(var(--info))" : "rgb(var(--good))"} strokeWidth="2">
                        {layer.injected ? <path d="M6 2.5v7M2.5 6h7" strokeLinecap="round" /> : <path d="m2.5 6.2 2.3 2.3 4.7-5" strokeLinecap="round" strokeLinejoin="round" />}
                      </svg>
                    )}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-mono text-[11px]">{layer.layer}</span>
                    <span className="subtle block text-2xs">{layer.detail}</span>
                  </span>
                </li>
              );
            })}
          </ul>
          <div
            className={cx("mt-5 rounded-lg border p-3 transition-all duration-500", verdict ? "opacity-100" : "translate-y-1 opacity-0")}
            style={{ borderColor: "rgb(var(--good) / 0.35)", backgroundColor: "rgb(var(--good) / 0.07)" }}
          >
            <div className="flex items-center justify-between">
              <span className="text-good text-sm font-semibold">Solved</span>
              <span className="subtle font-mono text-2xs">score 1.0</span>
            </div>
            <p className="muted mt-1 text-2xs leading-relaxed">Decided by tests the agent never saw. Wall time 10m 44s.</p>
            <Link to="/runs/daef026b84e0474b9f82b361933cdb91" className="accent-text group mt-2 inline-flex items-center gap-1 text-2xs font-medium" tabIndex={verdict ? 0 : -1}>
              Open this run <Arrow />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function Hero({ paused, onTogglePause }: { paused: boolean; onTogglePause: () => void }) {
  return (
    <section className="relative overflow-hidden">
      <div aria-hidden className="grid-ground pointer-events-none absolute inset-0" />
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-[-12%] h-[520px] w-[900px] -translate-x-1/2 rounded-full opacity-[0.10] blur-3xl"
        style={{ background: "radial-gradient(closest-side, rgb(var(--accent)), transparent)" }}
      />
      <Container className="relative pb-20 pt-12 sm:pt-16">
        <div className="grid items-end gap-8 lg:grid-cols-[1.1fr_1fr] lg:gap-16">
          <div>
            <div className="enter" style={{ "--i": 0 } as CSSProperties}>
              <span className="surface-2 hairline inline-flex items-center gap-2 rounded-full border py-1 pl-1.5 pr-3 text-2xs">
                <span className="accent-bg rounded-full px-2 py-0.5 font-medium">Local-first</span>
                <span className="muted">Free local models through Ollama</span>
              </span>
            </div>
            <h1 className="enter mt-6 text-[40px] font-semibold leading-[1.04] tracking-[-0.035em] sm:text-[56px]" style={{ "--i": 1 } as CSSProperties}>
              The evaluation lab for AI coding agents.
            </h1>
          </div>
          <div>
            <p className="enter muted text-[16px] leading-relaxed" style={{ "--i": 2 } as CSSProperties}>
              Run agents on real software tasks in sealed containers, grade them with hidden tests
              injected only after their patch is captured, and read every result with its run count
              and 95% interval. <span className="text-[rgb(var(--text))]">Prove. Measure. Improve.</span>
            </p>
            <div className="enter mt-6 flex flex-wrap gap-3" style={{ "--i": 3 } as CSSProperties}>
              <PrimaryButton to="/overview">Open the lab</PrimaryButton>
              <SecondaryButton href="#how">See how grading works</SecondaryButton>
            </div>
          </div>
        </div>
        <div className="enter mt-12 min-w-0" style={{ "--i": 4 } as CSSProperties}>
          <RunReplay paused={paused} onTogglePause={onTogglePause} />
        </div>
        <dl className="enter mt-8 grid grid-cols-1 gap-4 sm:grid-cols-3" style={{ "--i": 5 } as CSSProperties}>
          {[
            ["Sandbox", "--network none · --cap-drop ALL · no mounts"],
            ["Patch capture", "host-side diff against a pristine copy"],
            ["Every rate", "numerator / denominator · Wilson 95%"],
          ].map(([label, value]) => (
            <div key={label} className="hairline border-l pl-3">
              <dt className="subtle text-2xs">{label}</dt>
              <dd className="mt-0.5 font-mono text-[12px]">{value}</dd>
            </div>
          ))}
        </dl>
      </Container>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// live numbers
// --------------------------------------------------------------------------- //
function LiveNumbers() {
  const { data, isError, isLoading } = useOverview();
  const rate = data?.model_results.success_rate;
  const best = data?.best_observed;

  return (
    <section aria-labelledby="live-heading" className="hairline border-y">
      <Container className="py-10">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="live-heading" className="flex items-center gap-2.5 text-sm font-medium">
            <span className="beacon h-2 w-2 rounded-full" style={{ backgroundColor: "rgb(var(--good))" }} />
            Live from this lab
          </h2>
          <p className="subtle text-2xs">Read from the local API just now · never sample data</p>
        </div>
        {isLoading && <p className="muted mt-6 text-sm">Reading results…</p>}
        {isError && (
          <p className="muted mt-6 text-sm">
            The API is not running, so there are no live numbers to show. Start it with{" "}
            <code className="surface-2 rounded px-1.5 py-0.5 font-mono text-xs">agentbench serve</code>.
          </p>
        )}
        {data && (
          <dl className="mt-8 grid grid-cols-2 gap-x-8 gap-y-8 lg:grid-cols-4">
            <div>
              <dt className="subtle text-xs">Experiments run</dt>
              <dd className="mt-2 text-[34px] font-semibold leading-none tracking-tight"><CountUp value={data.experiments.total} /></dd>
            </div>
            <div>
              <dt className="subtle text-xs">Runs recorded</dt>
              <dd className="mt-2 text-[34px] font-semibold leading-none tracking-tight"><CountUp value={data.runs.total} /></dd>
              <dd className="muted mt-2 text-xs">{data.model_results.finished_runs} from live models</dd>
            </div>
            <div>
              <dt className="subtle text-xs">Model success rate</dt>
              <dd className="mt-2 text-[34px] font-semibold leading-none tracking-tight">
                {rate && rate.percent !== null ? <CountUp value={rate.percent} decimals={1} suffix="%" /> : "—"}
              </dd>
              {rate && <dd className="muted mt-2 text-xs">{rate.numerator}/{rate.denominator} attributable runs</dd>}
            </div>
            <div className="min-w-0">
              <dt className="subtle text-xs">Highest observed</dt>
              <dd className="mt-2 truncate font-mono text-[15px] leading-[34px]">{best ? best.label : "—"}</dd>
              <dd className="muted mt-2 text-xs">
                {best
                  ? `${best.success_rate.numerator}/${best.success_rate.denominator} solved · ${
                      best.distinguishable_from_runner_up === null
                        ? "no runner-up yet"
                        : best.distinguishable_from_runner_up
                          ? "ahead of runner-up"
                          : "not yet distinguishable from runner-up"
                    }`
                  : "no model results yet"}
              </dd>
            </div>
          </dl>
        )}
      </Container>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// how it works: pipeline with travelling pulses
// --------------------------------------------------------------------------- //
const PIPELINE = [
  { title: "Task spec", detail: "repo · image · tests", body: "Pinned repository, environment image, tests that must start passing and keep passing, hidden acceptance tests. Validated as runnable and falsifiable first." },
  { title: "Sealed sandbox", detail: "no network · no mounts", body: "The agent works in Docker with --network none, every capability dropped and no host mounts. Provider keys never enter the container." },
  { title: "Patch on host", detail: "diff vs pristine copy", body: "The workspace is copied out and diffed against a pristine baseline on the host. The agent's own git state is never trusted." },
  { title: "Hidden tests", detail: "injected after capture", body: "Only now are the acceptance tests added and run, with runner config files locked so a patch cannot rewrite the rules." },
  { title: "Verdict + evidence", detail: "attributed outcome", body: "Solved, unsolved, timeout or agent error — or excluded as infra, invalid task or grading error. Every event kept." },
];

function Pipeline() {
  return (
    <div className="mt-14">
      {/* desktop: horizontal track */}
      <div className="hidden grid-cols-5 gap-0 lg:grid" aria-hidden>
        {PIPELINE.map((step, index) => (
          <div key={step.title} className="relative flex items-center">
            <div className={cx("surface relative z-10 flex h-11 w-11 items-center justify-center rounded-xl border font-mono text-xs", index === PIPELINE.length - 1 ? "text-good" : "accent-text")} style={{ borderColor: index === PIPELINE.length - 1 ? "rgb(var(--good) / 0.45)" : "rgb(var(--accent) / 0.4)" }}>
              0{index + 1}
            </div>
            {index < PIPELINE.length - 1 && (
              <div className="relative mx-3 h-px flex-1 overflow-hidden" style={{ backgroundColor: "rgb(var(--border-strong))" }}>
                <span className="pulse-x" style={{ "--delay": `${index * 0.55}s` } as CSSProperties} />
              </div>
            )}
          </div>
        ))}
      </div>
      <ol className="mt-0 grid gap-8 lg:mt-6 lg:grid-cols-5 lg:gap-6">
        {PIPELINE.map((step, index) => (
          <li key={step.title} className="relative pl-14 lg:pl-0 lg:pr-4">
            {/* mobile: vertical track */}
            <div className="absolute left-0 top-0 flex h-full flex-col items-center lg:hidden" aria-hidden>
              <div className={cx("surface flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border font-mono text-xs", index === PIPELINE.length - 1 ? "text-good" : "accent-text")} style={{ borderColor: "rgb(var(--accent) / 0.4)" }}>
                0{index + 1}
              </div>
              {index < PIPELINE.length - 1 && (
                <div className="relative my-2 w-px flex-1 overflow-hidden" style={{ backgroundColor: "rgb(var(--border-strong))" }}>
                  <span className="pulse-y" style={{ "--delay": `${index * 0.55}s` } as CSSProperties} />
                </div>
              )}
            </div>
            <h3 className="text-[15px] font-semibold tracking-tight">{step.title}</h3>
            <p className="subtle mt-1 font-mono text-2xs">{step.detail}</p>
            <p className="muted mt-3 text-[13px] leading-relaxed">{step.body}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}

function HowItWorks({ paused, onTogglePause }: { paused: boolean; onTogglePause: () => void }) {
  const reduced = useReducedMotion();
  return (
    <section id="how" className="scroll-mt-16 py-24 sm:py-32">
      <Container>
        <div className="flex flex-wrap items-end justify-between gap-6">
          <SectionHeading eyebrow="How grading works" title="The agent never grades itself.">
            An agent saying “done” means it stopped. Whether it solved the task is decided later, on the
            host, by tests it never saw.
          </SectionHeading>
          {!reduced && <PauseButton paused={paused} onToggle={onTogglePause} />}
        </div>
        <Pipeline />
      </Container>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// principles: bento with real fragments
// --------------------------------------------------------------------------- //
function BentoCard({ title, body, children, className }: { title: string; body: string; children?: ReactNode; className?: string }) {
  return (
    <div className={cx("surface hairline lift flex flex-col justify-between gap-6 rounded-2xl border p-6", className)}>
      {children && <div className="min-h-[88px]">{children}</div>}
      <div>
        <h3 className="text-[15px] font-semibold tracking-tight">{title}</h3>
        <p className="muted mt-2 text-[13px] leading-relaxed">{body}</p>
      </div>
    </div>
  );
}

function Principles() {
  const { data } = useOverview();
  const best = data?.best_observed;
  const outcomes = data?.model_results.outcomes;

  return (
    <section id="principles" className="surface-2 hairline scroll-mt-16 border-y py-24 sm:py-32">
      <Container>
        <SectionHeading eyebrow="Principles" title="Built so the benchmark cannot lie.">
          Enforced in code and covered by tests, not written in a style guide.
        </SectionHeading>
        <div className="mt-14 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <BentoCard
            className="lg:col-span-2"
            title="Every rate shows its runs"
            body="A rate is never printed alone: its numerator, denominator and 95% Wilson interval come with it, and fewer than 30 runs is flagged as indicative."
          >
            {best ? (
              <div className="max-w-md">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-mono text-xs">{best.label}</span>
                  <span className="tnum text-sm font-semibold">
                    {best.success_rate.percent}% <span className="muted font-normal">({best.success_rate.numerator}/{best.success_rate.denominator})</span>
                  </span>
                </div>
                <RateBar rate={best.success_rate} className="mt-3" />
                <p className="subtle mt-2 text-2xs">
                  95% CI {((best.success_rate.ci_low ?? 0) * 100).toFixed(0)}–{((best.success_rate.ci_high ?? 0) * 100).toFixed(0)}% · live from this lab
                </p>
              </div>
            ) : (
              <p className="subtle font-mono text-xs">66.7% → printed as 66.7% (2/3), 95% CI 21–94%</p>
            )}
          </BentoCard>
          <BentoCard title="Unknown is not zero" body="Unreported tokens stay unreported, inconclusive grading is a grading error, and a failure no rule can classify stays unclassified.">
            <div className="flex flex-wrap gap-2">
              <Badge>tokens: not reported</Badge>
              <Badge>grading: inconclusive</Badge>
              <Badge>failure: unclassified</Badge>
            </div>
          </BentoCard>
          <BentoCard title="Failures are attributed" body="Infrastructure errors, broken tasks and grading errors are excluded from success rates and shown apart, so a crashed harness never lowers a model's score.">
            {outcomes ? <OutcomeStack outcomes={outcomes} showLegend /> : <p className="subtle text-xs">Outcome mix appears once runs exist.</p>}
          </BentoCard>
          <BentoCard title="Simulated is never a score" body="Every result carries its provenance. Pipeline checks by scripted agents never appear as model performance.">
            <div className="flex flex-wrap gap-2">
              <Badge tone="good">live model</Badge>
              <Badge tone="warn">simulated · not a model score</Badge>
              <Badge tone="info">external agent</Badge>
            </div>
          </BentoCard>
          <BentoCard title="Evidence is append-only" body="Run events, retry history and failure labels are only ever added to. A human correction supersedes a label; it never erases one.">
            <div className="space-y-1.5 font-mono text-[11px]">
              <p><span className="subtle">#1 rule </span>no_patch_produced <span className="subtle">· superseded</span></p>
              <p><span className="subtle">#2 human </span>tool_invocation_error</p>
            </div>
          </BentoCard>
        </div>
      </Container>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// features
// --------------------------------------------------------------------------- //
const FEATURE_GROUPS: { group: string; items: { to: string; title: string; body: string }[] }[] = [
  {
    group: "Workspace",
    items: [
      { to: "/overview", title: "Overview", body: "Model-only success rate, best observed model, runtime, and every excluded run with its reason." },
      { to: "/leaderboard", title: "Leaderboard", body: "Agents ranked by observed success with 95% intervals; rows that overlap the leader are marked, not ranked." },
      { to: "/experiments", title: "Experiments", body: "Search, filter and sort every experiment, each with a reproducibility record and its exact CLI command." },
      { to: "/compare", title: "Compare", body: "Two to six experiments side by side and a task-by-task outcome matrix. Unlike things are refused." },
      { to: "/benchmarks", title: "Benchmarks", body: "Task specs, environments, grading requirements, validation status and per-task history." },
    ],
  },
  {
    group: "Analysis",
    items: [
      { to: "/trajectories", title: "Trajectories", body: "Every recorded event of a run, a raw JSON view, and a patch viewer that separates generated from validated." },
      { to: "/failures", title: "Failures", body: "Timeouts, wrong patches, regressions, tool errors and tampering, classified only where the evidence holds." },
      { to: "/analytics", title: "Analytics", body: "Activity over time, success by category and difficulty, durations by outcome, token and tool usage." },
      { to: "/published", title: "Published results", body: "Frontier models on public leaderboards with sources and intervals, kept apart from your own runs." },
    ],
  },
  {
    group: "System",
    items: [
      { to: "/models", title: "Local models", body: "Installed and loaded Ollama models, the agents that use them and how they scored. Read-only." },
      { to: "/system", title: "System monitor", body: "RAM, swap, CPU, Docker and live sandboxes, so a slow run can be told from a swapping machine." },
      { to: "/agents", title: "Agent configs", body: "Versioned, immutable agent configurations with live readiness checks." },
    ],
  },
];

function Shot({ src, alt, to }: { src: string; alt: string; to: string }) {
  return (
    <Link to={to} className="zoom-frame lift hairline-strong surface group block overflow-hidden rounded-2xl border">
      <div className="hairline surface-2 flex items-center gap-1.5 border-b px-3 py-2" aria-hidden>
        {[0, 1, 2].map((dot) => (
          <span key={dot} className="h-2 w-2 rounded-full" style={{ backgroundColor: "rgb(var(--border-strong))" }} />
        ))}
      </div>
      <div className="overflow-hidden">
        <img src={src} alt={alt} loading="lazy" decoding="async" width={1440} height={900} className="block w-full" />
      </div>
    </Link>
  );
}

function Features() {
  return (
    <section id="features" className="scroll-mt-16 py-24 sm:py-32">
      <Container>
        <div className="flex flex-wrap items-end justify-between gap-6">
          <SectionHeading eyebrow="Inside the lab" title="Every view an honest evaluation needs.">
            Each one is a page in the app. Open any of them directly.
          </SectionHeading>
          <PrimaryButton to="/overview">Open the lab</PrimaryButton>
        </div>

        <div className="mt-14 grid gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <Shot src="/home/leaderboard.jpg" alt="Agent Labs leaderboard with success rates, run counts and 95% intervals" to="/leaderboard" />
          </div>
          <Shot src="/home/compare.jpg" alt="Compare studio with arm summaries and a task-by-task outcome matrix" to="/compare" />
        </div>

        <div className="mt-16 grid gap-12 lg:grid-cols-3 lg:gap-8">
          {FEATURE_GROUPS.map((group) => (
            <div key={group.group}>
              <p className="subtle text-2xs font-medium uppercase tracking-[0.1em]">{group.group}</p>
              <ul className="hairline mt-4 divide-y border-y" style={{ borderColor: "rgb(var(--border))" }}>
                {group.items.map((item) => (
                  <li key={item.to} className="hairline" style={{ borderColor: "rgb(var(--border))" }}>
                    <Link to={item.to} className="group flex items-start justify-between gap-4 py-4">
                      <span className="min-w-0">
                        <span className="block text-[14px] font-medium tracking-tight transition-colors group-hover:text-[rgb(var(--accent))]">{item.title}</span>
                        <span className="muted mt-1 block text-[13px] leading-relaxed">{item.body}</span>
                      </span>
                      <span className="subtle mt-0.5 shrink-0 group-hover:text-[rgb(var(--accent))]">
                        <Arrow />
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Container>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// run locally + close
// --------------------------------------------------------------------------- //
const QUICKSTART: [string, string][] = [
  ["$", "cd backend && uv venv && uv pip install -e '.[dev]'"],
  ["$", ".venv/bin/python -m agentbench.cli.main images build"],
  ["$", ".venv/bin/python -m agentbench.cli.main bootstrap"],
  ["$", "AGENTBENCH_API_PORT=8077 .venv/bin/python -m agentbench.cli.main serve"],
  ["$", "cd ../frontend && npm install && npm run dev"],
  ["→", "open http://localhost:5173"],
];

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <button
      type="button"
      className="subtle hairline press rounded-md border px-2 py-1 text-2xs hover:text-[rgb(var(--text))]"
      onClick={() => {
        navigator.clipboard
          ?.writeText(text)
          .then(() => setState("copied"))
          .catch(() => setState("failed"));
        window.setTimeout(() => setState("idle"), 1600);
      }}
    >
      {state === "copied" ? "Copied" : state === "failed" ? "Select to copy" : "Copy"}
    </button>
  );
}

function RunLocally() {
  return (
    <section id="local" className="surface-2 hairline scroll-mt-16 border-y py-24 sm:py-32">
      <Container className="grid items-center gap-12 lg:grid-cols-[1fr_1.15fr]">
        <div>
          <SectionHeading eyebrow="Run locally" title="Your laptop is the lab.">
            No paid API, no account, no data leaving the machine. Evaluate free local models through
            Ollama, or point it at any OpenAI-compatible endpoint.
          </SectionHeading>
          <dl className="mt-10 grid grid-cols-2 gap-6">
            {[
              ["Runtime", "Python 3.11+, Node 20+"],
              ["Sandbox", "Docker Desktop"],
              ["Models", "Ollama, tool-calling"],
              ["Storage", "SQLite or PostgreSQL"],
            ].map(([label, value]) => (
              <div key={label} className="hairline border-l pl-3">
                <dt className="subtle text-2xs">{label}</dt>
                <dd className="mt-1 text-[13px]">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="surface hairline-strong min-w-0 overflow-hidden rounded-2xl border">
          <div className="hairline flex items-center justify-between border-b px-4 py-2.5">
            <span className="subtle font-mono text-2xs">~/agent-labs</span>
            <CopyButton text={QUICKSTART.filter(([p]) => p === "$").map(([, c]) => c).join("\n")} />
          </div>
          <pre className="overflow-x-auto p-5 font-mono text-[12px] leading-[1.9]">
            {QUICKSTART.map(([prompt, command]) => (
              <div key={command}>
                <span className={prompt === "$" ? "subtle" : "text-good"}>{prompt} </span>
                {command}
              </div>
            ))}
          </pre>
        </div>
      </Container>
    </section>
  );
}

function Close() {
  return (
    <section className="relative overflow-hidden py-28 sm:py-36">
      <div aria-hidden className="grid-ground pointer-events-none absolute inset-0 opacity-70" />
      <Container className="relative text-center">
        <h2 className="mx-auto max-w-3xl text-[36px] font-semibold leading-[1.05] tracking-[-0.035em] sm:text-[56px]">
          Stop guessing which agent is better.
        </h2>
        <p className="muted mx-auto mt-5 max-w-xl text-[16px] leading-relaxed">
          Run it, grade it with tests it never saw, and read the answer with the evidence attached.
        </p>
        <div className="mt-9 flex flex-wrap justify-center gap-3">
          <PrimaryButton to="/overview" className="px-5 py-3">Open the lab</PrimaryButton>
          <GitHubLink className="surface-2 hairline press inline-flex items-center gap-2 rounded-lg border px-5 py-3 text-sm font-medium hover:border-[rgb(var(--border-strong))]">
            <GitHubMark className="h-4 w-4 transition-transform duration-300 group-hover:rotate-[-8deg] group-hover:scale-110" />
            Try it from GitHub
          </GitHubLink>
        </div>
      </Container>
    </section>
  );
}

function Footer() {
  return (
    <footer className="hairline border-t">
      <Container className="flex flex-wrap items-center justify-between gap-4 py-8">
        <div className="flex items-center gap-2.5">
          <Logo className="h-6 w-6" />
          <span className="text-[13px] font-semibold tracking-tight">Agent Labs</span>
          <span className="subtle text-2xs">Prove. Measure. Improve.</span>
        </div>
        <div className="flex items-center gap-4">
          <p className="subtle text-2xs">Open source · MIT licensed · local-first</p>
          <GitHubLink className="subtle inline-flex items-center gap-1.5 text-2xs hover:text-[rgb(var(--text))]">
            <GitHubMark className="h-3.5 w-3.5" />
            coderxtushar/Agent-Labs
          </GitHubLink>
        </div>
      </Container>
    </footer>
  );
}

export default function Home() {
  const [paused, setPaused] = useState(false);
  const toggle = () => setPaused((value) => !value);
  return (
    <div className="min-h-screen" data-motion={paused ? "paused" : "running"}>
      <TopNav />
      <main>
        <Hero paused={paused} onTogglePause={toggle} />
        <LiveNumbers />
        <HowItWorks paused={paused} onTogglePause={toggle} />
        <Principles />
        <Features />
        <RunLocally />
        <Close />
      </main>
      <Footer />
    </div>
  );
}
