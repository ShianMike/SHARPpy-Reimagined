import type { ReactNode } from "react";
import { BrandMark } from "../../ui/BrandMark";
import { Surface } from "../../ui/Surface";
import { cx } from "../../lib/cx";
import { StationSearch } from "../search/StationSearch";
import { ForecastControls } from "./ForecastControls";
import styles from "./Toolbar.module.css";

export const APP_TITLE_ID = "app-title";
export const PRODUCT_NAME = "SHARPpy Reimagined";
export interface ToolbarProps { className?: string | undefined; isMap: boolean; children: ReactNode; }

export function Toolbar({ className, isMap, children }: ToolbarProps): ReactNode {
  if (!isMap) return null;
  return <header aria-labelledby={APP_TITLE_ID} className={cx(styles.toolbar, className)} data-testid="toolbar">
    <Surface className={styles.brand} data-motion="fade" elevation="chrome">
      <BrandMark className={styles.brandMark} />
      <span id={APP_TITLE_ID} className={styles.title} data-testid="product-name">SHARPpy <span>Reimagined</span></span>
    </Surface>
    <ForecastControls className={styles.forecast} />
    <Surface className={styles.actions} data-motion="fade" elevation="chrome" role="group" aria-label="Map actions">
      <StationSearch />
      {children}
    </Surface>
  </header>;
}
