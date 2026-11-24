/**
 * Fetch wrapper.
 *
 * The backend returns errors as `{"error": {"code", "message", ...}}`, so
 * `ApiError` carries that message through to the UI instead of a generic
 * "request failed" — a user debugging a benchmark needs the real reason.
 */

import { ApiError } from "./errors";
import { SNAPSHOT_MODE, readOnlyError, snapshotDownloadUrl, snapshotGet, snapshotGetText } from "./snapshot";
import type { ApiErrorBody } from "./types";

export { ApiError };

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8000/api/v1";

function buildUrl(path: string, params?: Record<string, string | number | boolean | undefined | null>): string {
  // A relative base ("/api/v1", `npm run tailnet`) resolves against the page.
  const url = new URL(`${API_BASE_URL}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = response.statusText || "request failed";
  let fields: { location: string; message: string }[] = [];
  try {
    const body = (await response.json()) as ApiErrorBody;
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      const raw = body.error.fields ?? body.error.errors ?? [];
      fields = raw.map((entry) => ({
        location: entry.location ?? "",
        message: entry.message ?? "",
      }));
    }
  } catch {
    // A non-JSON error body (proxy error page) leaves the status-based message.
  }
  return new ApiError(response.status, code, message, fields);
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>,
): Promise<T> {
  if (SNAPSHOT_MODE) return snapshotGet<T>(path, params);
  const response = await fetch(buildUrl(path, params), { headers: { Accept: "application/json" } });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

export async function apiGetText(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>,
): Promise<string> {
  if (SNAPSHOT_MODE) return snapshotGetText(path, params);
  const response = await fetch(buildUrl(path, params));
  if (!response.ok) throw await toApiError(response);
  return await response.text();
}

async function mutate<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (SNAPSHOT_MODE) throw readOnlyError();
  const response = await fetch(buildUrl(path), {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const apiPost = <T>(path: string, body?: unknown): Promise<T> => mutate<T>("POST", path, body);
export const apiPatch = <T>(path: string, body?: unknown): Promise<T> => mutate<T>("PATCH", path, body);
export const apiDelete = <T>(path: string): Promise<T> => mutate<T>("DELETE", path);

/** Absolute URL for download links handled by the browser, not by fetch. */
export const downloadUrl = (
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>,
): string => (SNAPSHOT_MODE ? snapshotDownloadUrl(path, params) : buildUrl(path, params));
