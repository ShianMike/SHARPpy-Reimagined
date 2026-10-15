// One layer in the layer panel: its switch, Move up and Move down for the
// weather layers (Phase 6 layer ordering), its opacity slider where the
// descriptor allows one, and, while it is on, its own controls and status.

import { ChevronDown, ChevronUp, SlidersHorizontal } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { IconAction } from "../../ui/Controls";
import { Toggle } from "../../ui/Controls";
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
  const detailsId = useId();
  const hasSettings = state.visible && (order !== undefined || layer.opacityControl || children != null);
  return (
    <div className={styles.row} data-visible={state.visible} data-testid={`layer-row-${layer.id}`}>
      <div className={styles.rowHead}>
        {order === undefined ? null : <span className={styles.rank} aria-hidden="true">{order.fromTop + 1}</span>}
        <Toggle
          label={layer.label}
          checked={state.visible}
          onChange={(visible) => {
            setLayerVisible(layer.id, visible);
            setExpanded(visible);
          }}
        />
        {hasSettings ? (
          <IconAction
            icon={SlidersHorizontal}
            size="sm"
            label={`${layer.label} settings`}
            aria-expanded={expanded}
            aria-controls={detailsId}
            onClick={() => { setExpanded(!expanded); }}
          />
        ) : null}
      </div>
      {state.visible && layer.opacityControl ? <p className={styles.rowSummary}>{Math.round(state.opacity * 100)}% opacity</p> : null}
      <div id={detailsId} className={styles.rowDetails} data-motion="disclosure" inert={!expanded || !state.visible} hidden={!expanded || !state.visible}>
        {order === undefined ? null : (
          <div className={styles.order}>
            <span>Draw order · {order.fromTop + 1} of {order.count}</span>
            <span className={styles.orderButtons}>
              <IconAction
                icon={ChevronUp}
                size="sm"
                label={`Move ${layer.label} up`}
                disabled={order.fromTop === 0}
                onClick={() => {
                  moveLayer(order.id, 1);
                }}
              />
              <IconAction
                icon={ChevronDown}
                size="sm"
                label={`Move ${layer.label} down`}
                disabled={order.fromTop === order.count - 1}
                onClick={() => {
                  moveLayer(order.id, -1);
                }}
              />
              </span>
          </div>
        )}
        {layer.opacityControl && state.visible ? (
          <OpacityControl
            label={`${layer.label} opacity`}
            value={state.opacity}
            disabled={!state.visible}
            onChange={(opacity) => {
              setLayerOpacity(layer.id, opacity);
            }}
          />
        ) : null}
        {state.visible ? children : null}
      </div>
    </div>
  );
}
