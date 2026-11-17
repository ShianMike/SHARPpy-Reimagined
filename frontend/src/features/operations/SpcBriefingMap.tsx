import { useQueries } from "@tanstack/react-query";
import { useId, useMemo, type ReactNode } from "react";
import type { BasemapFeatureCollection } from "../../../tooling/basemap";
import type { LonLat, Ring } from "../../map/layers/outlookGeometry";
import type { OutlookArea } from "../../types/map";
import styles from "./SpcBriefing.module.css";

const WIDTH = 720, HEIGHT = 440;
const HATCH_STEP = 8;
const mercator = (latitude: number): number => Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, latitude)) * Math.PI / 360)) * 180 / Math.PI;
const north = mercator(51);
function project([longitude, latitude]: LonLat): [number, number] {
  return [(longitude + 126) * 12, (north - mercator(latitude)) * 12];
}
function pathOf(ring: Ring): string {
  return ring.map((point, index) => `${index === 0 ? "M" : "L"}${project(point).map((n) => n.toFixed(2)).join(",")}`).join("");
}

/** A small vector preview uses the same bundled geography as the main map, without another WebGL scene. */
export function SpcBriefingMap({ areas, point, label }: {
  areas: readonly OutlookArea[];
  point: LonLat | null;
  label: string;
}): ReactNode {
  const id = useId();
  const geography = useQueries({ queries: (["coastline", "states"] as const).map((family) => ({
    queryKey: ["briefing-geography", family],
    queryFn: async ({ signal }: { signal: AbortSignal }): Promise<BasemapFeatureCollection> => {
      const response = await fetch(`/geo/${family}.geojson`, { signal });
      if (!response.ok) throw new Error("Bundled geography unavailable");
      // These same-origin assets are validated and constructed by the basemap build plugin.
      return response.json() as Promise<BasemapFeatureCollection>;
    },
    staleTime: Infinity,
    retry: false,
  })) });
  const coastline = geography[0]?.data, states = geography[1]?.data;
  const paths = useMemo(() => [coastline, states].map((collection) => collection?.features.flatMap((feature) =>
    feature.geometry.coordinates.filter((line) => line.some(([lon, lat]) => lon >= -128 && lon <= -64 && lat >= 21 && lat <= 53)).map(pathOf)).join("") ?? ""), [coastline, states]);
  const risks = useMemo(() => [...areas].sort((a, b) => a.rank - b.rank).map((area) => ({ area, path: area.rings.map((ring) => `${pathOf(ring)}Z`).join("") })), [areas]);
  const marker = point === null ? null : project(point);
  return <div className={styles.map}>
    <svg viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`} role="img" aria-label={label}>
      <defs>
        <clipPath id={`${id}-clip`}><rect width={WIDTH} height={HEIGHT} /></clipPath>
        <pattern id={`${id}-hatch`} width={HATCH_STEP} height={HATCH_STEP} patternUnits="userSpaceOnUse" patternTransform="rotate(35)"><path d="M0 0V8" className={styles.hatch} /></pattern>
      </defs>
      <g clipPath={`url(#${id}-clip)`}>
        {[-120, -110, -100, -90, -80, -70].map((lon) => <path key={lon} d={pathOf([[lon, 20], [lon, 55]])} className={styles.grid} />)}
        {[25, 30, 35, 40, 45, 50].map((lat) => <path key={lat} d={pathOf([[-130, lat], [-60, lat]])} className={styles.grid} />)}
        {risks.map(({ area, path }, index) => <path key={index} className={styles.risk} d={path} fill={area.hatch ? `url(#${id}-hatch)` : area.fill ?? "none"} fillOpacity={area.hatch ? 1 : 0.62} stroke={area.stroke} fillRule="evenodd"><title>{area.description || area.label}</title></path>)}
        <path d={paths[1]} className={styles.states} />
        <path d={paths[0]} className={styles.coast} />
        {([["Seattle", -122.3, 47.6], ["Denver", -105, 39.7], ["Dallas", -96.8, 32.8], ["Chicago", -87.6, 41.9], ["New York", -74, 40.7], ["Miami", -80.2, 25.8]] as const).map(([name, lon, lat]) => {
          const [x, y] = project([lon, lat]);
          return <g key={name} className={styles.city}><circle cx={x} cy={y} r={2} /><text x={x + 6} y={y - 5}>{name}</text></g>;
        })}
        {marker === null ? null : <g className={styles.marker}><circle cx={marker[0]} cy={marker[1]} r={9} /><circle cx={marker[0]} cy={marker[1]} r={3} /><title>Selected location</title></g>}
      </g>
    </svg>
    <span className={styles.mapCredit}>{geography.some((query) => query.isError) ? "Basemap unavailable · " : ""}Contiguous U.S. · Natural Earth</span>
  </div>;
}
