import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useDockerStatus, useExperiments, useLocalModels, useResources, useRuns } from "../api/hooks";
import type { Resources } from "../api/types";
import { AXIS_TICK, useChartTheme } from "../components/charts";
import { StateBadge, formatRelative, formatTime } from "../components/domain";
import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  KeyValue,
  LoadingRows,
  LoadingTiles,
  Meter,
  Mono,
  Note,
  PageHeader,
  ProgressBar,
  Table,
  Td,
  Th,
  cx,
  formatBytes,
} from "../components/ui";

const HISTORY_LIMIT = 60;

interface Sample {
  at: string;
  time: string;
  ram: number | null;
  swap: number | null;
  cpu: number | null;
}

type Tone = "accent" | "good" | "warn" | "bad";

function percentTone(value: number | null | undefined): Tone {
  if (value === null || value === undefined) return "accent";
  if (value >= 90) return "bad";
  if (value >= 75) return "warn";
  return "accent";
}

function toSample(resources: Resources): Sample {
  return {
    at: resources.captured_at,
    time: new Date(resources.captured_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    ram: resources.memory.supported ? resources.memory.percent : null,
    swap: resources.swap.supported ? resources.swap.percent : null,
    cpu: resources.cpu.utilization_percent,
  };
}

/**
 * Keeps the last HISTORY_LIMIT snapshots seen by this page. The sample is appended
 * during render when `captured_at` changes (React's "adjust state on prop change"
 * pattern), so there is no effect and no extra commit.
 */
function useResourceHistory(resources: Resources | undefined): Sample[] {
  const [history, setHistory] = useState<{ at: string | null; samples: Sample[] }>({ at: null, samples: [] });
  if (resources && resources.captured_at !== history.at) {
    const sample = toSample(resources);
    setHistory((previous) => ({
      at: sample.at,
      samples: [...previous.samples, sample].slice(-HISTORY_LIMIT),
    }));
  }
  return history.samples;
}

function cssColor(token: string): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  return raw ? `rgb(${raw.split(/\s+/).join(", ")})` : "#888";
}

// --------------------------------------------------------------------------- //
function MeterTile({
  label,
  value,
  unit = "%",
  meter,
  tone = "accent",
  children,
  badge,
}: {
  label: string;
  value: number | null | undefined;
  unit?: string;
  meter: number | null | undefined;
  tone?: Tone;
  children?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div className="surface elevated hairline flex flex-col gap-3 rounded-xl border p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="eyebrow">{label}</p>
        {badge}
      </div>
      <p
        className={cx(
          "tnum text-[26px] font-semibold leading-none tracking-tight",
          tone === "bad" && "text-bad",
          tone === "warn" && "text-warn",
          (value === null || value === undefined) && "subtle",
        )}
      >
        {value === null || value === undefined ? "—" : `${value}${unit}`}
      </p>
      <Meter value={meter} tone={tone} label={label} />
      {children && <div className="muted space-y-0.5 text-xs">{children}</div>}
    </div>
  );
}

function UnsupportedTile({ label, reason }: { label: string; reason: string }) {
  return (
    <div className="surface elevated hairline flex flex-col gap-2 rounded-xl border p-4">
      <p className="eyebrow">{label}</p>
      <p className="subtle text-[13px] font-medium">Not supported on this platform</p>
      <p className="muted text-xs">{reason}</p>
    </div>
  );
}

