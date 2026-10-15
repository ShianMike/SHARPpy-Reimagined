// The graphics probe results and the map renderer's start-up state
// (DESIGN-frontend F6, F8 "Settings"). This is the one place the renderer
// error's developer message is shown, besides the console.

import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useGraphicsStore } from "../../stores/graphicsStore";
import type { GraphicsCapabilities } from "../../types/graphics";
import styles from "./SettingsView.module.css";

function yesNo(value: boolean | null): string {
  if (value === null) {
    return "Not available";
  }
  return value ? "Yes" : "No";
}

/** The rows of the table: a label and a display value. */
export function capabilityRows(capabilities: GraphicsCapabilities | null): [string, string][] {
  if (capabilities === null) {
    return [["Graphics probe", "Not run"]];
  }
  const { extensions, viewport } = capabilities;
  return [
    ["WebGL2", yesNo(capabilities.webgl2)],
    ["Major performance caveat", yesNo(capabilities.majorPerformanceCaveat)],
    ["Maximum texture size", capabilities.maxTextureSize === null ? "Not available" : String(capabilities.maxTextureSize)],
    ["Float colour buffers", yesNo(extensions.colorBufferFloat)],
    ["Float texture filtering", yesNo(extensions.textureFloatLinear)],
    ["Float blending", yesNo(extensions.floatBlend)],
    ["Device pixel ratio", String(capabilities.devicePixelRatio)],
    ["Viewport", `${String(viewport.width)} × ${String(viewport.height)} CSS pixels`],
    ["Renderer hint", capabilities.rendererHint ?? "Not exposed"],
    ["Renderer class", capabilities.rendererClass],
  ];
}

const RENDERER_STATES = { pending: "Starting", ready: "Ready", failed: "Failed" } as const;

export function CapabilityTable(): ReactNode {
  const capabilities = useGraphicsStore((state) => state.capabilities);
  const renderer = useGraphicsStore((state) => state.renderer);
  const rendererError = useGraphicsStore((state) => state.rendererError);
  const mode = useGraphicsStore((state) => state.effective.mode);
  const rows = capabilityRows(capabilities);
  rows.push(["Map renderer", mode === "reduced" ? "Off (no WebGL2)" : RENDERER_STATES[renderer]]);
  if (rendererError !== null) {
    rows.push(["Renderer error", `${rendererError.kind}: ${rendererError.message}`]);
  }
  return (
    <table className={styles.table} data-testid="capability-table">
      <caption className={styles.caption}>Graphics capabilities</caption>
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}>
            <th scope="row">{label}</th>
            <td className={cx(label === "Renderer hint" || label === "Renderer error" ? styles.wrap : "tabular")}>
              {value}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
