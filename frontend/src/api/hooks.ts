/** React Query hooks. Polling is enabled only where work can be in flight. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";

import { apiDelete, apiGet, apiGetText, apiPatch, apiPost } from "./client";
import type {
  Agent,
  AdapterCapability,
  BenchmarkSummary,
  Comparison,
  EventPage,
  Experiment,
  ExperimentMetrics,
  FailurePage,
  Health,
  ImportReport,
  Overview,
  Page,
  Progress,
  Run,
  RunDetail,
  SpecValidationResult,
  SystemCapabilities,
  SystemSettings,
  SystemStats,
  Task,
  ValidationReport,
} from "./types";

const ACTIVE_POLL_MS = 3000;

export const useHealth = (): UseQueryResult<Health> =>
  useQuery({ queryKey: ["health"], queryFn: () => apiGet<Health>("/health") });

export const useStats = (): UseQueryResult<SystemStats> =>
  useQuery({ queryKey: ["stats"], queryFn: () => apiGet<SystemStats>("/system/stats") });

export const useSettings = (): UseQueryResult<SystemSettings> =>
  useQuery({ queryKey: ["settings"], queryFn: () => apiGet<SystemSettings>("/system/settings") });

export const useCapabilities = (): UseQueryResult<SystemCapabilities> =>
  useQuery({
    queryKey: ["capabilities"],
    queryFn: () => apiGet<SystemCapabilities>("/system/capabilities"),
    staleTime: 60_000,
  });

export const useOverview = (): UseQueryResult<Overview> =>
  useQuery({
    queryKey: ["overview"],
    queryFn: () => apiGet<Overview>("/overview"),
    refetchInterval: (query) => ((query.state.data?.runs.active ?? 0) > 0 ? ACTIVE_POLL_MS : false),
  });

// --- tasks ----------------------------------------------------------------- //
export interface TaskFilters {
  benchmark_id?: string;
  category?: string;
  difficulty?: string;
  validation_status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export const useTasks = (filters: TaskFilters): UseQueryResult<Page<Task>> =>
  useQuery({
    queryKey: ["tasks", filters],
    queryFn: () => apiGet<Page<Task>>("/tasks", { ...filters }),
  });

export const useTask = (taskKey: string | undefined): UseQueryResult<Task> =>
  useQuery({
    queryKey: ["task", taskKey],
    queryFn: () => apiGet<Task>(`/tasks/${encodeURIComponent(taskKey as string)}`),
    enabled: Boolean(taskKey),
  });

export const useBenchmarks = (): UseQueryResult<{ benchmarks: BenchmarkSummary[] }> =>
  useQuery({
    queryKey: ["benchmarks"],
    queryFn: () => apiGet<{ benchmarks: BenchmarkSummary[] }>("/tasks/benchmarks"),
  });

export function useValidateTask() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (taskKey: string) =>
      apiPost<ValidationReport>(`/tasks/${encodeURIComponent(taskKey)}/validate`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["tasks"] });
      void client.invalidateQueries({ queryKey: ["task"] });
      void client.invalidateQueries({ queryKey: ["benchmarks"] });
    },
  });
}

export function useSetTaskEnabled() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ taskKey, enabled }: { taskKey: string; enabled: boolean }) =>
      apiPatch<Task>(`/tasks/${encodeURIComponent(taskKey)}/enabled`, { enabled }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["tasks"] }),
  });
}

export function useImportBenchmark() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ content, allow_overwrite }: { content: string; allow_overwrite: boolean }) =>
      apiPost<ImportReport>("/tasks/import", { content, allow_overwrite }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["tasks"] });
      void client.invalidateQueries({ queryKey: ["benchmarks"] });
      void client.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

export const useValidateSpec = () =>
  useMutation({
    mutationFn: (spec: unknown) => apiPost<SpecValidationResult>("/tasks/validate-spec", spec),
  });

// --- agents ---------------------------------------------------------------- //
export const useAgents = (): UseQueryResult<Page<Agent>> =>
  useQuery({ queryKey: ["agents"], queryFn: () => apiGet<Page<Agent>>("/agents", { limit: 200 }) });

export const useAdapters = (): UseQueryResult<{ adapters: AdapterCapability[]; keys: string[] }> =>
  useQuery({
    queryKey: ["adapters"],
    queryFn: () => apiGet<{ adapters: AdapterCapability[]; keys: string[] }>("/agents/adapters"),
    staleTime: 60_000,
  });

export interface NewAgent {
  name: string;
  version: string;
  adapter: string;
  description: string;
  model: string | null;
  config: Record<string, unknown>;
}

export function useCreateAgent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: NewAgent) => apiPost<Agent>("/agents", payload),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["agents"] });
      void client.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

export function useSetAgentEnabled() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      apiPatch<Agent>(`/agents/${id}/enabled`, { enabled }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["agents"] }),
  });
}

export function useDeleteAgent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDelete<void>(`/agents/${id}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["agents"] }),
  });
}

// --- experiments ----------------------------------------------------------- //
export const useExperiments = (): UseQueryResult<Page<Experiment>> =>
  useQuery({
    queryKey: ["experiments"],
    queryFn: () => apiGet<Page<Experiment>>("/experiments", { limit: 100 }),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => (item.progress?.active ?? 0) > 0 || (item.progress?.pending ?? 0) > 0)
        ? ACTIVE_POLL_MS
        : false,
  });

export const useExperiment = (id: string | undefined): UseQueryResult<Experiment> =>
  useQuery({
    queryKey: ["experiment", id],
    queryFn: () => apiGet<Experiment>(`/experiments/${id as string}`),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      (query.state.data?.progress?.is_complete ?? true) ? false : ACTIVE_POLL_MS,
  });

export const useExperimentMetrics = (id: string | undefined): UseQueryResult<ExperimentMetrics> =>
  useQuery({
    queryKey: ["experiment-metrics", id],
    queryFn: () => apiGet<ExperimentMetrics>(`/experiments/${id as string}/metrics`),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      (query.state.data?.completeness.is_complete ?? true) ? false : ACTIVE_POLL_MS,
  });

export const useExperimentRuns = (id: string | undefined, outcome?: string): UseQueryResult<Page<Run>> =>
  useQuery({
    queryKey: ["experiment-runs", id, outcome],
    queryFn: () => apiGet<Page<Run>>(`/experiments/${id as string}/runs`, { limit: 200, outcome }),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      query.state.data?.items.some((run) => !run.is_terminal) ? ACTIVE_POLL_MS : false,
  });

export interface NewExperiment {
  name: string;
  agent_config_id: string;
  task_keys: string[];
  description: string;
  repetitions: number;
  concurrency: number;
  seed: number | null;
  include_hints: boolean;
  launch: boolean;
}

export function useCreateExperiment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: NewExperiment) => apiPost<Experiment>("/experiments", payload),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["experiments"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
      void client.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

export function useExperimentAction(action: "launch" | "cancel" | "resume") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) =>
      apiPost<Progress>(`/experiments/${id}/${action}`, action === "resume" ? { force: Boolean(force) } : undefined),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["experiment"] });
      void client.invalidateQueries({ queryKey: ["experiments"] });
      void client.invalidateQueries({ queryKey: ["experiment-metrics"] });
      void client.invalidateQueries({ queryKey: ["experiment-runs"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
    },
  });
}

// --- runs ------------------------------------------------------------------ //
export const useRun = (id: string | undefined): UseQueryResult<RunDetail> =>
  useQuery({
    queryKey: ["run", id],
    queryFn: () => apiGet<RunDetail>(`/runs/${id as string}`),
    enabled: Boolean(id),
    refetchInterval: (query) => ((query.state.data?.is_terminal ?? true) ? false : ACTIVE_POLL_MS),
  });

export const useRunEvents = (id: string | undefined, live: boolean): UseQueryResult<EventPage> =>
  useQuery({
    queryKey: ["run-events", id],
    queryFn: () => apiGet<EventPage>(`/runs/${id as string}/events`, { limit: 2000 }),
    enabled: Boolean(id),
    refetchInterval: live ? ACTIVE_POLL_MS : false,
  });

export const useRunPatch = (id: string | undefined, enabled: boolean): UseQueryResult<string> =>
  useQuery({
    queryKey: ["run-patch", id],
    queryFn: () => apiGetText(`/runs/${id as string}/patch`),
    enabled: Boolean(id) && enabled,
  });

export function useAnnotateRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      runId,
      category,
      rationale,
      author,
    }: {
      runId: string;
      category: string;
      rationale: string;
      author: string;
    }) => apiPost(`/runs/${runId}/annotations`, { category, rationale, author: author || null }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["run"] });
      void client.invalidateQueries({ queryKey: ["failures"] });
    },
  });
}

// --- metrics --------------------------------------------------------------- //
export const useComparison = (
  baseline: string | undefined,
  candidate: string | undefined,
): UseQueryResult<Comparison> =>
  useQuery({
    queryKey: ["comparison", baseline, candidate],
    queryFn: () => apiGet<Comparison>("/compare", { baseline, candidate }),
    enabled: Boolean(baseline && candidate && baseline !== candidate),
  });

export interface FailureFilters {
  experiment_id?: string;
  category?: string;
  task_id?: string;
  source?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export const useFailures = (filters: FailureFilters): UseQueryResult<FailurePage> =>
  useQuery({
    queryKey: ["failures", filters],
    queryFn: () => apiGet<FailurePage>("/failures", { ...filters }),
  });
