import { useState } from "react";

import {
  useAdapters,
  useAgents,
  useCreateAgent,
  useDeleteAgent,
  useSetAgentEnabled,
} from "../api/hooks";
import type { AdapterCapability, Agent } from "../api/types";
import { SimulatedBadge, formatTime } from "../components/domain";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  Field,
  JsonBlock,
  LoadingRows,
  Modal,
  Mono,
  Table,
  Td,
  Th,
  inputClass,
} from "../components/ui";

function ReadinessRow({ agent }: { agent: Agent }) {
  const { ready, detail, missing } = agent.readiness;
  return (
    <div className="min-w-0">
      <Badge tone={ready ? "good" : "warn"}>{ready ? "ready" : "not ready"}</Badge>
      <p className="muted mt-1 break-words text-2xs">{detail}</p>
      {missing.length > 0 && (
        <p className="mt-0.5 text-2xs text-amber-400">
          missing: {missing.map((item) => <Mono key={item}>{item} </Mono>)}
        </p>
      )}
    </div>
  );
}

function CapabilityMatrix({ adapters }: { adapters: AdapterCapability[] }) {
  const flags: { key: keyof AdapterCapability; label: string }[] = [
    { key: "is_simulated", label: "Simulated" },
    { key: "requires_network", label: "Needs network" },
    { key: "reports_token_usage", label: "Reports tokens" },
    { key: "reports_cost", label: "Reports cost" },
    { key: "supports_cancellation", label: "Cancellable" },
    { key: "streams_events", label: "Streams events" },
  ];

  return (
    <Table>
      <thead>
        <tr>
          <Th>Adapter</Th>
          {flags.map((flag) => (
            <Th key={flag.key} className="text-center">
              {flag.label}
            </Th>
          ))}
          <Th>Credentials</Th>
        </tr>
      </thead>
      <tbody>
        {adapters.map((adapter) => (
          <tr key={adapter.key}>
            <Td>
              <Mono className="font-semibold">{adapter.key}</Mono>
              <p className="muted mt-0.5 max-w-md text-2xs">{adapter.description}</p>
            </Td>
            {flags.map((flag) => (
              <Td key={flag.key} className="text-center">
                {adapter[flag.key] ? (
                  <span className="text-emerald-400" aria-label="yes">
                    ✓
                  </span>
                ) : (
                  <span className="muted" aria-label="no">
                    ·
                  </span>
                )}
              </Td>
            ))}
            <Td>
              {adapter.requires_credentials.length === 0 ? (
                <span className="muted text-2xs">none</span>
              ) : (
                adapter.requires_credentials.map((credential) => (
                  <Mono key={credential} className="block">
                    {credential}
                  </Mono>
                ))
              )}
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function CreateAgentModal({
  adapters,
  onClose,
}: {
  adapters: AdapterCapability[];
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0");
  const [adapter, setAdapter] = useState(adapters[0]?.key ?? "mock");
  const [model, setModel] = useState("");
  const [description, setDescription] = useState("");
  const [configText, setConfigText] = useState('{\n  "behaviour": "noop"\n}');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const create = useCreateAgent();

  const selected = adapters.find((entry) => entry.key === adapter);

  const submit = () => {
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(configText) as Record<string, unknown>;
    } catch (error) {
      setJsonError(error instanceof Error ? error.message : "invalid JSON");
      return;
    }
    setJsonError(null);
    create.mutate(
      { name, version, adapter, description, model: model || null, config },
      { onSuccess: onClose },
    );
  };

  return (
    <Modal open onClose={onClose} title="Register agent configuration" wide>
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Name">
            <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field
            label="Version"
            hint="Bump this instead of editing an existing config, so past results stay interpretable."
          >
            <input
              className={inputClass}
              value={version}
              onChange={(event) => setVersion(event.target.value)}
            />
          </Field>
          <Field label="Adapter">
            <select
              className={inputClass}
              value={adapter}
              onChange={(event) => setAdapter(event.target.value)}
            >
              {adapters.map((entry) => (
                <option key={entry.key} value={entry.key}>
                  {entry.key} {entry.is_simulated ? "(simulated)" : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Model" hint="Identifier recorded on every run. Blank for non-model adapters.">
            <input className={inputClass} value={model} onChange={(event) => setModel(event.target.value)} />
          </Field>
        </div>

        <Field label="Description">
          <input
            className={inputClass}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>

        {selected && (
          <div className="surface-2 hairline rounded-md border p-3">
            <p className="muted text-2xs font-semibold uppercase tracking-wide">
              {selected.key} config schema
            </p>
            <JsonBlock value={selected.config_schema} maxHeight="max-h-40" />
            {selected.requires_credentials.length > 0 && (
              <p className="mt-2 text-2xs text-amber-400">
                Requires host environment variables: {selected.requires_credentials.join(", ")}.
                Secrets are never stored in the agent configuration.
              </p>
            )}
          </div>
        )}

        <Field label="Adapter config (JSON)">
          <textarea
            rows={8}
            spellCheck={false}
            className={`${inputClass} font-mono`}
            value={configText}
            onChange={(event) => setConfigText(event.target.value)}
          />
        </Field>

        {jsonError && (
          <p className="text-xs text-rose-400">Config is not valid JSON: {jsonError}</p>
        )}
        {create.isError && <ErrorState error={create.error} />}

        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!name.trim() || create.isPending} onClick={submit}>
            {create.isPending ? "Registering…" : "Register agent"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export default function Agents() {
  const agents = useAgents();
  const adapters = useAdapters();
  const setEnabled = useSetAgentEnabled();
  const remove = useDeleteAgent();
  const [creating, setCreating] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Agents</h1>
        <Button
          variant="primary"
          disabled={!adapters.data}
          onClick={() => setCreating(true)}
        >
          Register agent
        </Button>
      </div>

      {(setEnabled.isError || remove.isError) && (
        <ErrorState error={setEnabled.error ?? remove.error} />
      )}

      <Card
        title="Configurations"
        subtitle={agents.data ? `${agents.data.pagination.total} registered` : undefined}
      >
        {agents.isError && (
          <div className="p-4">
            <ErrorState error={agents.error} onRetry={() => void agents.refetch()} />
          </div>
        )}
        {agents.isLoading && <LoadingRows rows={4} label="Loading agents" />}
        {agents.data && agents.data.items.length === 0 && (
          <p className="muted p-4 text-xs">
            No agents registered. Run <Mono>agentbench bootstrap</Mono> to load the bundled demo
            agents, or register one above.
          </p>
        )}
        {agents.data && agents.data.items.length > 0 && (
          <Table>
            <thead>
              <tr>
                <Th>Agent</Th>
                <Th>Adapter</Th>
                <Th>Model</Th>
                <Th>Readiness</Th>
                <Th>Versions</Th>
                <Th>Created</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {agents.data.items.map((agent) => (
                <tr key={agent.id} className="hover:surface-2">
                  <Td>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Mono className="font-semibold">{agent.label}</Mono>
                      {agent.is_simulated && <SimulatedBadge compact />}
                      {!agent.enabled && <Badge tone="warn">disabled</Badge>}
                    </div>
                    {agent.description && (
                      <p className="muted mt-1 max-w-xl text-2xs">{agent.description}</p>
                    )}
                  </Td>
                  <Td>
                    <Mono>{agent.adapter}</Mono>
                  </Td>
                  <Td>
                    <Mono className={agent.model ? undefined : "muted"}>{agent.model ?? "—"}</Mono>
                  </Td>
                  <Td>
                    <ReadinessRow agent={agent} />
                  </Td>
                  <Td className="muted text-2xs">
                    prompt {agent.prompt_version} · tools {agent.tools_version}
                  </Td>
                  <Td className="muted text-2xs">{formatTime(agent.created_at)}</Td>
                  <Td>
                    <div className="flex flex-col gap-1">
                      <Button
                        onClick={() => setEnabled.mutate({ id: agent.id, enabled: !agent.enabled })}
                        disabled={setEnabled.isPending}
                      >
                        {agent.enabled ? "Disable" : "Enable"}
                      </Button>
                      <Button
                        variant="danger"
                        onClick={() => remove.mutate(agent.id)}
                        disabled={remove.isPending}
                        title="Only possible when no experiment references this agent"
                      >
                        Delete
                      </Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card
        title="Adapter capabilities"
        subtitle="Declared by each adapter and checked at run time"
      >
        {adapters.isError && (
          <div className="p-4">
            <ErrorState error={adapters.error} onRetry={() => void adapters.refetch()} />
          </div>
        )}
        {adapters.isLoading && <LoadingRows rows={4} label="Loading adapters" />}
        {adapters.data && <CapabilityMatrix adapters={adapters.data.adapters} />}
      </Card>

      {creating && adapters.data && (
        <CreateAgentModal adapters={adapters.data.adapters} onClose={() => setCreating(false)} />
      )}
    </div>
  );
}
