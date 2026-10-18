// Forecast-model sounding: a model, a recent or archived run, a forecast hour,
// and a point (the selected station, or typed coordinates). Models, runs, and
// hours all come from the backend's model list; the valid time shown is the
// run plus the forecast hour.
import { useId, type ReactNode, type SyntheticEvent } from "react";
import { Action, Toggle } from "../../ui/Controls";
import { useSoundingStore } from "../../stores/soundingStore";
import { useSelectedStation } from "../../stations/stationIndex";
import type { ForecastModel } from "../../types/soundings";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { useModelCatalog, useOpenSounding } from "./soundingQueries";
import { addHours, forecastLabel, formatUtc, resolveModelRun } from "./soundingTime";
import { SourceSelect } from "./SourceFields";
import { DownloadStatus } from "./DownloadStatus";
import styles from "./Sounding.module.css";

export function parseCoordinate(text: string, limit: number): number | null {
  const value = Number(text.trim());
  return text.trim() === "" || !Number.isFinite(value) || Math.abs(value) > limit ? null : value;
}

function ModelFields({ models }: { models: readonly ForecastModel[] }): ReactNode {
  const inputs = useSoundingStore((state) => state.model);
  const setModel = useSoundingStore((state) => state.setModel);
  const selected = useSelectedStation();
  const open = useOpenSounding();
  const request = open.isPending && open.variables.kind === "model" ? open.variables : null;
  const ids = { lat: useId(), lon: useId() };
  /** An edit clears a failure notice: it described the request before the edit. */
  const edit = (patch: Parameters<typeof setModel>[0]): void => {
    if (open.isError) {
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
      : model.defaultForecastHour;
  const station = selected.state === "found" ? selected.station : null;
  const atStation = inputs.atStation && station !== null;
  const latitude = atStation ? station.latitude : parseCoordinate(inputs.latitude, 90);
  const longitude = atStation ? station.longitude : parseCoordinate(inputs.longitude, 180);
  const valid = latitude !== null && longitude !== null;
  const submit = (event?: SyntheticEvent): void => {
    event?.preventDefault();
    if (latitude !== null && longitude !== null && !open.isPending) {
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
      <SourceSelect label="Model" value={model.key} options={models.map((item) => ({ value: item.key, label: item.label, detail: `${item.domain}${item.gridSpacingKm === null ? "" : ` · ${String(item.gridSpacingKm)} km`}` }))} onChange={(modelKey) => { edit({ modelKey, runTime: null, forecastHour: null }); }} />
      <p className={styles.hint}>{`${model.domain}${model.gridSpacingKm === null ? "" : `, ${String(model.gridSpacingKm)} km grid`}`}</p>
      <SourceSelect label="Run" value={run} options={newestFirst.map((item) => ({ value: item, label: formatUtc(item), detail: item > model.suggestedRun ? "May still be publishing" : item === model.suggestedRun ? "Latest suggested" : "Earlier cycle" }))} onChange={(runTime) => { edit({ modelKey: model.key, runTime }); }} />
      <SourceSelect label="Forecast" value={hour} options={hours.map((item) => ({ value: item, label: forecastLabel(item), detail: `Valid ${formatUtc(addHours(run, item))}` }))} onChange={(forecastHour) => { edit({ forecastHour }); }} />
      <p className={styles.hint}>Valid {formatUtc(addHours(run, hour))}</p>
      <Toggle label={station === null ? "Use selected station" : `At station ${station.id}`} checked={atStation} disabled={station === null} onChange={(atStation) => { edit({ atStation }); }} />
      {atStation ? null : (
        <div className={styles.pair}>
          <label className={styles.field} htmlFor={ids.lat}>
            Latitude
            <input
              id={ids.lat}
              className={styles.input}
              inputMode="decimal"
              aria-label="Latitude, degrees north"
              placeholder="Latitude"
              value={inputs.latitude}
              onChange={(event) => {
                edit({ latitude: event.target.value });
              }}
            />
          </label>
          <label className={styles.field} htmlFor={ids.lon}>
            Longitude
            <input
              id={ids.lon}
              className={styles.input}
              inputMode="decimal"
              aria-label="Longitude, degrees east"
              placeholder="Longitude"
              value={inputs.longitude}
              onChange={(event) => {
                edit({ longitude: event.target.value });
              }}
            />
          </label>
        </div>
      )}
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
        {open.isPending ? <Action onClick={open.cancel}>Stop download</Action> : null}
        {request !== null ? <DownloadStatus
          label={`${models.find((item) => item.key === request.model)?.label ?? model.label} ${forecastLabel(request.forecastHour)}`}
          progress={open.progress}
        /> : null}
      </div>
      {open.isPending ? <p className={styles.hint}>Model data downloads can take a minute or more.</p> : null}
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
  return <ModelFields models={catalog.data.models} />;
}
