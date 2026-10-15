// The health indicator's label and tone (DESIGN-contracts C3 UI mapping,
// DESIGN-frontend F8 States table). Pure, so the mapping is tested directly.

import type { StatusTone } from "../../components/StatusDot";
import type { BackendHealth } from "../../types/contracts";

export const HEALTH_LABELS = {
  checking: "Checking backend",
  ready: "Backend ready",
  degraded: "Backend degraded",
  starting: "Starting backend",
  restarting: "Restarting backend",
  unavailable: "Backend unavailable",
  unknown: "Backend status unknown",
} as const;

export interface HealthView {
  label: (typeof HEALTH_LABELS)[keyof typeof HEALTH_LABELS];
  tone: StatusTone;
}

/** The query's state: pending (no answer yet), error (the last check failed), or the health. */
export function healthView(status: "pending" | "error" | "success", health: BackendHealth | undefined): HealthView {
  if (status === "error") {
    return { label: HEALTH_LABELS.unknown, tone: "warning" };
  }
  if (health === undefined) {
    return { label: HEALTH_LABELS.checking, tone: "neutral" };
  }
  switch (health.bridge.status) {
    case "starting":
      return { label: HEALTH_LABELS.starting, tone: "info" };
    case "restarting":
      return { label: HEALTH_LABELS.restarting, tone: "warning" };
    case "unavailable":
      return { label: HEALTH_LABELS.unavailable, tone: "danger" };
    case "up":
      return health.scientificBackend?.status === "ok"
        ? { label: HEALTH_LABELS.ready, tone: "success" }
        : { label: HEALTH_LABELS.degraded, tone: "warning" };
  }
}
