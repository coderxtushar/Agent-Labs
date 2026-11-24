/**
 * Static snapshot data source.
 *
 * A build made with `npm run build:snapshot` (VITE_DATA_SOURCE=snapshot) never
 * calls an API. It reads `snapshot/manifest.json`, written by
 * `agentbench snapshot export`, which maps each GET the UI makes to the file
 * holding the API's exact response (status included). So every metric,
 * interval and provenance label shown is the backend's own output.
 *
 * The five list endpoints are exported whole, as "collections", and filtered
 * and paged here with the same rules as their SQL (see the routers named on
 * each handler). Anything else that was not exported fails with a
 * `not_in_snapshot` error, and every write fails with `read_only_snapshot`.
 *
 * The request key must match `request_key` in backend `agentbench/snapshot.py`:
 * the decoded path, then `?` and `name=value` pairs sorted by name, raw values,
 * booleans as true/false, empty values dropped.
 */

import { ApiError } from "./errors";
import type { ApiErrorBody } from "./types";

type Params = Record<string, string | number | boolean | undefined | null>;
type Item = Record<string, unknown>;

interface Entry {
  file: string;
  status: number;
  type: string;
}

export interface SnapshotManifest {
  format: number;
  generated_at: string;
  agentlabs_version: string;
  counts: { tasks: number; experiments: number; runs: number; agents: number; responses: number };
  notes: string[];
  entries: Record<string, Entry>;
  collections: Record<string, string>;
}

export const SNAPSHOT_MODE: boolean = import.meta.env.VITE_DATA_SOURCE === "snapshot";

const ROOT = `${import.meta.env.BASE_URL}snapshot/`;
const SUPPORTED_FORMAT = 1;

let manifest: SnapshotManifest | null = null;
const files = new Map<string, Promise<unknown>>();

/** Loads the manifest once; the app renders only after this resolves. */
export async function loadSnapshotManifest(): Promise<SnapshotManifest> {
  const response = await fetch(`${ROOT}manifest.json`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`snapshot manifest not found (${response.status})`);
  const loaded = (await response.json()) as SnapshotManifest;
  if (loaded.format !== SUPPORTED_FORMAT) {
    throw new Error(`snapshot format ${loaded.format} is not supported (expected ${SUPPORTED_FORMAT})`);
  }
  manifest = loaded;
  return loaded;
}

export const snapshotManifest = (): SnapshotManifest | null => manifest;

function cleanParams(params?: Params): [string, string][] {
  return Object.entries(params ?? {})
    .filter((pair): pair is [string, string | number | boolean] => {
      const value = pair[1];
      return value !== undefined && value !== null && value !== "";
    })
    .map(([name, value]): [string, string] => [name, String(value)])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}

export function snapshotKey(path: string, params?: Params): string {
  const pairs = cleanParams(params);
  const decoded = decodeURIComponent(path);
  return pairs.length ? `${decoded}?${pairs.map(([name, value]) => `${name}=${value}`).join("&")}` : decoded;
}

/** The export date for display, e.g. "22 Nov 2026"; empty if unknown. */
export function snapshotDate(): string {
  const date = manifest ? new Date(manifest.generated_at) : null;
  return date && !Number.isNaN(date.getTime())
    ? date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
    : "";
}

const exportedOn = (): string => (snapshotDate() ? ` (exported ${snapshotDate()})` : "");

const notInSnapshot = (key: string): ApiError =>
  new ApiError(
    404,
    "not_in_snapshot",
    `This view is not part of the published snapshot${exportedOn()}. Run Agent Labs locally to explore it. [${key}]`,
  );

export const readOnlyError = (): ApiError =>
  new ApiError(
    405,
    "read_only_snapshot",
    "This is a read-only snapshot of recorded Agent Labs results, so nothing can be created or changed here. Run Agent Labs locally to launch experiments.",
  );