function HistoryChart({
  samples,
  dataKey,
  label,
  token,
}: {
  samples: Sample[];
  dataKey: "ram" | "swap" | "cpu";
  label: string;
  token: "--accent" | "--warn" | "--info";
}) {
  // useChartTheme re-renders on theme change; SVG attributes cannot resolve var(), so read the token.
  const theme = useChartTheme();
  const color = cssColor(token);
  const latest = samples.length > 0 ? samples[samples.length - 1][dataKey] : null;
  const hasData = samples.some((sample) => sample[dataKey] !== null);
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2 px-1">
        <p className="eyebrow">{label}</p>
        <p className="tnum text-xs font-medium">{latest === null ? "—" : `${latest}%`}</p>
      </div>
      {hasData ? (
        <div className="mt-2 h-[120px]" role="img" aria-label={`${label} over the last ${samples.length} samples`}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={samples} margin={{ left: 0, right: 4, top: 4, bottom: 0 }}>
              <CartesianGrid stroke={theme.grid} strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="time" hide />
              <YAxis
                domain={[0, 100]}
                ticks={[0, 50, 100]}
                width={28}
                tick={{ ...AXIS_TICK, fill: theme.axis }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                contentStyle={theme.tooltip}
                labelStyle={{ color: theme.text }}
                formatter={(value) => [value === null || value === undefined ? "—" : `${String(value)}%`, label]}
              />
              <Area
                type="monotone"
                dataKey={dataKey}
                stroke={color}
                fill={color}
                fillOpacity={0.12}
                strokeWidth={1.5}
                connectNulls={false}
                isAnimationActive={false}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="subtle mt-2 flex h-[120px] items-center justify-center text-2xs">not available on this platform</p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
export default function SystemMonitor() {
  const resources = useResources();
  const docker = useDockerStatus();
  const ollama = useLocalModels(true);
  const experiments = useExperiments();
  const activeRuns = useRuns({ active_only: true, limit: 20 });
  const samples = useResourceHistory(resources.data);

  const res = resources.data;
  const memory = res?.memory;
  const swap = res?.swap;
  const cpu = res?.cpu;

  const memoryTight =
    (memory?.supported && (memory.pressure === "warning" || memory.pressure === "critical")) ||
    (swap?.supported && swap.percent !== null && swap.percent > 50);

  const memoryTone: Tone =
    memory?.supported && memory.pressure === "critical"
      ? "bad"
      : memory?.supported && memory.pressure === "warning"
        ? "warn"
        : percentTone(memory?.supported ? memory.percent : null);

  const runningExperiments = (experiments.data?.items ?? []).filter(
    (experiment) => experiment.state === "running" || experiment.state === "queued",
  );
  const loadedModels = (ollama.data?.models ?? []).filter((model) => model.running);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="System"
        title="System monitor"
        description="Cheap read-only snapshots of this machine, Docker and Ollama. Nothing on this page starts, stops or changes anything."
        meta={
          res && (
            <>
              <Badge tone="neutral">
                {res.platform.system} {res.platform.release} · {res.platform.machine}
              </Badge>
              <Badge tone="neutral">{cpu?.cores} cores</Badge>
              <span className="subtle text-2xs" title={formatTime(res.captured_at)}>
                sampled {formatRelative(res.captured_at)}
              </span>
            </>
          )
        }
      />

      {memoryTight && (
        <div role="status" className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
          <Note tone="warn">
            Memory is tight
            {memory?.supported && memory.pressure && memory.pressure !== "normal" ? ` (pressure: ${memory.pressure})` : ""}
            {swap?.supported && swap.percent !== null && swap.percent > 50 ? ` and swap is ${swap.percent}% used` : ""}
            ; live-run timings may be inflated by swapping.
          </Note>
        </div>
      )}

      {resources.isError && <ErrorState error={resources.error} onRetry={() => void resources.refetch()} />}
      {resources.isLoading && <LoadingTiles count={4} />}

      {res && memory && swap && cpu && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {memory.supported ? (
            <MeterTile
              label="Memory"
              value={memory.percent}
              meter={memory.percent}
              tone={memoryTone}
              badge={
                memory.pressure ? (
                  <Badge tone={memory.pressure === "normal" ? "good" : memory.pressure === "warning" ? "warn" : "bad"}>
                    pressure {memory.pressure}
                  </Badge>
                ) : (
                  <Badge tone="neutral" title="Memory pressure is reported on macOS only">
                    pressure n/a
                  </Badge>
                )
              }
            >
              <p className="tnum">
                {formatBytes(memory.used_bytes)} of {formatBytes(memory.total_bytes)} used
              </p>
              <p className="tnum">{formatBytes(memory.available_bytes)} available</p>
              {(memory.compressed_bytes !== undefined || memory.wired_bytes !== undefined) && (
                <p className="tnum subtle">
                  {memory.compressed_bytes !== undefined && `compressed ${formatBytes(memory.compressed_bytes)}`}
                  {memory.compressed_bytes !== undefined && memory.wired_bytes !== undefined && " · "}
                  {memory.wired_bytes !== undefined && `wired ${formatBytes(memory.wired_bytes)}`}
                </p>
              )}
            </MeterTile>
          ) : (
            <UnsupportedTile label="Memory" reason={memory.reason} />
          )}

          {swap.supported ? (
            <MeterTile
              label="Swap"
              value={swap.percent}
              meter={swap.percent}
              tone={swap.percent !== null && swap.percent > 50 ? "warn" : "accent"}
            >
              <p className="tnum">
                {formatBytes(swap.used_bytes)} of {formatBytes(swap.total_bytes)} used
              </p>
              {swap.total_bytes === 0 && <p className="subtle">no swap allocated</p>}
            </MeterTile>
          ) : (
            <UnsupportedTile label="Swap" reason={swap.reason} />
          )}

          <MeterTile
            label="CPU utilization"
            value={cpu.utilization_percent}
            meter={cpu.utilization_percent}
            tone={percentTone(cpu.utilization_percent)}
          >
            {cpu.utilization_percent === null && <p className="subtle">Not supported on this platform</p>}
            <p className="subtle">{cpu.utilization_note}</p>
          </MeterTile>

          {cpu.load_average ? (
            <MeterTile
              label="Load average"
              value={cpu.load_per_core}
              unit=" / core"
              meter={cpu.load_per_core === null ? null : Math.min(100, cpu.load_per_core * 100)}
              tone={cpu.load_per_core !== null && cpu.load_per_core >= 1 ? "warn" : "accent"}
            >
              <p className="tnum">
                1m {cpu.load_average[0]?.toFixed(2) ?? "—"} · 5m {cpu.load_average[1]?.toFixed(2) ?? "—"} · 15m{" "}
                {cpu.load_average[2]?.toFixed(2) ?? "—"}
              </p>
              <p className="subtle">1-minute load divided by {cpu.cores} cores; 1.0 means saturated</p>
            </MeterTile>
          ) : (
            <UnsupportedTile label="Load average" reason="os.getloadavg() is unavailable on this host" />
          )}
        </div>
      )}

      <Card
        title="Recent history"
        subtitle="Sampled by this page every 5 s while it is open; not recorded per run."
        actions={<span className="subtle tnum text-2xs">{samples.length}/{HISTORY_LIMIT} samples</span>}
      >
        {samples.length < 2 ? (
          <p className="muted p-4 text-xs">Collecting samples; the charts fill in while this page stays open.</p>
        ) : (
          <div className="grid gap-6 p-4 sm:grid-cols-3">
            <HistoryChart samples={samples} dataKey="ram" label="RAM %" token="--accent" />
            <HistoryChart samples={samples} dataKey="swap" label="Swap %" token="--warn" />
            <HistoryChart samples={samples} dataKey="cpu" label="CPU %" token="--info" />
          </div>
        )}
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card
          title="Docker"
          subtitle="Polled every 10 s"
          actions={
            docker.data && (
              <Badge tone={docker.data.available ? "good" : "bad"}>{docker.data.available ? "available" : "unavailable"}</Badge>
            )
          }
        >
          {docker.isError && (
            <div className="p-4">
              <ErrorState error={docker.error} onRetry={() => void docker.refetch()} />
            </div>
          )}
          {docker.isLoading && <LoadingRows rows={3} label="Loading Docker status" />}
          {docker.data && (
            <div className="p-4">
              {docker.data.available ? (
                <KeyValue
                  rows={[
                    ["Server version", docker.data.server_version ?? "—"],
                    ["CPUs", docker.data.cpus ?? "—"],
                    ["Docker VM memory", formatBytes(docker.data.memory_bytes)],
                    ["Containers running", docker.data.containers_running ?? "—"],
                    ["Images", docker.data.images ?? "—"],
                    ["Detail", <span key="detail" className="muted">{docker.data.detail}</span>],
                  ]}
                />
              ) : (
                <p className="text-warn text-xs">{docker.data.detail}</p>
              )}
            </div>
          )}
        </Card>

        <Card
          title="Ollama"
          subtitle="Polled every 15 s"
          actions={
            <>
              {ollama.data && (
                <Badge tone={ollama.data.reachable ? "good" : "bad"}>{ollama.data.reachable ? "reachable" : "unreachable"}</Badge>
              )}
              <Link to="/models" className="link text-2xs">
                Local models
              </Link>
            </>
          }
        >
          {ollama.isError && (
            <div className="p-4">
              <ErrorState error={ollama.error} onRetry={() => void ollama.refetch()} />
            </div>
          )}
          {ollama.isLoading && <LoadingRows rows={3} label="Loading Ollama status" />}
          {ollama.data && !ollama.data.reachable && (
            <div className="space-y-1 p-4 text-xs">
              <p className="text-warn">{ollama.data.detail}</p>
              <p className="muted">
                <Mono>{ollama.data.base_url}</Mono>
              </p>
            </div>
          )}
          {ollama.data && ollama.data.reachable && (
            <>
              <div className="p-4">
                <KeyValue
                  rows={[
                    ["Version", ollama.data.version ?? "—"],
                    ["Base URL", <Mono key="base">{ollama.data.base_url}</Mono>],
                    ["Installed", ollama.data.models.length],
                    ["Loaded", ollama.data.running_count],
                  ]}
                />
              </div>
              {loadedModels.length === 0 ? (
                <p className="muted hairline border-t px-4 py-3 text-xs">No model is loaded right now.</p>
              ) : (
                <Table>
                  <thead>
                    <tr>
                      <Th>Loaded model</Th>
                      <Th className="text-right">VRAM</Th>
                      <Th className="text-right">Context</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {loadedModels.map((model) => (
                      <tr key={model.name}>
                        <Td>
                          <Mono>{model.name}</Mono>
                        </Td>
                        <Td className="tnum text-right">{formatBytes(model.loaded?.vram_bytes)}</Td>
                        <Td className="tnum text-right">
                          {model.loaded?.context_length != null ? model.loaded.context_length.toLocaleString() : "—"}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </>
          )}
        </Card>
      </div>

      <Card
        title="Sandbox containers"
        subtitle="Resource usage of Agent Labs sandboxes right now, from docker stats"
        actions={docker.data && <span className="subtle tnum text-2xs">{docker.data.sandboxes.length} running</span>}
      >
        {docker.data && docker.data.sandboxes.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <Th>Container</Th>
                <Th>Image</Th>
                <Th>Status</Th>
                <Th>Run</Th>
                <Th className="text-right">CPU</Th>
                <Th className="text-right">Memory</Th>
                <Th className="text-right">Mem %</Th>
              </tr>
            </thead>
            <tbody>
              {docker.data.sandboxes.map((sandbox) => (
                <tr key={sandbox.id} className="row-hover">
                  <Td>
                    <Mono>{sandbox.id.slice(0, 12)}</Mono>
                    {sandbox.name && <p className="subtle mt-0.5 font-mono text-2xs">{sandbox.name}</p>}
                  </Td>
                  <Td>
                    <Mono>{sandbox.image ?? "—"}</Mono>
                  </Td>
                  <Td className="muted">{sandbox.status ?? "—"}</Td>
                  <Td>
                    {sandbox.run_id ? (
                      <Link to={`/runs/${sandbox.run_id}`} className="link font-mono text-2xs">
                        {sandbox.run_id.slice(0, 10)}
                      </Link>
                    ) : (
                      <span className="subtle">—</span>
                    )}
                  </Td>
                  <Td className="tnum text-right">{sandbox.cpu_percent ?? "—"}</Td>
                  <Td className="tnum text-right">{sandbox.memory_usage ?? "—"}</Td>
                  <Td className="tnum text-right">{sandbox.memory_percent ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <div className="p-4">
            <EmptyState
              title="No Agent Labs sandboxes running"
              description={
                docker.data && !docker.data.available
                  ? "Docker is unavailable, so container stats cannot be read."
                  : "Containers appear here while runs execute."
              }
            />
          </div>
        )}
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card
          title="Active runs"
          actions={activeRuns.data && <span className="subtle tnum text-2xs">{activeRuns.data.pagination.total}</span>}
        >
          {activeRuns.isError && (
            <div className="p-4">
              <ErrorState error={activeRuns.error} onRetry={() => void activeRuns.refetch()} />
            </div>
          )}
          {activeRuns.isLoading && <LoadingRows rows={3} label="Loading active runs" />}
          {activeRuns.data && activeRuns.data.items.length === 0 && (
            <p className="muted p-4 text-xs">No runs in flight.</p>
          )}
          {activeRuns.data && activeRuns.data.items.length > 0 && (
            <Table>
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>State</Th>
                  <Th>Agent</Th>
                  <Th>Task</Th>
                  <Th>Started</Th>
                </tr>
              </thead>
              <tbody>
                {activeRuns.data.items.map((run) => (
                  <tr key={run.id} className="row-hover">
                    <Td>
                      <Link to={`/runs/${run.id}`} className="link font-mono text-2xs">
                        {run.id.slice(0, 10)}
                      </Link>
                    </Td>
                    <Td>
                      <StateBadge state={run.state} />
                    </Td>
                    <Td>
                      <Mono>{run.agent.label}</Mono>
                    </Td>
                    <Td>
                      <Mono>{run.task_key}</Mono>
                    </Td>
                    <Td className="muted text-2xs" title={formatTime(run.started_at)}>
                      {formatRelative(run.started_at)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        <Card title="Running and queued experiments">
          {experiments.isError && (
            <div className="p-4">
              <ErrorState error={experiments.error} onRetry={() => void experiments.refetch()} />
            </div>
          )}
          {experiments.isLoading && <LoadingRows rows={3} label="Loading experiments" />}
          {experiments.data && runningExperiments.length === 0 && (
            <p className="muted p-4 text-xs">No experiment is running or queued.</p>
          )}
          {runningExperiments.length > 0 && (
            <ul className="divide-y divide-[rgb(var(--border))]">
              {runningExperiments.map((experiment) => (
                <li key={experiment.id} className="space-y-2 px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Link to={`/experiments/${experiment.id}`} className="link min-w-0 truncate text-xs font-medium">
                      {experiment.name}
                    </Link>
                    <Badge tone={experiment.state === "running" ? "info" : "neutral"}>{experiment.state}</Badge>
                  </div>
                  <p className="subtle font-mono text-2xs">{experiment.agent.label}</p>
                  {experiment.progress && (
                    <div>
                      <ProgressBar percent={experiment.progress.percent} />
                      <p className="tnum muted mt-1 text-2xs">
                        {experiment.progress.finished}/{experiment.progress.total} finished · {experiment.progress.active} in
                        flight · {experiment.progress.pending} pending
                      </p>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
