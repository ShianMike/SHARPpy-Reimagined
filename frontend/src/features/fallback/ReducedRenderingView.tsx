// The reduced-rendering view (DESIGN-frontend F6, F8): replaces the map when
// WebGL2 is missing or the map failed to start. A fixed warning notice per
// cause (rendererError.message goes to the console and Settings, never here),
// with Try the map again for every cause except missing WebGL2, which cannot
// change during the session; then the catalogue's status; then StationList.
// The toolbar, search, and inspector keep working beside it.

import type { ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { Notice } from "../../components/Notice";
import { useGraphicsStore } from "../../stores/graphicsStore";
import type { RendererErrorKind } from "../../types/graphics";
import { CatalogueStatusNotice } from "../errors/CatalogueStatusNotice";
import { StationList } from "./StationList";
import styles from "./ReducedRenderingView.module.css";

export const REDUCED_NOTICES: Readonly<Record<"noWebgl2" | RendererErrorKind, string>> = {
  noWebgl2: "Map needs WebGL2",
  constructor: "Map could not start",
  overlay: "Station layer unavailable",
  timeout: "Map startup timed out",
};

export const STILL_WORKS = "Station search, selection, and the inspector still work.";

export function ReducedRenderingView(): ReactNode {
  const mode = useGraphicsStore((state) => state.effective.mode);
  const errorKind = useGraphicsStore((state) => state.rendererError?.kind ?? null);
  const noWebgl2 = mode === "reduced";
  const title = REDUCED_NOTICES[noWebgl2 || errorKind === null ? "noWebgl2" : errorKind];
  return (
    <section className={styles.view} aria-label="Station list" data-testid="reduced-rendering">
      <Notice
        tone="warning"
        surface="flat"
        title={title}
        message={noWebgl2 ? `This device does not provide WebGL2. ${STILL_WORKS}` : errorKind === "timeout" ? `No frame appeared within 15 seconds. ${STILL_WORKS}` : STILL_WORKS}
        testId="reduced-notice"
        {...(noWebgl2
          ? {}
          : {
              actions: (
                <Action
                  tone="accent"
                  onClick={() => {
                    useGraphicsStore.getState().retryMap();
                  }}
                >
                  Try the map again
                </Action>
              ),
            })}
      />
      <CatalogueStatusNotice surface="flat" />
      <StationList />
    </section>
  );
}
