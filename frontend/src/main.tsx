import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { SNAPSHOT_MODE, loadSnapshotManifest } from "./api/snapshot";
import { router } from "./router";
import "./index.css";

// Apply the saved theme before first paint, for the home page and the app alike.
try {
  document.documentElement.dataset.theme =
    window.localStorage.getItem("agentbench-theme") === "light" ? "light" : "dark";
} catch {
  // Storage unavailable (private mode): keep the dark default from index.html.
}

const queryClient = new QueryClient({
  defaultOptions: {
    // A static snapshot never changes, so it is never stale and never retried.
    queries: SNAPSHOT_MODE
      ? { retry: false, refetchOnWindowFocus: false, staleTime: Infinity }
      : { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
});

const root = createRoot(document.getElementById("root") as HTMLElement);
const renderApp = () =>
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );

if (SNAPSHOT_MODE) {
  // Every read is answered from the manifest, so it must be in hand first.
  loadSnapshotManifest().then(renderApp, (error: unknown) => {
    root.render(
      <div style={{ padding: 32, fontFamily: "system-ui, sans-serif" }}>
        <h1 style={{ fontSize: 18, fontWeight: 600 }}>Agent Labs snapshot could not load</h1>
        <p style={{ opacity: 0.7, marginTop: 8 }}>{error instanceof Error ? error.message : String(error)}</p>
        <p style={{ opacity: 0.7, marginTop: 8 }}>
          Export one with <code>agentbench snapshot export</code>, then <code>npm run build:snapshot</code>.
        </p>
      </div>,
    );
  });
} else {
  renderApp();
}