function readFile(file: string, asText: boolean): Promise<unknown> {
  const id = `${asText ? "t" : "j"}:${file}`;
  let pending = files.get(id);
  if (!pending) {
    pending = fetch(`${ROOT}${file}`).then(async (response) => {
      if (!response.ok) throw new ApiError(response.status, `http_${response.status}`, `snapshot file missing: ${file}`);
      return asText ? response.text() : response.json();
    });
    // A failed read is retried next time instead of caching the failure.
    pending.catch(() => files.delete(id));
    files.set(id, pending);
  }
  return pending;
}

async function errorFrom(entry: Entry): Promise<ApiError> {
  let code = `http_${entry.status}`;
  let message = "request failed";
  try {
    const body = (await readFile(entry.file, false)) as ApiErrorBody;
    code = body.error?.code ?? code;
    message = body.error?.message ?? message;
  } catch {
    // Keep the status-based message.
  }
  return new ApiError(entry.status, code, message);
}

function requireManifest(): SnapshotManifest {
  if (!manifest) throw new ApiError(503, "snapshot_not_loaded", "The snapshot manifest has not loaded.");
  return manifest;
}

export async function snapshotGet<T>(path: string, params?: Params): Promise<T> {
  const loaded = requireManifest();
  const key = snapshotKey(path, params);
  const entry = loaded.entries[key];
  if (entry) {
    if (entry.status >= 400) throw await errorFrom(entry);
    return (await readFile(entry.file, false)) as T;
  }
  const decoded = decodeURIComponent(path);
  const collection = loaded.collections[decoded];
  const handler = collection ? listHandler(decoded) : undefined;
  if (collection && handler) {
    const { items } = (await readFile(collection, false)) as { items: Item[] };
    return handler(items, Object.fromEntries(cleanParams(params))) as T;
  }
  throw notInSnapshot(key);
}

export async function snapshotGetText(path: string, params?: Params): Promise<string> {
  const entry = requireManifest().entries[snapshotKey(path, params)];
  if (!entry) throw notInSnapshot(snapshotKey(path, params));
  if (entry.status >= 400) throw await errorFrom(entry);
  return (await readFile(entry.file, true)) as string;
}

/** A static file URL for download links; a short explanation when not exported. */
export function snapshotDownloadUrl(path: string, params?: Params): string {
  const entry = manifest?.entries[snapshotKey(path, params)];
  return entry && entry.status < 400 ? `${ROOT}${entry.file}` : `${ROOT}not-in-snapshot.txt`;
}

// --------------------------------------------------------------------------- //
// List endpoints: filter + page the exported collection like the server does.
// --------------------------------------------------------------------------- //

type Query = Record<string, string>;
type Handler = (items: Item[], query: Query) => unknown;

const field = (item: Item, path: string): unknown =>
  path.split(".").reduce<unknown>((value, part) => (value as Item | null | undefined)?.[part], item);
const text = (item: Item, path: string): string => String(field(item, path) ?? "").toLowerCase();
const truthy = (value: string | undefined): boolean => value === "true";

function page(items: Item[], query: Query, defaultLimit = 50) {
  const limit = Math.min(Math.max(Number(query.limit ?? defaultLimit) || defaultLimit, 1), 200);
  const offset = Math.max(Number(query.offset ?? 0) || 0, 0);
  const slice = items.slice(offset, offset + limit);
  return {
    items: slice,
    pagination: { total: items.length, limit, offset, has_more: offset + slice.length < items.length },
  };
}

function equals(items: Item[], query: Query, pairs: [param: string, path: string][]): Item[] {
  return pairs.reduce(
    (rows, [param, path]) => (query[param] ? rows.filter((row) => String(field(row, path)) === query[param]) : rows),
    items,
  );
}

const byText = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/** routers/tasks.py `list_endpoint` → registry/service.py `task_query`. */
const tasksHandler: Handler = (items, query) => {
  let rows = equals(items, query, [
    ["benchmark_id", "benchmark_id"],
    ["category", "category"],
    ["difficulty", "difficulty"],
    ["validation_status", "validation_status"],
  ]);
  if (truthy(query.enabled_only)) rows = rows.filter((row) => row.enabled === true);
  const needle = (query.search ?? "").toLowerCase();
  if (needle) rows = rows.filter((row) => text(row, "title").includes(needle) || text(row, "task_id").includes(needle));
  if (query.tag) rows = rows.filter((row) => ((row.tags as string[] | undefined) ?? []).includes(query.tag));
  return page(rows, query);
};

