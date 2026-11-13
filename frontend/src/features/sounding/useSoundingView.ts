import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS } from "../../lib/backend/queryOptions";
import { useSoundingStore } from "../../stores/soundingStore";
import { useUpdateStore } from "../../stores/updateStore";
import { useUiStore } from "../../stores/uiStore";
import type { ImageMode, RenderChoice, SoundingColorStyle } from "../../types/soundings";
import type { ViewRequest } from "../../types/soundingView";
import { appendEdit, INITIAL_VIEW } from "./viewHistory";

export function useSoundingView(id: string, choice: RenderChoice, quality: ImageMode, timeIndex = 0) {
  const client = useBackendClient();
  const colorStyle = useUiStore((store) => store.soundingColorStyle);
  const cache = useQueryClient();
  const viewId = timeIndex === 0 ? id : `${id}:${String(timeIndex)}`;
  const state = useSoundingStore((store) => store.views[viewId] ?? INITIAL_VIEW);
  const setView = useSoundingStore((store) => store.setView);
  const edits = state.history[state.cursor] ?? [];
  const request = { ...state.options, edits, ...(timeIndex === 0 ? {} : { timeIndex }) };
  const mutationKey = ["sounding-edit", id, timeIndex] as const;
  const pending = useIsMutating({ mutationKey }) > 0;
  const key = (view: ViewRequest, style = colorStyle) => ["soundings", id, "view", timeIndex, choice, quality, style, view] as const;
  const image = useQuery({ ...BASE_QUERY_OPTIONS,
    queryKey: key(request), queryFn: () => client.renderSoundingView(id, choice, quality, request, colorStyle),
    staleTime: Infinity, gcTime: 5 * 60_000,
    placeholderData: (previous, query) => query?.queryKey[1] === id && query.queryKey[3] === timeIndex ? previous : undefined,
  });
  const edit = useMutation({
    mutationKey,
    onMutate: () => useSoundingStore.getState().views[viewId] ?? INITIAL_VIEW,
    mutationFn: ({ view, style }: { view: ViewRequest; style: SoundingColorStyle }) => {
      useUpdateStore.getState().noteSoundingWork();
      return client.renderSoundingView(id, choice, quality, view, style);
    },
    onSuccess: (result, input, original) => {
      const current = useSoundingStore.getState();
      if (!current.open.some((item) => item.soundingId === id) || (current.views[viewId] ?? INITIAL_VIEW) !== original) return;
      const resolved = { ...input.view, edits: result.edits };
      delete resolved.drag;
      cache.setQueryData(key(resolved, input.style), result);
      setView(viewId, appendEdit(original, result.edits));
    },
  });
  return { image, edit: { ...edit, variables: edit.variables?.view, mutate: (view: ViewRequest) => { edit.mutate({ view, style: colorStyle }); } }, state, edits, request,
    ready: image.isSuccess && !image.isPlaceholderData && !image.isFetching && !pending && !edit.isError && image.data.image.size > 0,
    busy: image.isFetching || pending,
    update: (next: typeof state) => { edit.reset(); setView(viewId, next); },
  };
}
