// Compact map recovery bar; diagnostics expand beneath its message and actions.

import { useId, useState, type ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { useMapNoticeStore } from "./mapNoticeStore";
import styles from "./MapNotices.module.css";
import { ChevronDown, Map, RotateCw } from "lucide-react";

export const LAYERS_NOTICE_TITLE = "Map layers missing";
export const CONTEXT_LOST_TITLE = "Map drawing stopped";

function detailLine(failed: readonly string[], slow: readonly string[], runtimeErrors: number): string {
  const parts: string[] = [];
  if (failed.length > 0) {
    parts.push(`Could not load: ${failed.join(", ")}.`);
  }
  if (slow.length > 0) {
    parts.push(`Still loading: ${slow.join(", ")}.`);
  }
  if (runtimeErrors > 0) {
    parts.push(`Other map errors: ${String(runtimeErrors)}.`);
  }
  return parts.join(" ");
}

export function MapNotices(): ReactNode {
  const id = useId();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const failed = useMapNoticeStore((state) => state.basemapFailed);
  const slow = useMapNoticeStore((state) => state.basemapSlow);
  const runtimeErrors = useMapNoticeStore((state) => state.runtimeErrors);
  const contextLost = useGraphicsStore((state) => state.contextLost);
  const hasIssues = failed.length > 0 || slow.length > 0 || runtimeErrors > 0;

  if (!hasIssues && !contextLost) return null;

  return (
    <div className={styles.notices}>
      <Surface className={styles.notice} elevation="chrome" data-motion="enter"
        data-tone={contextLost ? "danger" : "warning"} role={contextLost ? "alert" : "status"}
        aria-labelledby={`${id}-title`} data-testid={contextLost ? "map-context-lost" : "map-layers-notice"}>
        <div className={styles.content}>
          <Map className={styles.icon} aria-hidden="true" />
          <div className={styles.copy}>
            <p id={`${id}-title`} className={styles.title}>{contextLost ? CONTEXT_LOST_TITLE : LAYERS_NOTICE_TITLE}</p>
            <p className={styles.message}>{contextLost ? "The graphics device was reset." : "Some map layers are unavailable."}</p>
          </div>
        </div>
        <div className={styles.actions}>
          {contextLost ? null : <Action className={styles.disclosure} aria-expanded={detailsOpen} aria-controls={`${id}-details`}
            onClick={() => { setDetailsOpen(!detailsOpen); }}>
            Details <ChevronDown className="icon-sm" aria-hidden="true" />
          </Action>}
          <Action tone="accent" onClick={() => { setDetailsOpen(false); useGraphicsStore.getState().retryMap(); }}>
            <RotateCw className="icon-sm" aria-hidden="true" /> Reload map
          </Action>
        </div>
        {contextLost ? null : <p id={`${id}-details`} className={styles.details} hidden={!detailsOpen}>
          {detailLine(failed, slow, runtimeErrors)}
        </p>}
      </Surface>
    </div>
  );
}
