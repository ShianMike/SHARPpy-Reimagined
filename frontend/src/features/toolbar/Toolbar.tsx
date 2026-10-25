import type { ReactNode } from "react";
import { BrandMark } from "../../ui/BrandMark";
import { Surface } from "../../ui/Surface";
import { cx } from "../../lib/cx";
import { StationSearch } from "../search/StationSearch";
import { ForecastControls } from "./ForecastControls";
import { LiveRadarControls } from "../tracking/LiveRadarControls";
import styles from "./Toolbar.module.css";

export const APP_TITLE_ID = "app-title";
export const PRODUCT_NAME = "SHARPpy Reimagined";
export interface ToolbarProps { className?: string | undefined; isMap: boolean; live?: boolean; radarScansRef?: ((element: HTMLDivElement | null) => void) | undefined; children: ReactNode; }

export function Toolbar({ className, isMap, live = false, radarScansRef, children }: ToolbarProps): ReactNode {
  if (!isMap) return null;
  return <header aria-label={live ? "Live tracking controls" : undefined} aria-labelledby={live ? undefined : APP_TITLE_ID} className={cx(styles.toolbar, className)} data-testid="toolbar">
    <Surface className={styles.brand} data-motion="fade" elevation="chrome">
      <BrandMark className={styles.brandMark} />
      <span id={APP_TITLE_ID} className={styles.title} data-testid="product-name">SHARPpy <span>Reimagined</span></span>
    </Surface>
    {live ? <LiveRadarControls className={styles.forecast} scansRef={radarScansRef} /> : <ForecastControls className={styles.forecast} />}
    <Surface className={styles.actions} data-motion="fade" elevation="chrome" role="group" aria-label="Map actions">
      <StationSearch />
      {children}
    </Surface>
  </header>;
}
