import type { ReactNode } from "react";
import { Eye, EyeOff, TriangleAlert } from "lucide-react";
import { useForecastContext } from "../../features/forecast/useForecast";
import { cx } from "../../lib/cx";
import { displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { IconAction } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import type { PanelField } from "../panelField";
import { FieldLegend } from "./FieldLegend";
import styles from "./ParameterScale.module.css";
import hints from "../../ui/HoverHint.module.css";

export function ParameterScale({ panel, field, className }: { panel: number; field: PanelField; className?: string | undefined }): ReactNode {
  const units = useUiStore((state) => state.units);
  const layout = useMapStore((state) => state.panelLayout);
  const context = useForecastContext();
  const { product, blocked, frame } = field;
  if (product === null || context?.fieldsAvailable !== true) return null;
  const visible = blocked !== "hidden";
  const status = !visible ? null : frame.isError ? "Unavailable" : frame.isFetching ? "Loading…" : null;
  const loading = visible && frame.isFetching && !frame.isError;
  return <Surface as="aside" data-motion="fade" aria-label="Parameter scale" aria-description={`${product.label} · ${displayUnit(product.palette.units, units, product.palette.display) || "Unitless"}${layout > 1 ? ` · Panel ${String(panel + 1)}` : ""}`} className={cx(styles.panel, className)} data-testid="parameter-scale">
    <FieldLegend palette={product.palette} vertical />
    {status === null || loading ? null : <span className={cx(styles.status, hints.target)} tabIndex={0} role="img" aria-label={status}><TriangleAlert className="icon-sm" aria-hidden="true" /><span className={hints.hint} data-side="left" aria-hidden="true">{status}</span></span>}
    <IconAction className={styles.visibility} size="sm" icon={visible ? Eye : EyeOff} loading={loading} label="Model layer" aria-pressed={visible} aria-description={`${loading ? "Loading. " : ""}${visible ? "Layer on. Activate to hide." : "Layer off. Activate to show."}`} hintSide="left" hintText={`Model layer${loading ? " · Loading…" : ""}`}
      onClick={() => {
        const state = useMapStore.getState();
        state.setActivePanel(panel);
        state.setLayerVisible("model-field", !panelLayers(state, panel)["model-field"].visible);
      }} />
  </Surface>;
}
