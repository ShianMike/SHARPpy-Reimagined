// Forecast-model sounding: a model, a recent or archived run, a forecast hour,
// and a point (the selected station, or typed coordinates). Models, runs, and
// hours all come from the backend's model list; the valid time shown is the
// run plus the forecast hour.
import { useId, useState, type ReactNode, type SyntheticEvent } from "react";
import { Action, Toggle } from "../../ui/Controls";
import { Notice } from "../../components/Notice";
import { useSoundingStore } from "../../stores/soundingStore";
import { useSelectedStation } from "../../stations/stationIndex";
import type { ForecastModel } from "../../types/soundings";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { modelRegion, useModelCatalog, useOpenSounding } from "./soundingQueries";
import { addHours, forecastLabel, formatUtc, resolveModelRun } from "./soundingTime";
import { SourceSelect } from "./SourceFields";
import { DownloadStatus } from "./DownloadStatus";
import styles from "./Sounding.module.css";

export function parseCoordinate(text: string, limit: number): number | null {
  const value = Number(text.trim());
  return !/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(text.trim()) || !Number.isFinite(value) || Math.abs(value) > limit ? null : value;
}

function ModelFields({ models }: { models: readonly ForecastModel[] }): ReactNode {
  const inputs = useSoundingStore((state) => state.model);
  const setModel = useSoundingStore((state) => state.setModel);
  const selected = useSelectedStation();
  const open = useOpenSounding();
  const request = open.isPending && open.variables.kind === "model" ? open.variables : null;
  const ids = { lat: useId(), lon: useId() };
  const [touched, setTouched] = useState({ lat: false, lon: false });
  /** An edit clears a failure notice: it described the request before the edit. */
  const edit = (patch: Parameters<typeof setModel>[0]): void => {
    if (open.isError || open.cancelled) {
      open.reset();
    }
    setModel(patch);
  };

  const model = models.find((item) => item.key === inputs.modelKey) ?? models[0];
  if (model === undefined) {
    return <p className={styles.empty}>The backend lists no forecast models.</p>;
  }
  const run = resolveModelRun(model, inputs.modelKey === null || inputs.modelKey === model.key ? inputs.runTime : null);
  const hours = model.forecastHours.filter((item) => item <= (model.maxForecastHours[new Date(run).getUTCHours()] ?? Infinity));
  const hour =
    inputs.forecastHour !== null && hours.includes(inputs.forecastHour)
      ? inputs.forecastHour
      : hours.includes(model.defaultForecastHour) ? model.defaultForecastHour : hours[0] ?? 0;
  const station = selected.state === "found" ? selected.station : null;
  const atStation = inputs.atStation && station !== null;
  const latitude = atStation ? station.latitude : parseCoordinate(inputs.latitude, 90);
  const longitude = atStation ? station.longitude : parseCoordinate(inputs.longitude, 180);
  const latInvalid = !atStation && latitude === null && (touched.lat || inputs.latitude.trim() !== "");
  const lonInvalid = !atStation && longitude === null && (touched.lon || inputs.longitude.trim() !== "");
  const bounds = model.bounds;
  const outside = latitude !== null && longitude !== null && (latitude < bounds.south || latitude > bounds.north ||
    (bounds.west <= bounds.east ? longitude < bounds.west || longitude > bounds.east : longitude < bounds.west && longitude > bounds.east));
  const valid = latitude !== null && longitude !== null && !outside && hours.includes(hour);
  const submit = (event?: SyntheticEvent): void => {
    event?.preventDefault();
    if (valid && !open.isPending) {
      open.mutate({
        kind: "model",
        model: model.key,
        runTime: run,
        forecastHour: hour,
        latitude,
        longitude,
        location: atStation ? station.id : null,
      });
    }
  };
  const newestFirst = model.recentRuns.includes(run) ? model.recentRuns : [...model.recentRuns, run];

  return (
    <form className={styles.form} onSubmit={submit} aria-label="Model sounding">
      <SourceSelect label="Model" value={model.key} options={models.map((item) => ({ value: item.key, label: item.label, group: modelRegion(item), detail: `${item.domain}${item.gridSpacingKm === null ? "" : ` · ${String(item.gridSpacingKm)} km`}` }))} onChange={(modelKey) => { edit({ modelKey, runTime: null, forecastHour: null }); }} />
      <p className={styles.hint}>{`${model.domain}${model.gridSpacingKm === null ? "" : `, ${String(model.gridSpacingKm)} km grid`}`}</p>
      <SourceSelect label="Run" value={run} options={newestFirst.map((item) => ({ value: item, label: formatUtc(item), detail: item > model.suggestedRun ? "May still be publishing" : item === model.suggestedRun ? "Latest suggested" : "Earlier cycle" }))} onChange={(runTime) => { edit({ modelKey: model.key, runTime }); }} />
      <SourceSelect label="Forecast" value={hour} options={hours.map((item) => ({ value: item, label: forecastLabel(item), detail: `Valid ${formatUtc(addHours(run, item))}` }))} onChange={(forecastHour) => { edit({ forecastHour }); }} />
      <p className={styles.hint}>Valid {formatUtc(addHours(run, hour))}</p>
      <Toggle label={station === null ? "Use selected station" : `At station ${station.id}`} checked={atStation} disabled={station === null} onChange={(atStation) => { edit({ atStation }); }} />
      {atStation ? null : (
        <div className={styles.pair}>
          <label className={styles.field} htmlFor={ids.lat}>
            <span className={styles.coordinateLabel}>Latitude<small>°N</small></span>
            <input
              id={ids.lat}
              className={styles.input}
              inputMode="decimal"
              aria-label="Latitude, degrees north"
              aria-required="true"
              aria-invalid={latInvalid}
              aria-describedby={latInvalid ? `${ids.lat}-error` : undefined}
              placeholder="35.2"
              value={inputs.latitude}
              onChange={(event) => {
                edit({ latitude: event.target.value });
              }}
              onBlur={() => { setTouched((state) => ({ ...state, lat: true })); }}
            />
          </label>
          <label className={styles.field} htmlFor={ids.lon}>
            <span className={styles.coordinateLabel}>Longitude<small>°E</small></span>
            <input
              id={ids.lon}
              className={styles.input}
              inputMode="decimal"
              aria-label="Longitude, degrees east"
              aria-required="true"
              aria-invalid={lonInvalid}
              aria-describedby={lonInvalid ? `${ids.lon}-error` : undefined}
              placeholder="-97.4"
              value={inputs.longitude}
              onChange={(event) => {
                edit({ longitude: event.target.value });
              }}
              onBlur={() => { setTouched((state) => ({ ...state, lon: true })); }}
            />
          </label>
        </div>
      )}
      {!latInvalid && !lonInvalid ? null : <Notice tone="danger" title="Check coordinates" detail="Enter decimal degrees." role="alert">
        <dl className={styles.coordinateRanges}>
          {!latInvalid ? null : <div id={`${ids.lat}-error`}><dt>Latitude</dt><dd>−90° to 90°</dd></div>}
          {!lonInvalid ? null : <div id={`${ids.lon}-error`}><dt>Longitude</dt><dd>−180° to 180°</dd></div>}
        </dl>
      </Notice>}
      {atStation ? null : <p className={styles.hint}>Negative = south / west</p>}
      {outside ? <Notice tone="warning" title="Outside model coverage" message={`${model.label} covers ${model.domain}. Choose a point inside its map outline.`} role="alert" /> : null}
      <div className={styles.actions}>
        <Action
          tone="accent"
          disabled={!valid || open.isPending}
          onClick={() => {
            submit();
          }}
          data-testid="open-model"
        >
          Open sounding
        </Action>
        {open.isPending ? <Action onClick={open.cancel}>Cancel request</Action> : null}
        {request !== null ? <DownloadStatus
          label={`${models.find((item) => item.key === request.model)?.label ?? model.label} ${forecastLabel(request.forecastHour)}`}
          progress={open.progress}
        /> : null}
      </div>
      {open.cancelled ? <p className={styles.hint} role="status">Request cancelled. Your inputs and open soundings have been kept.</p> : null}
      {open.isError ? (
        <SoundingErrorNotice
          error={open.error}
          operation="open_sounding"
          onRetry={() => {
            submit();
          }}
          busy={open.isPending}
          testId="model-error"
        />
      ) : null}
    </form>
  );
}

export function ModelForm(): ReactNode {
  const catalog = useModelCatalog();
  if (catalog.isError) {
    return (
      <SoundingErrorNotice
        error={catalog.error}
        operation="get_model_catalog"
        onRetry={() => {
          void catalog.refetch();
        }}
        busy={catalog.isFetching}
        testId="models-error"
      />
    );
  }
  if (catalog.data === undefined) {
    return <p className={styles.empty}>Loading the forecast models</p>;
  }
  return <ModelFields models={catalog.data.models.filter((model) => model.soundingsAvailable)} />;
}
