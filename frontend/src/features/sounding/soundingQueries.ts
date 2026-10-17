// Sounding and model queries (DESIGN-frontend F3). An open sounding never
// changes. The interactive editor keeps its derived view separately.
import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS, QUERY_KEYS, soundingKeys } from "../../lib/backend/queryOptions";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
import type {
  ModelCatalog,
  OpenSoundingRequest,
  RenderChoice,
  SoundingSummary,
  SoundingProgress,
} from "../../types/soundings";

const MODEL_LIST_STALE_MS = 10 * 60_000;
/** Images are large; an unused one leaves memory after five minutes. */
const IMAGE_GC_MS = 5 * 60_000;

const MODEL_LABELS: Readonly<Record<string, string>> = {
  hrrr: "HRRR",
  rap: "RAP",
  nam: "NAM",
  "nam-3km-conus": "NAM 3km CONUS",
  rdps: "RDPS",
  "hrw-wrf-arw": "HRW WRF-ARW",
  "hrw-wrf-nssl": "HRW WRF-NSSL",
  "hrw-fv3": "HRW FV3",
  "rrfs-a": "RRFS",
  "refs-m1": "REFSm1",
  "refs-m2": "REFSm2",
  "refs-m3": "REFSm3",
  "refs-m4": "REFSm4",
  "refs-m5": "REFSm5",
  gfs: "GFS",
  "openmeteo-ecmwf-ifs025": "ECMWF",
};

const requestedModels = (catalog: ModelCatalog): ModelCatalog => ({
  ...catalog,
  models: catalog.models
    .filter((model) => Object.hasOwn(MODEL_LABELS, model.key))
    .map((model) => ({ ...model, label: MODEL_LABELS[model.key] ?? model.label })),
});

export function useModelCatalog(): UseQueryResult<ModelCatalog> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: QUERY_KEYS.models,
    queryFn: () => client.getModelCatalog(),
    staleTime: MODEL_LIST_STALE_MS,
    select: requestedModels,
  });
}

/**
 * Opens a sounding. On success it joins the open list, becomes the active
 * sounding, and the Sounding workspace comes forward.
 */
export function useOpenSounding(): UseMutationResult<SoundingSummary, Error, OpenSoundingRequest> & { progress: SoundingProgress | undefined } {
  const client = useBackendClient();
  const [progressId, setProgressId] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (request: OpenSoundingRequest) => {
      const id = request.kind === "model" ? crypto.randomUUID().replaceAll("-", "") : undefined;
      setProgressId(id ?? null);
      return client.openSounding(request, id);
    },
    onSuccess: (summary) => {
      useSoundingStore.getState().add(summary);
      useAppStore.getState().setActiveWorkspace("sounding");
    },
    onSettled: () => { setProgressId(null); },
  });
  const progress = useQuery({
    queryKey: ["soundings", "progress", progressId],
    queryFn: () => client.getSoundingProgress(progressId ?? ""),
    enabled: mutation.isPending && progressId !== null,
    refetchInterval: 1000,
    refetchIntervalInBackground: true,
    retry: false,
    gcTime: 0,
  });
  return { ...mutation, progress: mutation.isPending ? progress.data : undefined };
}

export function useSoundingImage(soundingId: string | null, choice: RenderChoice): UseQueryResult<Blob> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: soundingKeys.image(soundingId ?? "", choice),
    queryFn: () => client.renderSounding(soundingId ?? "", choice),
    enabled: soundingId !== null,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: IMAGE_GC_MS,
    // While another ascent of the same sounding is drawn, keep showing the last
    // image of that sounding (never another sounding's image).
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === soundingId ? previous : undefined,
  });
}

interface ObjectUrl {
  url: string;
  users: number;
}

/** One object URL per image blob, shared by every view that shows it. */
const objectUrls = new WeakMap<Blob, ObjectUrl>();

function objectUrlFor(blob: Blob): ObjectUrl {
  let entry = objectUrls.get(blob);
  if (entry === undefined) {
    entry = { url: URL.createObjectURL(blob), users: 0 };
    objectUrls.set(blob, entry);
  }
  return entry;
}

/**
 * An object URL for `blob`. The URL is revoked once no mounted view uses it;
 * the release waits a task, so an unmount followed at once by a mount (React
 * StrictMode, a re-keyed view) keeps the same URL.
 */
export function useObjectUrl(blob: Blob | undefined): string | null {
  const url = blob === undefined ? null : objectUrlFor(blob).url;
  useEffect(() => {
    if (blob === undefined) {
      return;
    }
    retainObjectUrl(blob);
    return () => {
      releaseObjectUrl(blob);
    };
  }, [blob]);
  return url;
}

function retainObjectUrl(blob: Blob): void {
  objectUrlFor(blob).users += 1;
}

function releaseObjectUrl(blob: Blob): void {
  const entry = objectUrls.get(blob);
  if (entry === undefined) {
    return;
  }
  entry.users -= 1;
  setTimeout(() => {
    if (entry.users === 0 && objectUrls.get(blob) === entry) {
      objectUrls.delete(blob);
      URL.revokeObjectURL(entry.url);
    }
  }, 0);
}
