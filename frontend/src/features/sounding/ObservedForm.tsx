// Observed sounding: a station, a UTC date and launch hour, and a provider.
// The station defaults to the map selection. Which reports exist is the
// backend's answer: the form never guesses availability.
import { useEffect, useId, type ReactNode, type SyntheticEvent } from "react";
import { Action } from "../../ui/Controls";
import { useSoundingStore } from "../../stores/soundingStore";
import { stationIndex, useSelectedStation } from "../../stations/stationIndex";
import { displayName } from "../../stations/stationFormat";
import { useStationCatalog } from "../../stations/stationQueries";
import { OBSERVED_PROVIDERS, type ObservedProvider } from "../../types/soundings";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { useOpenSounding } from "./soundingQueries";
import { formatUtc, RELEASE_HOURS, releaseTime } from "./soundingTime";
import { SourceDate, SourceSelect } from "./SourceFields";
import styles from "./Sounding.module.css";

const PROVIDER_LABELS: Readonly<Record<ObservedProvider, string>> = {
  auto: "Automatic (UWyo, then IEM)",
  uwyo: "University of Wyoming",
  iem: "Iowa Environmental Mesonet",
  igra: "NOAA IGRA archive",
};

export function ObservedForm(): ReactNode {
  const inputs = useSoundingStore((state) => state.observed);
  const setObserved = useSoundingStore((state) => state.setObserved);
  const selected = useSelectedStation();
  const catalog = useStationCatalog();
  const open = useOpenSounding();
  const stationInput = useId();
  /** An edit clears a failure notice: it described the request before the edit. */
  const edit = (patch: Parameters<typeof setObserved>[0]): void => {
    if (open.isError) {
      open.reset();
    }
    setObserved(patch);
  };

  const selectedId = selected.state === "found" ? selected.station.id : null;
  useEffect(() => {
    if (selectedId !== null) {
      setObserved({ stationId: selectedId });
    }
  }, [selectedId, setObserved]);

  const stationId = inputs.stationId.trim();
  const known = catalog.data === undefined ? undefined : stationIndex(catalog.data.stations).get(stationId);
  const time = releaseTime(inputs.date, inputs.hour);
  const valid = stationId.length >= 1 && stationId.length <= 16 && time !== null;
  const submit = (event?: SyntheticEvent): void => {
    event?.preventDefault();
    if (valid && !open.isPending) {
      open.mutate({ kind: "observed", stationId, time, provider: inputs.provider });
    }
  };

  return (
    <form className={styles.form} onSubmit={submit} aria-label="Observed sounding">
      <label className={styles.field} htmlFor={stationInput}>
        Station
        <input
          id={stationInput}
          className={styles.input}
          value={inputs.stationId}
          maxLength={16}
          placeholder="WMO id, e.g. 72357"
          spellCheck={false}
          onChange={(event) => {
            edit({ stationId: event.target.value });
          }}
        />
      </label>
      <p className={styles.hint} data-testid="observed-station-name">
        {known !== undefined
          ? displayName(known.name)
          : stationId === ""
            ? "Pick a station on the map or type its id."
            : "Not in the bundled list; the providers may still know it."}
      </p>
      <SourceDate value={inputs.date} onChange={(date) => { edit({ date }); }} />
      <SourceSelect label="Launch" value={inputs.hour} options={RELEASE_HOURS.map((hour) => ({ value: hour, label: `${String(hour).padStart(2, "0")}Z`, detail: hour === 0 || hour === 12 ? "Standard launch" : "Additional launch" }))} onChange={(hour) => { edit({ hour }); }} />
      <SourceSelect label="Provider" value={inputs.provider} options={OBSERVED_PROVIDERS.map((provider) => ({ value: provider, label: PROVIDER_LABELS[provider] }))} onChange={(provider) => { edit({ provider }); }} />
      <div className={styles.actions}>
        <Action
          tone="accent"
          disabled={!valid || open.isPending}
          onClick={() => {
            submit();
          }}
          data-testid="open-observed"
        >
          Open sounding
        </Action>
        {open.isPending ? <span className={styles.busy} role="status">{`Fetching ${stationId} ${formatUtc(time)}`}</span> : null}
      </div>
      {open.isError ? (
        <SoundingErrorNotice
          error={open.error}
          operation="open_sounding"
          onRetry={() => {
            submit();
          }}
          busy={open.isPending}
          testId="observed-error"
        />
      ) : null}
    </form>
  );
}
