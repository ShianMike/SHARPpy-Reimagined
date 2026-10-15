import { useMutation } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { parseCoordinate } from "./ModelForm";
import { useOpenSounding } from "./soundingQueries";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { SourceSelect } from "./SourceFields";
import { formatUtc } from "./soundingTime";
import styles from "./Sounding.module.css";

export function WrfForm(): ReactNode {
  const client = useBackendClient();
  const open = useOpenSounding();
  const [path, setPath] = useState("");
  const [time, setTime] = useState("");
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const inspect = useMutation({
    mutationFn: () => client.inspectWrf(path.trim()),
    onSuccess: (file) => {
      setTime(file.times[0] ?? "");
      setLatitude(String(file.latitude));
      setLongitude(String(file.longitude));
    },
  });
  const lat = parseCoordinate(latitude, 90);
  const lon = parseCoordinate(longitude, 180);
  const busy = inspect.isPending || open.isPending;
  const valid = inspect.data !== undefined && inspect.data.times.includes(time) && lat !== null && lon !== null;
  const submit = (): void => {
    if (valid && !busy) {
      open.mutate({ kind: "wrf", filePath: inspect.data.filePath, time, latitude: lat, longitude: lon });
    }
  };
  return <form className={styles.form} aria-label="Raw WRF sounding" onSubmit={(event) => { event.preventDefault(); submit(); }}>
    <label className={styles.field}>WRF file path
      <input className={styles.input} value={path} placeholder="Full path to wrfout or NetCDF file" disabled={busy}
        onChange={(event) => { setPath(event.target.value); inspect.reset(); open.reset(); }} />
    </label>
    <p className={styles.hint}>Paste the full local path. Large datasets stay on disk; only the selected point is extracted.</p>
    <Action disabled={path.trim() === "" || busy} onClick={() => { inspect.mutate(); }}>Read WRF file</Action>
    {inspect.data === undefined ? null : <>
      <p className={styles.hint}>{inspect.data.fileName}</p>
      <SourceSelect label="Forecast time (UTC)" value={time} options={inspect.data.times.map((value) => ({ value, label: formatUtc(value) }))}
        disabled={busy} onChange={(value) => { setTime(value); open.reset(); }} />
      <div className={styles.pair}>
        <label className={styles.field}>Latitude, degrees north
          <input className={styles.input} inputMode="decimal" value={latitude} disabled={busy}
            onChange={(event) => { setLatitude(event.target.value); open.reset(); }} />
        </label>
        <label className={styles.field}>Longitude, degrees east
          <input className={styles.input} inputMode="decimal" value={longitude} disabled={busy}
            onChange={(event) => { setLongitude(event.target.value); open.reset(); }} />
        </label>
      </div>
      <p className={styles.hint}>Starts at the domain center. Enter a point inside this dataset’s grid.</p>
      <Action tone="accent" disabled={!valid || busy} onClick={submit}>Open WRF sounding</Action>
    </>}
    {busy ? <span className={styles.busy} role="status">{inspect.isPending ? "Reading WRF times and domain" : "Extracting WRF point sounding"}</span> : null}
    {inspect.isError ? <SoundingErrorNotice error={inspect.error} operation="inspect_wrf" onRetry={() => { inspect.mutate(); }} busy={busy} testId="wrf-inspect-error" /> : null}
    {open.isError ? <SoundingErrorNotice error={open.error} operation="open_sounding" onRetry={submit} busy={busy} testId="wrf-open-error" /> : null}
  </form>;
}
