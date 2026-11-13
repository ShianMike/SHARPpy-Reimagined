import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import styles from "./RadarCredits.module.css";

export function RadarCredits({ className }: { className?: string | undefined }): ReactNode {
  return (
    <aside className={cx(styles.credits, className)} aria-label="Radar credits" data-testid="radar-credits">
      <p>
        Radar data from{" "}
        <a href="https://www.weather.gov/" target="_blank" rel="noreferrer">NOAA / NWS</a>,{" "}
        <a href="https://www.bom.gov.au/" target="_blank" rel="noreferrer">Australia’s Bureau of Meteorology</a>,{" "}
        <a href="https://www.dwd.de/" target="_blank" rel="noreferrer">Deutscher Wetterdienst</a>, and{" "}
        <a href="https://www.meteofrance.fr/" target="_blank" rel="noreferrer">Météo-France</a>.{" "}
        European scans via{" "}
        <a href="https://eumetnet.github.io/openradardata-documentation/" target="_blank" rel="noreferrer">EUMETNET OPERA</a>;{" "}
        Australian and additional French scans via{" "}
        <a href="https://mrrl.net/" target="_blank" rel="noreferrer">MRRL InterRAD</a>{" "}
        (<a href="https://feeds.mrrl.net/ATTRIBUTION.txt" target="_blank" rel="noreferrer">source credits</a>).{" "}
        OPERA licences vary; French data under{" "}
        <a href="https://www.etalab.gouv.fr/wp-content/uploads/2017/04/ETALAB-Licence-Ouverte-v2.0.pdf" target="_blank" rel="noreferrer">Licence Ouverte 2.0</a>.{" "}
        Rendered by SHARPpy Reimagined.
      </p>
    </aside>
  );
}
