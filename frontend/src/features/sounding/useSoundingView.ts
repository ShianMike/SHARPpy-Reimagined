import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS } from "../../lib/backend/queryOptions";
import { useSoundingStore } from "../../stores/soundingStore";
import type { ImageMode, RenderChoice } from "../../types/soundings";
import type { ViewRequest } from "../../types/soundingView";
import { appendEdit, INITIAL_VIEW } from "./viewHistory";

export function useSoundingView(id: string, choice: RenderChoice, quality: ImageMode, timeIndex = 0) {
  const client = useBackendClient();
  const cache = useQueryClient();
  const viewId = timeIndex === 0 ? id : `${id}:${String(timeIndex)}`;
  const state = useSoundingStore((store) => store.views[viewId] ?? INITIAL_VIEW);
  const setView = useSoundingStore((store) => store.setView);
  const edits = state.history[state.cursor] ?? [];
  const request = { ...state.options, edits, ...(timeIndex === 0 ? {} : { timeIndex }) };
  const key = (view: ViewRequest) => ["soundings", id, "view", timeIndex, choice, quality, view] as const;
  const image = useQuery({ ...BASE_QUERY_OPTIONS,
    queryKey: key(request), queryFn: () => client.renderSoundingView(id, choice, quality, request),
    staleTime: Infinity, gcTime: 5 * 60_000,
    placeholderData: (previous, query) => query?.queryKey[1] === id && query.queryKey[3] === timeIndex ? previous : undefined,
  });
  const edit = useMutation({
    mutationKey: ["sounding-edit", id],
    mutationFn: (next: ViewRequest) => client.renderSoundingView(id, choice, quality, next),
    onSuccess: (result) => {
      if (!useSoundingStore.getState().open.some((item) => item.soundingId === id)) return;
      cache.setQueryData(key({ ...request, edits: result.edits }), result);
      setView(viewId, appendEdit(state, result.edits));
    },
  });
  return { image, edit, state, edits, request,
    busy: image.isFetching || edit.isPending,
    update: (next: typeof state) => { edit.reset(); setView(viewId, next); },
  };
}
