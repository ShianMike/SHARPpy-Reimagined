import type { ReactNode } from "react";
import { Eye, EyeOff, TriangleAlert } from "lucide-react";
import { useForecastContext } from "../../features/forecast/useForecast";
import { cx } from "../../lib/cx";
import { displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { panelLayers, useMapStore, type PanelLayout } from "../../stores/mapStore";
import { IconAction } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import type { PanelField } from "../panelField";
import { FieldLegend } from "./FieldLegend";
import styles from "./ParameterScale.module.css";
import hints from "../../ui/HoverHint.module.css";

export function ModelLayerButton({ panel, field, className, hintSide }: {
  panel: number; field: PanelField; className?: string | undefined; hintSide: "left" | "right";
}): ReactNode {
  const visible = field.blocked !== "hidden";
  const loading = visible && field.frame.isFetching && !field.frame.isError;
  return <IconAction className={cx(styles.visibility, className)} size="sm" icon={visible ? Eye : EyeOff} loading={loading} label="Model layer" aria-pressed={visible} aria-description={`${loading ? "Loading. " : ""}${visible ? "Layer on. Activate to hide." : "Layer off. Activate to show."}`} hintSide={hintSide} hintText={`Model layer${loading ? " · Loading…" : ""}`}
    onClick={() => {
      const state = useMapStore.getState();
      state.setActivePanel(panel);
      state.setLayerVisible("model-field", !panelLayers(state, panel)["model-field"].visible);
    }} />;
}

export function ParameterScale({ panel, layout, field }: { panel: number; layout: PanelLayout; field: PanelField }): ReactNode {
  const units = useUiStore((state) => state.units);
  const context = useForecastContext();
  const { product, blocked, frame } = field;
  if (product === null || context?.fieldsAvailable !== true) return null;
  const unavailable = blocked !== "hidden" && frame.isError;
  return <aside data-motion="fade" aria-label="Parameter scale" aria-description={`${product.label} · ${displayUnit(product.palette.units, units, product.palette.display) || "Unitless"}${layout > 1 ? ` · Panel ${String(panel + 1)}` : ""}`} className={styles.panel} data-testid="parameter-scale">
    <FieldLegend palette={product.palette} vertical />
    {layout === 1 || unavailable ? <Surface className={styles.controls}>
      {unavailable ? <span className={cx(styles.status, hints.target)} tabIndex={0} role="img" aria-label="Unavailable"><TriangleAlert className="icon-sm" aria-hidden="true" /><span className={hints.hint} data-side="left" aria-hidden="true">Unavailable</span></span> : null}
      {layout === 1 ? <ModelLayerButton panel={panel} field={field} hintSide="left" /> : null}
    </Surface> : null}
  </aside>;
}
