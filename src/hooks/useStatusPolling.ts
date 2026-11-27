import { useEffect, useState } from "react";
import { fetchStatus } from "../api/fetchStatus";
import { PollController, type VisibilitySource } from "../api/pollController";
import { toView } from "../api/statusState";
import type { StatusView } from "../api/types";

const documentVisibility: VisibilitySource = {
  isHidden: () => document.visibilityState === "hidden",
  subscribe: (listener) => {
    document.addEventListener("visibilitychange", listener);
    return () => document.removeEventListener("visibilitychange", listener);
  },
};

/**
 * Starts polling /status when the component mounts and stops when it unmounts.
 * All the logic lives in PollController. This hook only connects it to React.
 * React StrictMode runs the effect, its cleanup, then the effect again in development.
 * The first controller is stopped before the second starts, so one loop remains.
 */
export function useStatusPolling(baseIntervalMs: number): StatusView {
  const [view, setView] = useState<StatusView>({ kind: "loading" });

  useEffect(() => {
    const controller = new PollController({
      fetchStatus: (signal) => fetchStatus(signal),
      baseIntervalMs,
      now: () => Date.now(),
      visibility: documentVisibility,
      onChange: (state) => {
        setView(toView(state));
        // The overlay does not show causes, so log them for debugging. Never the response body.
        if (state.lastFailure !== null) console.warn("[status] request failed:", state.lastFailure);
      },
    });
    controller.start();
    return () => controller.stop();
  }, [baseIntervalMs]);

  return view;
}
