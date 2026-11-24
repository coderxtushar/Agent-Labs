import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";

// `npm run tailnet` serves the UI for your own devices through `tailscale serve`.
// The browser then calls `/api/v1` on the same origin and this proxy forwards it
// to the API on this machine, so the API itself never leaves loopback.
// AGENTBENCH_* values are read from the repo-root .env and the shell; they stay
// in this Node process and are never put into the browser bundle.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "AGENTBENCH_");
  const apiPort = env.AGENTBENCH_API_PORT ?? "8077";
  // Only needed when the API was started with a token: the proxy adds it, so
  // the token is not shipped to the browser.
  const proxy: Record<string, ProxyOptions> = {
    "/api": {
      target: `http://127.0.0.1:${apiPort}`,
      headers: env.AGENTBENCH_API_TOKEN ? { Authorization: `Bearer ${env.AGENTBENCH_API_TOKEN}` } : {},
    },
  };
  // `tailscale serve` uses the machine's *.ts.net name as the Host header.
  const allowedHosts = [".ts.net"];

  return {
    plugins: [react()],
    server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy, allowedHosts },
    preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy, allowedHosts },
    build: { sourcemap: false, chunkSizeWarningLimit: 900 },
  };
});