/** routers/experiments.py `list_endpoint`; the collection is in `newest` order. */
const experimentsHandler: Handler = (items, query) => {
  let rows = equals(items, query, [
    ["state", "state"],
    ["benchmark_id", "benchmark_id"],
    ["agent_config_id", "agent.config_id"],
  ]);
  if (query.include_simulated === "false") rows = rows.filter((row) => field(row, "agent.is_simulated") !== true);
  const needle = (query.search ?? "").trim().toLowerCase();
  if (needle) {
    rows = rows.filter((row) =>
      [
        text(row, "name"),
        text(row, "description"),
        text(row, "benchmark_id"),
        text(row, "id"),
        text(row, "agent.label").split("@")[0],
        text(row, "agent.model"),
      ].some((value) => value.includes(needle)),
    );
  }
  const created = (row: Item) => String(row.created_at ?? "");
  const id = (row: Item) => String(row.id ?? "");
  if (query.sort === "oldest") {
    rows = [...rows].sort((a, b) => byText(created(a), created(b)) || byText(id(a), id(b)));
  } else if (query.sort === "name") {
    rows = [...rows].sort((a, b) => byText(text(a, "name"), text(b, "name")) || byText(id(a), id(b)));
  }
  return page(rows, query);
};

/** routers/runs.py `list_endpoint` (newest first, as exported). */
const runsHandler: Handler = (items, query) => {
  let rows = equals(items, query, [
    ["experiment_id", "experiment_id"],
    ["outcome", "outcome"],
    ["state", "state"],
    ["task_id", "task_id"],
  ]);
  if (truthy(query.active_only)) {
    rows = rows.filter((row) => !["completed", "cancelled", "pending"].includes(String(row.state)));
  }
  return page(rows, query);
};

/** routers/experiments.py `runs_endpoint` (task, then attempt order, as exported). */
const experimentRunsHandler: Handler = (items, query) => {
  let rows = equals(items, query, [
    ["outcome", "outcome"],
    ["state", "state"],
  ]);
  if (query.task_key) {
    const [taskId, version] = query.task_key.split("@");
    rows = rows.filter((row) => row.task_id === taskId && (!version || row.task_version === version));
  }
  return page(rows, query);
};

/** routers/metrics.py `failures_endpoint`: the distribution ignores only `category`. */
const failuresHandler: Handler = (items, query) => {
  let scoped = equals(items, query, [
    ["experiment_id", "run.experiment_id"],
    ["task_id", "run.task_id"],
    ["source", "annotation.source"],
  ]);
  const needle = (query.search ?? "").toLowerCase();
  if (needle) {
    scoped = scoped.filter(
      (row) =>
        text(row, "annotation.rationale").includes(needle) ||
        text(row, "run.task_id").includes(needle) ||
        text(row, "run.termination_reason").includes(needle),
    );
  }
  const distribution: Record<string, number> = {};
  for (const row of scoped) {
    const category = String(field(row, "annotation.category"));
    distribution[category] = (distribution[category] ?? 0) + 1;
  }
  const categories = (query.category ?? "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  const rows = categories.length
    ? scoped.filter((row) => categories.includes(String(field(row, "annotation.category"))))
    : scoped;
  return {
    ...page(rows, query),
    distribution,
    distribution_scope: "all filters except category (experiment, task, source and search apply)",
  };
};

function listHandler(path: string): Handler | undefined {
  if (path === "/tasks") return tasksHandler;
  if (path === "/experiments") return experimentsHandler;
  if (path === "/runs") return runsHandler;
  if (path === "/failures") return failuresHandler;
  if (/^\/experiments\/[^/]+\/runs$/.test(path)) return experimentRunsHandler;
  return undefined;
}
