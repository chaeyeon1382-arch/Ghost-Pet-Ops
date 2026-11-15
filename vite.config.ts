import react from "@vitejs/plugin-react";
import { loadEnv, type Plugin } from "vite";
import { defineConfig } from "vitest/config";
import { DEMO_SCENARIOS, buildMockResponse } from "./src/api/mockStatus";
import type { Status } from "./src/domain/types";

/**
 * Dev-only helpers for running the app in `api` mode without a deployed backend.
 * They apply only to the dev server (`npm run dev`), never to `vite build`, and they are read here,
 * in Node, from variables WITHOUT the `VITE_` prefix. Vite never exposes those to browser code,
 * and nothing under src/ reads them.
 *
 *   DEV_FAKE_STATUS=<unknown|healthy|slow|down|recovering>
 *       GET /status is answered by the existing mock builder. No backend needed.
 *   DEV_API_TARGET=<http(s) URL>
 *       GET /status is forwarded to that URL by the dev server's proxy.
 *
 * Set at most one of them. See README.md.
 */
interface DevOptions {
  fakeScenario: Status | null;
  apiTarget: string | null;
  /** Extra hosts the dev server answers to. Only set inside GitHub Codespaces. */
  allowedHosts: string[];
}

const NO_DEV_OPTIONS: DevOptions = { fakeScenario: null, apiTarget: null, allowedHosts: [] };

function readDevOptions(env: Record<string, string>): DevOptions {
  const fake = (env.DEV_FAKE_STATUS ?? "").trim();
  const target = (env.DEV_API_TARGET ?? "").trim();

  if (fake !== "" && target !== "") {
    throw new Error("Set only one of DEV_FAKE_STATUS and DEV_API_TARGET, so it is clear which one answers /status.");
  }
  if (fake !== "" && !Object.hasOwn(DEMO_SCENARIOS, fake)) {
    throw new Error(`DEV_FAKE_STATUS must be one of: ${Object.keys(DEMO_SCENARIOS).join(", ")}. Got "${fake}".`);
  }
  if (target !== "" && !/^https?:\/\/[^\s]+$/.test(target)) {
    throw new Error(`DEV_API_TARGET must be an http:// or https:// URL. Got "${target}".`);
  }

  // Vite blocks Host headers it does not know. A codespace's forwarded URL is
  // https://<name>-5173.app.github.dev, so allow that one domain, and only inside a codespace.
  const inCodespace = env.CODESPACES === "true";
  const forwardingDomain = env.GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN || "app.github.dev";

  return {
    fakeScenario: fake === "" ? null : (fake as Status),
    apiTarget: target === "" ? null : target,
    allowedHosts: inCodespace ? [`.${forwardingDomain}`] : [],
  };
}

/** Answers GET /status with the mock data. `apply: "serve"` keeps it out of production builds. */
function fakeStatusPlugin(scenario: Status): Plugin {
  return {
    name: "fake-status",
    apply: "serve",
    configureServer(server) {
      server.config.logger.info(`[fake-status] GET /status answers with the "${scenario}" mock scenario (dev server only)`);
      server.middlewares.use("/status", (req, res, next) => {
        // The mount path is stripped, so "/status" arrives as "/" and "/status/x" as "/x".
        const path = (req.url ?? "/").split("?")[0];
        if (req.method !== "GET" || path !== "/") {
          next();
          return;
        }
        res.statusCode = 200;
        res.setHeader("Content-Type", "application/json");
        res.setHeader("Cache-Control", "no-store");
        res.end(JSON.stringify(buildMockResponse(scenario)));
      });
    },
  };
}

export default defineConfig(({ command, mode }) => {
  // "." is the project root, where npm runs. loadEnv also reads variables set in the shell.
  const env = loadEnv(mode, ".", "");
  // Production builds ignore every DEV_* variable.
  const dev = command === "serve" ? readDevOptions(env) : NO_DEV_OPTIONS;

  return {
    plugins: [react(), ...(dev.fakeScenario !== null ? [fakeStatusPlugin(dev.fakeScenario)] : [])],
    server: {
      proxy: dev.apiTarget !== null ? { "/status": { target: dev.apiTarget, changeOrigin: true } } : undefined,
      allowedHosts: dev.allowedHosts.length > 0 ? dev.allowedHosts : undefined,
    },
    test: { environment: "node", include: ["src/**/*.test.{ts,tsx}"] },
  };
});
