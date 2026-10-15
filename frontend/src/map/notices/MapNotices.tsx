// Map notices (DESIGN-frontend F4, F8): portalled into the overlay's centre
// column. "Some map layers could not be drawn" stays while a basemap source
// failed or is slow, or a runtime error happened, and is withdrawn when all
// three are empty. A lost WebGL context has its own notice. Both offer Reload
// map, which rebuilds the map with a fresh runtime.

import type { ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { Notice } from "../../components/Notice";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { useMapNoticeStore } from "./mapNoticeStore";
import styles from "./MapNotices.module.css";

export const LAYERS_NOTICE_TITLE = "Some map layers could not be drawn";
export const CONTEXT_LOST_TITLE = "Map rendering stopped because the graphics device was reset";

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
  const failed = useMapNoticeStore((state) => state.basemapFailed);
  const slow = useMapNoticeStore((state) => state.basemapSlow);
  const runtimeErrors = useMapNoticeStore((state) => state.runtimeErrors);
  const contextLost = useGraphicsStore((state) => state.contextLost);
  const hasIssues = failed.length > 0 || slow.length > 0 || runtimeErrors > 0;

  const reload = (
    <Action
      tone="accent"
      onClick={() => {
        useGraphicsStore.getState().retryMap();
      }}
    >
      Reload map
    </Action>
  );

  return (
    <div className={styles.notices}>
      {contextLost ? (
        <Notice
          tone="danger"
          surface="floating"
          role="alert"
          title={CONTEXT_LOST_TITLE}
          message="The stations and the basemap come back when the map is reloaded."
          actions={reload}
          testId="map-context-lost"
        />
      ) : null}
      {hasIssues && !contextLost ? (
        <Notice
          tone="warning"
          surface="floating"
          title={LAYERS_NOTICE_TITLE}
          message="The stations keep drawing. Reload the map to try the missing layers again."
          detail={detailLine(failed, slow, runtimeErrors)}
          actions={reload}
          testId="map-layers-notice"
        />
      ) : null}
    </div>
  );
}
