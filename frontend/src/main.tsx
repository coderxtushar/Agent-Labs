import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

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
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
});

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
