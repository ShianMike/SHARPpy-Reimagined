// Layer visibility and ordering stay compact; opacity and provider options open together.
import { ChevronDown, ChevronRight, ChevronUp, Eye, Plus } from "lucide-react";
import { useId, useRef, useState, type ReactNode } from "react";
import { IconAction } from "../../ui/Controls";
import { useMapStore } from "../../stores/mapStore";
import type { LayerDescriptor, LayerState, OrderableLayerId } from "../layers/registry";
import { OpacityControl } from "./OpacityControl";
import styles from "./LayerPanel.module.css";

export interface LayerRowProps {
  layer: LayerDescriptor;
  state: LayerState;
  /** For a weather layer: its place from the top, and how many there are. */
  order?: { id: OrderableLayerId; fromTop: number; count: number } | undefined;
  children?: ReactNode;
}

export function LayerRow({ layer, state, order, children }: LayerRowProps): ReactNode {
  const { setLayerVisible, setLayerOpacity, moveLayer } = useMapStore.getState();
  const [expanded, setExpanded] = useState(false);
  const row = useRef<HTMLDivElement>(null);
  const detailsId = useId();
  const hasSettings = state.visible && (layer.opacityControl || children != null);
  return (
    <div ref={row} className={styles.row} data-visible={state.visible} data-testid={`layer-row-${layer.id}`}>
      <div className={styles.rowHead}>
        {order === undefined ? null : <span className={styles.rank} aria-hidden="true">{order.fromTop + 1}</span>}
        {hasSettings ? <button type="button" className={styles.layerDisclosure} aria-label={`${layer.label} settings`}
          aria-expanded={expanded} aria-controls={detailsId} onClick={() => { setExpanded(!expanded); }}>
          {expanded ? <ChevronDown className="icon-sm" aria-hidden="true" /> : <ChevronRight className="icon-sm" aria-hidden="true" />}<span>{layer.label}</span>
        </button> : <span className={styles.layerLabel}>{layer.label}</span>}
        {order === undefined ? null : <span className={styles.orderButtons} role="group" aria-label={`${layer.label} draw order`}>
          <IconAction icon={ChevronUp} size="sm" label={`Move ${layer.label} up`} disabled={order.fromTop === 0} onClick={() => { moveLayer(order.id, 1); }} />
          <IconAction icon={ChevronDown} size="sm" label={`Move ${layer.label} down`} disabled={order.fromTop === order.count - 1} onClick={() => { moveLayer(order.id, -1); }} />
        </span>}
        <IconAction className={styles.visibility} icon={state.visible ? Eye : Plus} size="sm" label={layer.label}
          role="switch" aria-checked={state.visible} aria-description={state.visible ? "Visible on map. Activate to hide." : "Hidden. Activate to add to the map."}
          onClick={() => {
            const focused = row.current?.contains(document.activeElement) === true;
            setLayerVisible(layer.id, !state.visible);
            setExpanded(false);
            if (focused) requestAnimationFrame(() => {
              // The switch moves between the active and available columns.
              if (row.current === null && document.activeElement === document.body)
                document.querySelector<HTMLElement>(`[data-testid="layer-row-${layer.id}"] [role="switch"]`)?.focus();
            });
          }}
        />
      </div>
      <div id={detailsId} className={styles.rowDetails} data-motion="disclosure" inert={!expanded || !state.visible} hidden={!expanded || !state.visible}>
        {expanded && state.visible ? <>
        {layer.opacityControl ? <OpacityControl label={`${layer.label} opacity`} value={state.opacity}
          onChange={(opacity) => { setLayerOpacity(layer.id, opacity); }} /> : null}
        {children}
        </> : null}
      </div>
    </div>
  );
}
