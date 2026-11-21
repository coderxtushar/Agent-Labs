import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useLocalModels } from "../api/hooks";
import type { LocalModelAgent, LocalModels as LocalModelsData, ProvenanceValue } from "../api/types";
import { RateDisplay, formatDuration, formatRelative, formatTime } from "../components/domain";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  KeyValue,
  LoadingRows,
  LoadingTiles,
  Mono,
  Note,
  PageHeader,
  SearchInput,
  Segmented,
  StatTile,
  Table,
  Td,
  Th,
  cx,
  formatBytes,
  type BadgeTone,
} from "../components/ui";

type Model = LocalModelsData["models"][number];
type Filter = "all" | "loaded" | "used" | "unused";

// --------------------------------------------------------------------------- //
// local helpers

/** Ollama timestamps carry nanoseconds; trim to milliseconds so every engine parses them. */
function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const trimmed = iso.replace(/(\.\d{3})\d+/, "$1");
  const value = new Date(trimmed).getTime();
  return Number.isNaN(value) ? null : value;
}

function normalisedIso(iso: string | null | undefined): string | null {
  const value = parseTime(iso);
  return value === null ? null : new Date(value).toISOString();
}

function formatUntil(iso: string | null | undefined): string {
  const then = parseTime(iso);
  if (then === null) return "—";
  const seconds = Math.round((then - Date.now()) / 1000);
  if (seconds <= 0) return "unloading now";
  if (seconds > 365 * 86400) return "not scheduled to unload";
  if (seconds < 60) return `in ${seconds}s`;
  if (seconds < 3600) return `in ${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `in ${Math.floor(seconds / 3600)}h`;
  return `in ${Math.floor(seconds / 86400)}d`;
}

const PROVENANCE_CHIP: Record<ProvenanceValue, { label: string; tone: BadgeTone; model: boolean }> = {
  live_model: { label: "live model", tone: "good", model: true },
  external_agent: { label: "external agent", tone: "info", model: true },
  simulated: { label: "simulated", tone: "warn", model: false },
  offline_adapter_test: { label: "offline adapter test", tone: "warn", model: false },
};

function ProvenanceChip({ value }: { value: ProvenanceValue }) {
  const meta = PROVENANCE_CHIP[value] ?? { label: value, tone: "neutral" as BadgeTone, model: false };
  return (
    <Badge tone={meta.tone} title={meta.model ? undefined : "Pipeline check, not a model benchmark result"}>
      {meta.label}
      {!meta.model && <span className="opacity-70">· not a model score</span>}
    </Badge>
  );
}

function CopyCommand({ command }: { command: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");

  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(command);
      setStatus("copied");
    } catch {
      setStatus("failed");
    }
    window.setTimeout(() => setStatus("idle"), 2000);
  };

  return (
    <div className="surface-2 hairline flex min-w-0 items-center gap-2 rounded-md border py-1 pl-2.5 pr-1">
      <span className="subtle select-none font-mono text-2xs" aria-hidden>
        $
      </span>
      <code className="min-w-0 flex-1 select-all truncate font-mono text-2xs" title={command}>
        {command}
      </code>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={`Copy command: ${command}`}
        className={cx(
          "shrink-0 rounded px-1.5 py-0.5 text-2xs font-medium transition-colors duration-150",
          status === "copied" ? "text-good" : status === "failed" ? "text-warn" : "muted hover:text-[rgb(var(--text))]",
        )}
      >
        {status === "copied" ? "Copied" : status === "failed" ? "Select and copy" : "Copy"}
      </button>
    </div>
  );
}

function CommandList({ commands, label = "Run this yourself in a terminal" }: { commands: string[]; label?: string }) {
  return (
    <div className="space-y-1.5">
      <p className="eyebrow">{label}</p>
      <div className="grid gap-1.5 sm:grid-cols-2">
        {commands.map((command) => (
          <CopyCommand key={command} command={command} />
        ))}
      </div>
    </div>
  );
}

function configValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

// --------------------------------------------------------------------------- //
function AgentUsage({ agent }: { agent: LocalModelAgent }) {
  const entries = Object.entries(agent.config).filter(([key]) => key !== "model");
  return (
    <div className="hairline rounded-lg border">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <Link to={`/leaderboard/${agent.agent_config_id}`} className="link font-mono text-2xs font-semibold">
          {agent.label}
        </Link>
        <Badge tone={agent.enabled ? "good" : "neutral"}>{agent.enabled ? "enabled" : "disabled"}</Badge>
      </div>
      {entries.length > 0 && (
        <dl className="hairline flex flex-wrap gap-x-5 gap-y-1 border-t px-3 py-2">
          {entries.map(([key, value]) => (
            <div key={key} className="flex min-w-0 items-baseline gap-1.5">
              <dt className="subtle text-2xs">{key}</dt>
              <dd className="tnum min-w-0 break-all font-mono text-2xs">{configValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="hairline border-t">
        {agent.results.length === 0 ? (
          <p className="subtle px-3 py-2 text-2xs">No finished benchmark runs recorded for this config.</p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Provenance</Th>
                <Th>Success rate</Th>
                <Th className="text-right">Attributable runs</Th>
                <Th className="text-right">Experiments</Th>
                <Th className="text-right">Median runtime</Th>
              </tr>
            </thead>
            <tbody>
              {agent.results.map((result) => (
                <tr key={result.provenance}>
                  <Td>
                    <ProvenanceChip value={result.provenance} />
                  </Td>
                  <Td>
                    <RateDisplay rate={result.success_rate} size="sm" />
                  </Td>
                  <Td className="tnum text-right">{result.attributable_runs}</Td>
                  <Td className="tnum text-right">{result.experiment_count}</Td>
                  <Td className="tnum text-right">{formatDuration(result.runtime.median_s)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </div>
    </div>
  );
}

function ModelCard({ model }: { model: Model }) {
  const modified = normalisedIso(model.modified_at);
  const commands = [
    ...(model.running ? [`ollama stop ${model.name}`] : []),
    `ollama pull ${model.name}`,
    `ollama rm ${model.name}`,
    `ollama show ${model.name}`,
  ];

  return (
    <Card
      className="fade-in"
      title={<span className="font-mono">{model.name}</span>}
      subtitle={
        <span className="flex flex-wrap items-center gap-x-1.5">
          {[model.family, model.parameter_size, model.quantization].filter(Boolean).join(" · ") || "details not reported"}
        </span>
      }
      actions={
        model.running ? (
          <Badge tone="info">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
            loaded
          </Badge>
        ) : (
          <Badge tone="neutral">not loaded</Badge>
        )
      }
    >
      <div className="space-y-4 p-4">
        <KeyValue
          rows={[
            ["Parent model", <Mono key="parent">{model.parent_model ?? "—"}</Mono>],
            ["Family", model.family ?? "—"],
            ["Parameter size", model.parameter_size ?? "—"],
            ["Quantization", model.quantization ?? "—"],
            ["Size on disk", formatBytes(model.size_bytes)],
            [
              "Max context",
              model.max_context_length !== null ? `${model.max_context_length.toLocaleString()} tokens` : "not reported",
            ],
            ["Format", model.format ?? "—"],
            [
              "Modified",
              <span key="modified" title={formatTime(modified)}>
                {formatRelative(modified)}
              </span>,
            ],
            ["Digest", <Mono key="digest">{model.digest ?? "—"}</Mono>],
          ]}
        />

        <div>
          <p className="eyebrow">Capabilities</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {model.capabilities && model.capabilities.length > 0 ? (
              model.capabilities.map((capability) => (
                <Badge key={capability} tone="neutral">
                  {capability}
                </Badge>
              ))
            ) : (
              <span className="subtle text-2xs">not reported by this Ollama version</span>
            )}
          </div>
        </div>

        {model.loaded && (
          <div className="surface-2 hairline rounded-lg border p-3">
            <p className="eyebrow mb-2">Loaded now</p>
            <KeyValue
              rows={[
                ["VRAM", formatBytes(model.loaded.vram_bytes)],
                ["Loaded size", formatBytes(model.loaded.size_bytes)],
                [
                  "Context length",
                  model.loaded.context_length !== null
                    ? `${model.loaded.context_length.toLocaleString()} tokens`
                    : "not reported",
                ],
                [
                  "Unloads",
                  <span key="expires" title={formatTime(normalisedIso(model.loaded.expires_at))}>
                    {formatUntil(model.loaded.expires_at)}
                  </span>,
                ],
              ]}
            />
          </div>
        )}

        <div className="space-y-2">
          <p className="eyebrow">Agent configs using this model</p>
          {model.agents.length === 0 ? (
            <p className="subtle text-xs">Not used by any agent config.</p>
          ) : (
            model.agents.map((agent) => <AgentUsage key={agent.agent_config_id} agent={agent} />)
          )}
        </div>

        <details className="group">
          <summary className="muted cursor-pointer select-none text-xs hover:text-[rgb(var(--text))]">
            CLI commands for this model
          </summary>
          <div className="mt-2">
            <CommandList commands={commands} />
          </div>
        </details>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
export default function LocalModels() {
  const [live, setLive] = useState(false);
  const query = useLocalModels(live);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const data = query.data;
  const models = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data?.models ?? []).filter((model) => {
      if (filter === "loaded" && !model.running) return false;
      if (filter === "used" && model.agents.length === 0) return false;
      if (filter === "unused" && model.agents.length > 0) return false;
      if (!needle) return true;
      return [model.name, model.family, model.parent_model, model.parameter_size]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });
  }, [data, search, filter]);

  const totals = useMemo(() => {
    const all = data?.models ?? [];
    const known = all.filter((model) => model.size_bytes !== null);
    return {
      count: all.length,
      bytes: known.reduce((sum, model) => sum + (model.size_bytes ?? 0), 0),
      unknownSizes: all.length - known.length,
      used: all.filter((model) => model.agents.length > 0).length,
      vram: all.reduce((sum, model) => sum + (model.loaded?.vram_bytes ?? 0), 0),
      vramKnown: all.some((model) => model.loaded?.vram_bytes !== null && model.loaded?.vram_bytes !== undefined),
    };
  }, [data]);

  const missing = data?.agents_missing_model ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="System"
        title="Local models"
        description="Models installed in your local Ollama, which agent configs use them, and what those configs have scored. Read-only."
        meta={
          data && (
            <>
              <Badge tone={data.reachable ? "good" : "bad"}>
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                Ollama {data.reachable ? "reachable" : "unreachable"}
              </Badge>
              {data.version && <Badge tone="neutral">v{data.version}</Badge>}
              <Mono className="subtle">{data.base_url}</Mono>
            </>
          )
        }
        actions={
          <>
            <Segmented
              label="Auto-refresh"
              value={live ? "live" : "manual"}
              onChange={(value) => setLive(value === "live")}
              options={[
                { value: "manual", label: "Manual" },
                { value: "live", label: "Every 15 s", title: "Poll Ollama's read-only endpoints every 15 seconds" },
              ]}
            />
            <Button onClick={() => void query.refetch()} disabled={query.isFetching} title="Re-read /api/tags and /api/ps">
              {query.isFetching ? "Refreshing…" : "Refresh"}
            </Button>
          </>
        }
      />

      <Card>
        <div className="space-y-3 p-4">
          <p className="text-[13px] font-medium">Agent Labs never downloads, removes, or modifies models.</p>
          <Note>
            {data?.read_only_note ??
              "Agent Labs only reads Ollama. Pull, remove or stop models yourself with the ollama CLI."}{" "}
            The commands on this page are copy-only.
          </Note>
          <Note>Ollama cloud models are billed per token; Agent Labs is designed around free local models.</Note>
        </div>
      </Card>

      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.isLoading && <LoadingTiles count={4} />}

      {data && !data.reachable && (
        <Card title="Ollama is not reachable" subtitle={<Mono>{data.base_url}</Mono>}>
          <div className="space-y-3 p-4">
            <p className="text-warn text-xs">{data.detail}</p>
            <CommandList commands={["ollama serve", "brew services start ollama"]} label="Start it yourself in a terminal" />
            <Note>Installed models cannot be listed until the server responds. Live agent runs will fail with a connection error.</Note>
          </div>
        </Card>
      )}

      {data && data.reachable && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile label="Installed" value={totals.count} hint="from GET /api/tags" />
            <StatTile
              label="Loaded now"
              value={data.running_count}
              hint={totals.vramKnown ? `${formatBytes(totals.vram)} VRAM in use` : "from GET /api/ps"}
            />
            <StatTile
              label="Disk used"
              value={formatBytes(totals.bytes)}
              hint={totals.unknownSizes > 0 ? `${totals.unknownSizes} model sizes not reported` : "sum of installed models"}
            />
            <StatTile
              label="Used by agent configs"
              value={`${totals.used}/${totals.count}`}
              hint="models referenced by at least one config"
            />
          </div>

          {missing.length > 0 && (
            <Card
              title="Agent configs reference models that are not installed"
              subtitle="Runs with these configs will fail until the model is pulled."
              className="border-amber-500/30"
            >
              <Table>
                <thead>
                  <tr>
                    <Th>Agent config</Th>
                    <Th>Model</Th>
                    <Th>Status</Th>
                    <Th>Run this yourself in a terminal</Th>
                  </tr>
                </thead>
                <tbody>
                  {missing.map((agent) => (
                    <tr key={agent.agent_config_id}>
                      <Td>
                        <Link to={`/leaderboard/${agent.agent_config_id}`} className="link font-mono text-2xs">
                          {agent.label}
                        </Link>
                      </Td>
                      <Td>
                        <Mono className="text-warn">{agent.model}</Mono>
                      </Td>
                      <Td>
                        <Badge tone={agent.enabled ? "warn" : "neutral"}>{agent.enabled ? "enabled" : "disabled"}</Badge>
                      </Td>
                      <Td className="min-w-[220px]">
                        <CopyCommand command={`ollama pull ${agent.model}`} />
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          )}

          <section className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-[13px] font-semibold tracking-tight">
                Installed models <span className="subtle tnum ml-1 font-normal">{models.length}</span>
              </h2>
              <div className="flex flex-wrap items-center gap-2">
                <Segmented<Filter>
                  label="Filter models"
                  value={filter}
                  onChange={setFilter}
                  options={[
                    { value: "all", label: "All" },
                    { value: "loaded", label: "Loaded" },
                    { value: "used", label: "In use" },
                    { value: "unused", label: "Unused" },
                  ]}
                />
                <SearchInput value={search} onChange={setSearch} placeholder="Filter by name" label="Filter models by name" className="w-48" />
              </div>
            </div>

            {data.models.length === 0 ? (
              <EmptyState
                title="No models installed"
                description={
                  <div className="space-y-3">
                    <p>Ollama is running but reports no installed models.</p>
                    <CommandList commands={["ollama pull <model>"]} />
                  </div>
                }
              />
            ) : models.length === 0 ? (
              <EmptyState title="No models match" description="Clear the search or filter to see every installed model." />
            ) : (
              <div className="grid gap-4 2xl:grid-cols-2">
                {models.map((model) => (
                  <ModelCard key={model.name} model={model} />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {query.isLoading && <LoadingRows rows={4} label="Loading models" />}
    </div>
  );
}
