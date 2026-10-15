import { useId, useState, type ReactNode, type SyntheticEvent } from "react";
import { Action, Toggle } from "../../ui/Controls";
import { useSoundingStore } from "../../stores/soundingStore";
import { useSelectedStation } from "../../stations/stationIndex";
import { parseCoordinate } from "./ModelForm";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { useOpenSounding } from "./soundingQueries";
import { formatUtc, releaseTime, utcDate } from "./soundingTime";
import { SourceDate, SourceSelect } from "./SourceFields";
import styles from "./Sounding.module.css";

const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

export function Era5Form(): ReactNode {
  const inputs = useSoundingStore((state) => state.era5);
  const setEra5 = useSoundingStore((state) => state.setEra5);
  const selected = useSelectedStation();
  const open = useOpenSounding();
  const [now] = useState(() => new Date());
  const ids = { lat: useId(), lon: useId() };
  const edit = (patch: Parameters<typeof setEra5>[0]): void => {
    if (open.isError) open.reset();
    setEra5(patch);
  };
  const station = selected.state === "found" ? selected.station : null;
  const atStation = inputs.atStation && station !== null;
  const latitude = atStation ? station.latitude : parseCoordinate(inputs.latitude, 90);
  const longitude = atStation ? station.longitude : parseCoordinate(inputs.longitude, 180);
  const time = releaseTime(inputs.date, inputs.hour);
  const valid = time !== null && time >= "1940-01-01T00:00:00Z" && Date.parse(time) <= now.getTime() && latitude !== null && longitude !== null;
  const submit = (event?: SyntheticEvent): void => {
    event?.preventDefault();
    if (valid && !open.isPending) {
      open.mutate({ kind: "era5", time, latitude, longitude, location: atStation ? station.id : null });
    }
  };

  return (
    <form className={styles.form} onSubmit={submit} aria-label="ERA5 sounding">
      <p className={styles.hint}>Global reanalysis on a 0.25° grid, hourly from 1940. Recent data usually arrives about five days late.</p>
      <SourceDate value={inputs.date} min="1940-01-01" max={utcDate(now)} onChange={(date) => { edit({ date }); }} />
      <SourceSelect label="Analysis hour (UTC)" value={inputs.hour} options={HOURS.map((hour) => ({ value: hour, label: `${String(hour).padStart(2, "0")}Z` }))} onChange={(hour) => { edit({ hour }); }} />
      <Toggle label={station === null ? "Use selected station" : `At station ${station.id}`} checked={atStation} disabled={station === null} onChange={(atStation) => { edit({ atStation }); }} />
      {atStation ? null : (
        <div className={styles.pair}>
          <label className={styles.field} htmlFor={ids.lat}>
            Latitude
            <input id={ids.lat} className={styles.input} inputMode="decimal" aria-label="Latitude, degrees north" placeholder="Latitude" value={inputs.latitude} onChange={(event) => { edit({ latitude: event.target.value }); }} />
          </label>
          <label className={styles.field} htmlFor={ids.lon}>
            Longitude
            <input id={ids.lon} className={styles.input} inputMode="decimal" aria-label="Longitude, degrees east" placeholder="Longitude" value={inputs.longitude} onChange={(event) => { edit({ longitude: event.target.value }); }} />
          </label>
        </div>
      )}
      <p className={styles.hint}>Uses your backend’s Copernicus CDS profile. <a href="https://cds.climate.copernicus.eu/how-to-api" target="_blank" rel="noreferrer">CDS setup</a></p>
      <div className={styles.actions}>
        <Action tone="accent" disabled={!valid || open.isPending} onClick={() => { submit(); }} data-testid="open-era5">Open sounding</Action>
        {open.isPending ? <span className={styles.busy}>{`Retrieving ERA5 ${formatUtc(time)}`}</span> : null}
      </div>
      {open.isPending ? <p className={styles.hint}>Copernicus queues requests; a download can take several minutes.</p> : null}
      {open.isError ? <SoundingErrorNotice error={open.error} operation="open_sounding" onRetry={() => { submit(); }} busy={open.isPending} testId="era5-error" /> : null}
    </form>
  );
}
