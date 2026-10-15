// The bundled MapLibre worker (DESIGN-frontend F4). MapLibre 6 computes its
// worker URL from import.meta.url at run time, which Vite cannot see, so a
// plain build emits no worker and no GeoJSON source ever loads. This import
// makes Vite bundle the worker (worker.format "es" in vite.config.ts) and
// hands MapLibre its same-origin URL before the first Map is constructed.
// Imported only by MapRuntime.ts, as its first import.

import { setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

setWorkerUrl(workerUrl);
