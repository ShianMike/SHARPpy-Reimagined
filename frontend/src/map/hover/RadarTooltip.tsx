import { useEffect, useState, type ReactNode } from "react";
import { panelLayers, panelWeather, useMapStore } from "../../stores/mapStore";
import { useEffectiveRadarSite, useRadarSiteFrame } from "../mapQueries";
import { radarValues } from "../pngImages";
import { radarCodeAt, type RadarValues } from "../radarValues";
import { formatValue } from "../field/colourize";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { PointerValue } from "./FieldTooltip";
import { useHoverStore } from "./hoverStore";

export function RadarTooltip({ panel }: { panel: number }): ReactNode {
  const visible = useMapStore((state) => panelLayers(state, panel).radar.visible);
  const { radarProduct, radarTilt, radarScope } = useMapStore((state) => panelWeather(state, panel));
  const site = useEffectiveRadarSite(panel);
  const time = useWeatherTime();
  const query = useRadarSiteFrame(radarProduct, site, visible && radarScope === "site", time, radarTilt);
  const frame = visible && radarScope === "site" ? query.data : undefined;
  const pointer = useHoverStore((state) => state.pointer?.panel === panel ? state.pointer : null);
  const hovering = pointer !== null;
  const [decoded, setDecoded] = useState<{ png: Blob; samples: RadarValues | null } | null>(null);
  useEffect(() => {
    if (frame === undefined || !hovering) return;
    let current = true;
    radarValues(frame.png).then((samples) => {
      if (current) setDecoded({ png: frame.png, samples });
    }, (error: unknown) => { console.warn("[map] radar values could not be decoded", error); });
    return () => { current = false; };
  }, [frame, hovering]);
  const samples = decoded?.png === frame?.png ? decoded?.samples : null;
  if (pointer === null || frame === undefined || samples == null) return null;
  const code = radarCodeAt(samples, frame.bounds, pointer.longitude, pointer.latitude);
  const value = samples.values[code];
  const text = code === samples.folded ? "Range folded" : value == null ? "No data here"
    : `${samples.lowerBounds.has(code) ? "≥ " : ""}${frame.product === "correlation-coefficient" ? value.toFixed(3) : formatValue(value)}${frame.units === "" ? "" : ` ${frame.units}`}`;
  return <PointerValue pointer={pointer} label={`${frame.siteId} · ${frame.label}`} text={text} />;
}
