import { useCapabilities, useSettings } from "../api/hooks";
import { OutcomeBadge } from "../components/domain";
import {
  Badge,
  Card,
  ErrorState,
  KeyValue,
  LoadingTiles,
  Mono,
  Table,
  Td,
  Th,
} from "../components/ui";

export default function Settings() {
  const settings = useSettings();
  const capabilities = useCapabilities();

  if (settings.isError) {
    return <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />;
  }
  if (settings.isLoading || !settings.data) return <LoadingTiles count={4} />;

  const data = settings.data;
  const unsafeSandbox = data.execution.sandbox_provider === "local_unsafe";

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Settings</h1>
      <p className="muted text-xs">
        Read-only. Every value comes from the server's effective configuration. Change these with
        environment variables and restart; see <Mono>.env.example</Mono>.
      </p>

      {unsafeSandbox && (
        <div role="alert" className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 text-xs">
          <p className="font-semibold text-rose-400">
            The host-local sandbox provider is active. This is not a sandbox.
          </p>
          <p className="mt-1 text-rose-200/90">
            Task commands run as ordinary processes on the host with no filesystem confinement, no
            network restriction and no resource limits. Use it only for repositories you wrote
            yourself.
          </p>
        </div>
      )}

      {!data.api.auth_required && (
        <div role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
          <p className="font-semibold text-amber-400">API authentication is disabled.</p>
          <p className="mt-1 text-amber-200/90">
            This service starts containers and executes untrusted code. Set{" "}
            <Mono>AGENTBENCH_API_TOKEN</Mono> for any deployment that is not loopback-only.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Execution">
          <div className="p-4">
            <KeyValue
              rows={[
                ["Sandbox provider", <Mono>{data.execution.sandbox_provider}</Mono>],
                [
                  "Host-local provider allowed",
                  <Badge tone={data.execution.local_unsafe_sandbox_allowed ? "bad" : "good"}>
                    {String(data.execution.local_unsafe_sandbox_allowed)}
                  </Badge>,
                ],
                ["Default image", <Mono>{data.execution.default_image}</Mono>],
                ["Max concurrent runs", data.execution.max_concurrent_runs],
                ["Infra retry limit", data.execution.infra_retry_limit],
                ["Run lease", `${data.execution.run_lease_seconds}s`],
                [
                  "Worker",
                  data.execution.worker_in_api_process ? "in API process" : "separate process",
                ],
              ]}
            />
          </div>
        </Card>

        <Card title="Hard limits" subtitle="A task may request less, never more">
          <div className="p-4">
            <KeyValue
              rows={[
                ["Max agent timeout", `${data.limits.max_agent_timeout_s}s`],
                ["Max setup timeout", `${data.limits.max_setup_timeout_s}s`],
                ["Max test timeout", `${data.limits.max_test_timeout_s}s`],
                ["Max memory", `${data.limits.max_memory_mb} MB`],
                ["Max CPUs", data.limits.max_cpus],
              ]}
            />
          </div>
        </Card>

        <Card title="Retention">
          <div className="p-4">
            <KeyValue
              rows={[
                ["Event payload cap", `${data.retention.event_payload_max_chars} chars`],
                ["Log tail cap", `${data.retention.log_tail_max_chars} chars`],
                [
                  "Event retention",
                  data.retention.event_retention_days === 0
                    ? "unlimited (0 = no pruning)"
                    : `${data.retention.event_retention_days} days`,
                ],
              ]}
            />
          </div>
        </Card>

        <Card title="API and versions">
          <div className="p-4">
            <KeyValue
              rows={[
                ["Environment", data.env],
                ["Bind", <Mono>{`${data.api.host}:${data.api.port}`}</Mono>],
                [
                  "Auth required",
                  <Badge tone={data.api.auth_required ? "good" : "warn"}>
                    {String(data.api.auth_required)}
                  </Badge>,
                ],
                ["CORS origins", <Mono>{data.api.cors_origins.join(", ") || "none"}</Mono>],
                ["Platform", data.versions.platform],
                ["Task schema", data.versions.task_schema],
                ["Grader implementation", data.versions.grader_implementation],
              ]}
            />
          </div>
        </Card>
      </div>

      <Card
        title="Provider credentials"
        subtitle="Presence only — values are never transmitted by the API"
      >
        <div className="p-4">
          <KeyValue
            rows={Object.entries(data.credentials_present).map(([provider, present]) => [
              provider,
              <Badge tone={present ? "good" : "neutral"}>
                {present ? "configured" : "not configured"}
              </Badge>,
            ])}
          />
        </div>
      </Card>

      {capabilities.data && (
        <>
          <Card title="Sandbox providers">
            <Table>
              <thead>
                <tr>
                  <Th>Provider</Th>
                  <Th>Available</Th>
                  <Th>Isolation</Th>
                  <Th>Detail</Th>
                </tr>
              </thead>
              <tbody>
                {capabilities.data.sandbox_providers.map((provider) => (
                  <tr key={provider.name}>
                    <Td>
                      <Mono className="font-semibold">{provider.name}</Mono>
                    </Td>
                    <Td>
                      <Badge tone={provider.available ? "good" : "neutral"}>
                        {String(provider.available)}
                      </Badge>
                    </Td>
                    <Td className={provider.isolation === "none" ? "text-rose-400" : undefined}>
                      {provider.isolation}
                    </Td>
                    <Td className="muted">{provider.detail}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <p className="muted hairline border-t p-3 text-2xs">
              Container isolation is not a virtual-machine boundary. A container escape or a kernel
              vulnerability reaches the host. See <Mono>docs/security.md</Mono>.
            </p>
          </Card>

          <Card
            title="Run outcome vocabulary"
            subtitle="Which outcomes count toward a success rate, and which are excluded"
          >
            <Table>
              <thead>
                <tr>
                  <Th>Outcome</Th>
                  <Th>Counts toward success rate</Th>
                  <Th>Attributable to agent</Th>
                </tr>
              </thead>
              <tbody>
                {capabilities.data.vocabularies.run_outcomes.map((outcome) => (
                  <tr key={outcome.value}>
                    <Td>
                      <OutcomeBadge outcome={outcome.value} />
                    </Td>
                    <Td>
                      <Badge tone={outcome.counts_toward_success_rate ? "good" : "neutral"}>
                        {String(outcome.counts_toward_success_rate)}
                      </Badge>
                    </Td>
                    <Td>
                      <Badge tone={outcome.attributable_to_agent ? "good" : "neutral"}>
                        {String(outcome.attributable_to_agent)}
                      </Badge>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>

          <Card title="Failure categories">
            <div className="flex flex-wrap gap-1.5 p-4">
              {capabilities.data.vocabularies.failure_categories.map((category) => (
                <Badge key={category} tone="neutral">
                  {category}
                </Badge>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
