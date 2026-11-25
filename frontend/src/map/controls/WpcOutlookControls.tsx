import type { ReactNode } from "react";
import { OutlookDiscussionSummary } from "../../features/operations/OutlookDiscussionSummary";
import { compactUtc } from "../../features/sounding/soundingTime";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import {
  OUTLOOK_DAYS,
  WPC_WINTER_PRODUCTS,
  WPC_WINTER_PRODUCT_LABELS,
  type OutlookDay,
  type WpcOutlookDayMode,
} from "../../types/map";
import { useLatestWpcOutlook, useWpcOutlook } from "../mapQueries";
import layerStyles from "./LayerPanel.module.css";
import styles from "./WpcOutlookControls.module.css";

const MODES: readonly { value: WpcOutlookDayMode; label: string }[] = [
  { value: "auto", label: "Auto" },
  ...OUTLOOK_DAYS.map((day) => ({ value: day, label: `Day ${String(day)}` })),
];

function dayFromTitle(title: string | undefined): OutlookDay | null {
  const match = title?.match(/\bDay ([123])\b/);
  return match?.[1] === "1" ? 1 : match?.[1] === "2" ? 2 : match?.[1] === "3" ? 3 : null;
}

export function WpcOutlookControls({ panel, kind = "ero" }: { panel: number; kind?: "ero" | "winter" }): ReactNode {
  const validTime = useWeatherTime();
  const layerId = kind === "winter" ? "wpc-winter" : "wpc-ero";
  const visible = useMapStore((state) => panelLayers(state, panel)[layerId].visible);
  const dayMode = useMapStore((state) => (kind === "winter" ? state.wpcWinterDay : state.wpcOutlookDay));
  const winterProduct = useMapStore((state) => state.wpcWinterProduct);
  const product = kind === "winter" ? winterProduct : "ero";
  const query = useWpcOutlook(visible, validTime, dayMode, product);
  const outlook = query.data?.outlook;
  const matchedDay = dayMode !== "auto" ? dayMode : outlook?.covers === true ? dayFromTitle(outlook.title) : null;
  const discussionQuery = useLatestWpcOutlook(
    matchedDay ?? 1,
    visible && matchedDay !== null && outlook?.discussion === undefined,
    product,
  );
  const discussion = outlook?.discussion ?? (dayMode === "auto" && outlook?.covers === true ? discussionQuery.data?.outlook?.discussion : undefined);
  const name = kind === "winter" ? "winter weather" : "excessive rainfall";

  let status: ReactNode;
  if (dayMode === "auto" && validTime === null) {
    status = <p className={layerStyles.hint}>Choose a time to view WPC {name} outlooks, or select Day 1–3.</p>;
  } else if (query.isError) {
    status = (
      <SoundingErrorNotice
        error={query.error}
        operation="get_wpc_outlook"
        onRetry={() => { void query.refetch(); }}
        busy={query.isFetching}
        testId={`outlook-error-${kind}`}
      />
    );
  } else if (query.data === undefined) {
    status = <p className={layerStyles.hint}>Loading the WPC {name} outlook</p>;
  } else if (outlook === null || outlook === undefined) {
    status = (
      <p className={layerStyles.hint} data-testid={`outlook-none-${kind}`}>
        {dayMode === "auto" && validTime !== null
          ? `WPC published no ${name} outlook covering ${compactUtc(validTime)}.`
          : `WPC published no Day ${String(dayMode)} ${name} outlook.`}
      </p>
    );
  } else {
    status = (
      <p className={outlook.covers || dayMode !== "auto" ? layerStyles.hint : layerStyles.warning} data-testid={`outlook-shown-${kind}`}>
        {outlook.covers || dayMode !== "auto"
          ? `${outlook.subtitle}. ${outlook.attribution}.`
          : `No WPC ${name} outlook covers ${compactUtc(validTime ?? "")}. Nearest issuance: ${outlook.subtitle}.`}
      </p>
    );
  }

  return (
    <div className={layerStyles.extra}>
      {kind === "winter" ? (
        <div className={styles.row}>
          <span className={styles.label}>Component</span>
          <div className={styles.modes} role="group" aria-label="WPC winter outlook product">
            {WPC_WINTER_PRODUCTS.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={winterProduct === option}
                onClick={() => { useMapStore.getState().setWpcWinterProduct(option); }}
              >
                {WPC_WINTER_PRODUCT_LABELS[option].short}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className={styles.row}>
        <span className={styles.label}>Issuance</span>
        <div className={styles.modes} role="group" aria-label={kind === "winter" ? "WPC winter outlook day" : "WPC outlook day"}>
          {MODES.map((option) => (
            <button
              key={String(option.value)}
              type="button"
              aria-pressed={dayMode === option.value}
              onClick={() => {
                const store = useMapStore.getState();
                if (kind === "winter") store.setWpcWinterDay(option.value);
                else store.setWpcOutlookDay(option.value);
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      {status}
      <OutlookDiscussionSummary discussion={discussion} compact />
    </div>
  );
}
