// Forecast-model sounding: a model, a recent or archived run, a forecast hour,
// and a point given by latitude and signed longitude.
import { useId, useState, type ReactNode, type SyntheticEvent } from "react";
import { Action } from "../../ui/Controls";
import { Notice } from "../../components/Notice";
import { useSoundingStore } from "../../stores/soundingStore";
import type { ForecastModel } from "../../types/soundings";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { modelRegion, useModelCatalog, useOpenSounding } from "./soundingQueries";
import { addHours, forecastLabel, resolveModelRun } from "./soundingTime";
import { SourceSelect } from "./SourceFields";
import { ModelRunFields } from "./ModelRunFields";
import { DownloadStatus } from "./DownloadStatus";
import styles from "./Sounding.module.css";

export function parseCoordinate(text: string, limit: number): number | null {
  const value = Number(text.trim());
  return !/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(text.trim()) || !Number.isFinite(value) || Math.abs(value) > limit ? null : value;
}

function ModelFields({ models }: { models: readonly ForecastModel[] }): ReactNode {
  const inputs = useSoundingStore((state) => state.model);
  const setModel = useSoundingStore((state) => state.setModel);
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
  const latitude = parseCoordinate(inputs.latitude, 90);
  const longitude = parseCoordinate(inputs.longitude, 180);
  const latInvalid = latitude === null && (touched.lat || inputs.latitude.trim() !== "");
  const lonInvalid = longitude === null && (touched.lon || inputs.longitude.trim() !== "");
  const bounds = model.bounds;
  const latitudeSouth = latitude === null ? inputs.latitude.trimStart().startsWith("-") : latitude < 0;
  const longitudeWest = longitude === null ? inputs.longitude.trimStart().startsWith("-") : longitude < 0;
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
        location: null,
      });
    }
  };

  return (
    <form className={styles.form} onSubmit={submit} aria-label="Model sounding">
      <SourceSelect label="Model" value={model.key} popup="list" searchLabel="Search models" options={models.map((item) => ({ value: item.key, label: item.label, group: modelRegion(item), detail: `${item.domain}${item.gridSpacingKm === null ? "" : ` · ${String(item.gridSpacingKm)} km`}` }))} onChange={(modelKey) => { edit({ modelKey, runTime: null, forecastHour: null }); }} />
      <ModelRunFields model={model} run={run} onChange={(runTime) => { edit({ modelKey: model.key, runTime }); }} />
      <SourceSelect label="Forecast" value={hour} popup="grid" options={hours.map((item) => ({ value: item, label: forecastLabel(item), group: `Valid ${addHours(run, item).slice(0, 10)} · UTC`, detail: `${addHours(run, item).slice(11, 13)}Z` }))} onChange={(forecastHour) => { edit({ forecastHour }); }} />
      <div className={styles.pair}>
        <label className={styles.field} htmlFor={ids.lat}>
          <span className={styles.coordinateLabel}>Latitude<small>{latitudeSouth ? "°S" : "°N"}</small></span>
          <input
            id={ids.lat}
            className={styles.input}
            inputMode="decimal"
            aria-label="Latitude"
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
          <span className={styles.coordinateLabel}>Longitude<small>{longitudeWest ? "°W" : "°E"}</small></span>
          <input
            id={ids.lon}
            className={styles.input}
            inputMode="decimal"
            aria-label="Longitude"
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
      {!latInvalid && !lonInvalid ? null : <Notice tone="danger" title="Check coordinates" detail="Enter decimal degrees." role="alert">
        <dl className={styles.coordinateRanges}>
          {!latInvalid ? null : <div id={`${ids.lat}-error`}><dt>Latitude</dt><dd>−90° to 90°</dd></div>}
          {!lonInvalid ? null : <div id={`${ids.lon}-error`}><dt>Longitude</dt><dd>−180° to 180°</dd></div>}
        </dl>
      </Notice>}
      <p className={styles.hint}>Negative = south / west</p>
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
