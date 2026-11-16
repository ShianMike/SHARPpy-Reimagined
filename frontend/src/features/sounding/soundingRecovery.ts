/* eslint-disable @typescript-eslint/no-unnecessary-condition -- Subscriptions and lifecycle callbacks change queue flags while asynchronous work is pending. */
import { shallow } from "zustand/shallow";
import type { BackendClient } from "../../lib/backend/client";
import type { SoundingSnapshot } from "../../lib/contracts/soundingRecovery";
import { useAppStore } from "../../stores/appStore";
import { MAX_OPEN, useSoundingStore } from "../../stores/soundingStore";
import { useSoundingRecoveryStore as recovery } from "../../stores/soundingRecoveryStore";
import { captureRecoverySession, type RecoverySession } from "./recoverySession";
import { createRecoveryRepository, type RecoveryRepository } from "./recoveryRepository";

type RecoveryClient = Pick<BackendClient, "getSoundingSnapshot" | "restoreSoundingSnapshot">;

/** Source copies are written once; completed edits and selections are checkpointed immediately. */
export function createSoundingRecovery(client: RecoveryClient, repository: RecoveryRepository) {
  let stopped = false, dirty = false, restoring = false;
  let running: Promise<void> | null = null;
  let unsubscribe = (): void => undefined;
  let previousOverride: RecoverySession | null | undefined;
  const sources = new Map<string, SoundingSnapshot>(), written = new Set<string>();
  const fail = (error: unknown): void => {
    if (!stopped) recovery.setState({ status: "error", message: error instanceof Error ? error.message : "The recovery copy could not be saved." });
  };
  const save = async (): Promise<void> => {
    while (dirty && !stopped && !restoring) {
      dirty = false;
      const session = captureRecoverySession(useSoundingStore.getState());
      recovery.setState({ status: "saving", message: null });
      for (const sounding of session?.open ?? []) if (!sources.has(sounding.soundingId)) {
        const source = await client.getSoundingSnapshot(sounding.soundingId);
        if (source.soundingId !== sounding.soundingId) throw new Error("The backend returned a different sounding's recovery source.");
        sources.set(sounding.soundingId, source);
      }
      if (stopped) return;
      if (dirty) continue;
      const added = [...sources.values()].filter(source => !written.has(source.soundingId));
      await repository.save(session, added, previousOverride);
      for (const source of added) written.add(source.soundingId);
      const keep = new Set(session?.open.map(item => item.soundingId));
      for (const id of sources.keys()) if (!keep.has(id)) { sources.delete(id); written.delete(id); }
      if (!stopped && !dirty) recovery.setState({ status: session === null ? "idle" : "saved", savedAt: session?.savedAt ?? null, message: null });
    }
  };
  const schedule = (): void => {
    dirty = true;
    if (running !== null || restoring || stopped) return;
    running = save().catch(fail).finally(() => { running = null; if (dirty && !stopped && recovery.getState().status !== "error") schedule(); });
  };
  const restore = async (): Promise<void> => {
    const session = recovery.getState().previous;
    if (session === null || restoring) return;
    if (session.open.length + useSoundingStore.getState().open.length > MAX_OPEN) {
      fail(new Error(`Close some open soundings before restoring this session. Up to ${String(MAX_OPEN)} soundings can be open.`)); return;
    }
    restoring = true;
    await running;
    recovery.setState({ status: "restoring", message: "Reopening saved profiles…" });
    try {
      const ids = new Map<string, string>();
      const restored = [];
      for (const item of session.open) {
        if (stopped) return;
        const source = await repository.source(item.soundingId);
        const summary = await client.restoreSoundingSnapshot(source.payload);
        restored.push(summary); ids.set(item.soundingId, summary.soundingId);
        sources.set(summary.soundingId, { ...source, soundingId: summary.soundingId });
      }
      if (stopped) return;
      const current = useSoundingStore.getState();
      if (current.open.length + restored.length > MAX_OPEN) throw new Error("The open sounding list changed. Close some soundings and try restoring again.");
      const views = Object.fromEntries([...ids].flatMap(([oldId, newId]) => Object.entries(session.views)
        .filter(([key]) => key === oldId || key.startsWith(`${oldId}:`))
        .map(([key, value]) => [`${newId}${key.slice(oldId.length)}`, value])));
      previousOverride = null;
      useSoundingStore.setState({ open: [...restored, ...current.open], activeId: ids.get(session.activeId ?? "") ?? restored[0]?.soundingId ?? null,
        mode: session.mode, choice: session.choice, views: { ...current.views, ...views },
        timeIndices: { ...current.timeIndices, ...Object.fromEntries(Object.entries(session.timeIndices).map(([id, value]) => [ids.get(id) ?? "", value])) } });
      recovery.setState({ previous: null, prompt: false });
      useAppStore.getState().setActiveWorkspace("sounding");
      dirty = true;
    } catch (error) { fail(error); }
    finally { restoring = false; if (dirty) schedule(); }
  };
  const discard = async (): Promise<void> => {
    if (restoring) return;
    await running;
    previousOverride = null;
    recovery.setState({ previous: null, prompt: false });
    schedule();
  };
  const backendRestart = (): void => {
    restoring = true;
    recovery.setState({ status: "starting", message: null });
    // Pause checkpoints before the provider clears invalid backend IDs.
    void (async () => {
      await running;
      const previous = await repository.start();
      if (stopped) return;
      previousOverride = undefined;
      sources.clear(); written.clear();
      recovery.setState({ status: "idle", previous, prompt: previous !== null });
    })().catch(fail).finally(() => { restoring = false; if (dirty && recovery.getState().status !== "error") schedule(); });
  };
  return {
    start: async (): Promise<void> => {
      try {
        const previous = await repository.start();
        if (stopped) return;
        recovery.setState({ status: "idle", previous, prompt: previous !== null, message: null, restore, discard, retrySave: schedule, backendRestart });
        unsubscribe = useSoundingStore.subscribe(state => [state.open, state.activeId, state.mode, state.choice, state.views, state.timeIndices],
          schedule, { equalityFn: shallow });
        if (useSoundingStore.getState().open.length > 0) schedule();
      } catch (error) { fail(error); }
    },
    stop: (): void => { stopped = true; unsubscribe(); repository.close(); },
    flush: async (): Promise<void> => { schedule(); await running; },
  };
}

/** One writer per browser origin prevents a second window from replacing an active session. */
export function startSoundingRecovery(client: RecoveryClient): () => void {
  let stop = (): void => undefined, release = (): void => undefined;
  let closed = false;
  const begin = async (): Promise<void> => {
    if (closed) return;
    const service = createSoundingRecovery(client, createRecoveryRepository());
    stop = service.stop;
    await service.start();
  };
  if ("locks" in navigator) {
    void navigator.locks.request("sharppy-sounding-recovery", { ifAvailable: true }, async lock => {
      if (lock === null) { recovery.setState({ status: "unavailable", message: "Session recovery is active in another app window." }); return; }
      const lifetime = new Promise<void>(resolve => { release = resolve; });
      await begin();
      if (!closed) await lifetime;
    }).catch((error: unknown) => { recovery.setState({ status: "unavailable", message: String(error) }); });
  } else void begin();
  return () => { closed = true; stop(); release(); };
}
