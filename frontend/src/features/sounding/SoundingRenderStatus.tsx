import { useLayoutEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { GlassContextBoundary } from "../../components/GlassSurface";
import { Surface } from "../../ui/Surface";
import styles from "./SoundingRenderStatus.module.css";

export type SoundingRenderPhase = "initial" | "recalculating" | "updating" | "refining";

const PHASE_COPY: Record<SoundingRenderPhase, { title: string; detail: string }> = {
  initial: {
    title: "Rendering sounding image…",
    detail: "Skew-T and hodograph",
  },
  recalculating: {
    title: "Recalculating profile…",
    detail: "Applying edited levels",
  },
  updating: {
    title: "Rendering updated sounding…",
    detail: "Refreshing chart view",
  },
  refining: {
    title: "Preview ready · Refining image…",
    detail: "Adding map locator",
  },
};

export function SoundingRenderStatus({ phase, overImage }: { phase: SoundingRenderPhase; overImage: boolean }): ReactNode {
  const copy = PHASE_COPY[phase];
  const stageRef = useRef<HTMLDivElement>(null);
  const portalRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const stage = stageRef.current;
    const portal = portalRef.current;
    if (!stage || !portal) return;

    const sync = () => {
      const hidden = stage.closest("[hidden]") !== null;
      portal.hidden = hidden;
      if (hidden) return;

      const box = stage.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) {
        portal.style.setProperty("--stage-top", `${String(box.top)}px`);
        portal.style.setProperty("--stage-left", `${String(box.left)}px`);
        portal.style.setProperty("--stage-width", `${String(box.width)}px`);
        portal.style.setProperty("--stage-height", `${String(box.height)}px`);
      }
    };

    sync();
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(sync) : null;
    ro?.observe(stage);
    window.addEventListener("resize", sync);

    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", sync);
    };
  }, []);

  return (
    <>
      <div
        ref={stageRef}
        className={styles.scrim}
        data-over-image={overImage}
        data-phase={phase}
        aria-hidden="true"
      />
      {typeof document !== "undefined"
        ? createPortal(
            <GlassContextBoundary>
              <div
                ref={portalRef}
                className={styles.portal}
                data-over-image={overImage}
                data-phase={phase}
                data-testid="sounding-render-status"
              >
                <Surface elevation="popover" className={styles.card} data-motion="enter" data-overlay-kind="sounding">
                  <span className={styles.indicator} aria-hidden="true">
                    <svg className={styles.spinner} viewBox="0 0 24 24" fill="none">
                      <circle className={styles.ringTrack} cx="12" cy="12" r="9" />
                      <circle className={styles.ringArc} cx="12" cy="12" r="9" pathLength={100} />
                    </svg>
                  </span>
                  <div className={styles.copy}>
                    <p className={styles.title} role="status" aria-live="polite">{copy.title}</p>
                    <p className={styles.detail}>{copy.detail}</p>
                  </div>
                </Surface>
              </div>
            </GlassContextBoundary>,
            document.body,
          )
        : null}
    </>
  );
}
