// The model field row's own controls (Phase 4): the product, grouped by the
// catalogue's categories; the frame's state (loading, the backend's error with
// its action, or the hour shown and its source); the colour scale; and the
// product's description and caveat from sharpmod.

import type { ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { useForecastContext } from "../../features/forecast/useForecast";
import { compactUtc, forecastLabel } from "../../features/sounding/soundingTime";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { SourceSelect } from "../../features/sounding/SourceFields";
import { useForecastStore } from "../../stores/forecastStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import type { MapCatalog } from "../../types/map";
import { usePanelField, type PanelField } from "../panelField";
import { FieldLegend } from "./FieldLegend";
import styles from "./LayerPanel.module.css";

function ProductSelect({ catalog, value }: { catalog: MapCatalog; value: string }): ReactNode {
  return (
    <SourceSelect label="Product" value={value}
      options={catalog.categories.flatMap((category) => catalog.products
        .filter((product) => product.category === category)
        .map((product) => ({ value: product.key, label: product.label, detail: category })))}
      onChange={useMapStore.getState().setProduct} />
  );
}

function FrameState({ field }: { field: PanelField }): ReactNode {
  const context = useForecastContext();
  const { frame, request } = field;
  if (field.blocked === "loading-models") {
    return <p className={styles.hint}>Loading the forecast models</p>;
  }
  if (field.blocked === "other-model" && field.catalog.data !== undefined) {
    const fieldModel = field.catalog.data.fieldModel;
    const label = context?.models.find((model) => model.key === fieldModel)?.label ?? fieldModel;
    return (
      <div className={styles.status}>
        <p className={styles.hint}>{`Map fields come from ${label} runs; ${context?.model.label ?? "this model"} has none here.`}</p>
        <Action
          onClick={() => {
            useForecastStore.getState().setModel(fieldModel);
          }}
        >{`Use ${label}`}</Action>
      </div>
    );
  }
  if (request === null) {
    return null;
  }
  if (frame.isError) {
    return (
      <SoundingErrorNotice
        error={frame.error}
        operation="get_weather_field"
        onRetry={() => {
          void frame.refetch();
        }}
        busy={frame.isFetching}
        testId="field-error"
      />
    );
  }
  const shown = frame.data;
  if (shown === undefined || shown.forecastHour !== request.forecastHour) {
    return <p className={styles.hint} data-testid="field-loading">{`Loading ${forecastLabel(request.forecastHour)}. A new model hour takes a few seconds.`}</p>;
  }
  return (
    <p className={styles.hint} data-testid="field-shown">
      {`${forecastLabel(shown.forecastHour)} of the ${compactUtc(shown.runTime)} run, valid ${compactUtc(shown.validTime)}. ${shown.attribution}.`}
    </p>
  );
}

/** Shown inside the model field row while the layer is on. */
export function FieldControls({ panel }: { panel: number }): ReactNode {
  const field = usePanelField(panel);
  const smoothing = useMapStore((state) => panelLayers(state, panel)["model-field"].smoothing ?? false);
  const catalog = field.catalog;
  if (catalog.isError) {
    return (
      <SoundingErrorNotice
        error={catalog.error}
        operation="get_map_catalog"
        onRetry={() => {
          void catalog.refetch();
        }}
        busy={catalog.isFetching}
        testId="map-catalog-error"
      />
    );
  }
  if (catalog.data === undefined || field.product === null) {
    return <p className={styles.hint}>Loading the map products</p>;
  }
  const { product } = field;
  const contour = product.contour;
  return (
    <div className={styles.extra}>
      <ProductSelect catalog={catalog.data} value={product.key} />
      <SourceSelect label="Pixel rendering" value={smoothing ? "smooth" : "sharp"}
        options={[
          { value: "sharp", label: "Sharp", detail: "Original pixel edges" },
          { value: "smooth", label: "Smooth", detail: "Smooth shapes, crisp colors" },
        ]}
        onChange={(value) => { useMapStore.getState().setFieldSmoothing(value === "smooth"); }} />
      <p className={styles.hint}>Applies to all model parameters in this panel.</p>
      <FrameState field={field} />
      <FieldLegend palette={product.palette} />
      {product.description === "" ? null : <p className={styles.hint}>{product.description}</p>}
      {contour === null ? null : (
        <p className={styles.hint}>{`Contours every ${String(contour.interval)} ${contour.label}.`}</p>
      )}
      {product.caveat === "" ? null : <p className={styles.warning}>{product.caveat}</p>}
    </div>
  );
}
