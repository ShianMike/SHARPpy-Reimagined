import type { BackendClient } from "../../lib/backend/client";
import { useSoundingStore } from "../../stores/soundingStore";

/** Keep open profiles out of the background LRU, including older edited tabs. */
export function startSoundingRetention(client: Pick<BackendClient, "retainSoundings">): () => void {
  let pending: string[] | null = null;
  let running = false, stopped = false;
  const send = (): void => {
    if (stopped || running || pending === null) return;
    const ids = pending;
    pending = null;
    running = true;
    // BackendClient records failures. A later open-list change retries its
    // latest state; failed requests never create a background retry loop.
    void client.retainSoundings(ids).catch(() => undefined).finally(() => {
      running = false;
      send();
    });
  };
  const update = (): void => {
    pending = useSoundingStore.getState().open.map(item => item.soundingId);
    send();
  };
  const unsubscribe = useSoundingStore.subscribe(state => state.open, update);
  if (useSoundingStore.getState().open.length > 0) update();
  return () => { stopped = true; pending = null; unsubscribe(); };
}
