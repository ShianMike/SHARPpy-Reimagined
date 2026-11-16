// @vitest-environment jsdom
import summaryFixture from "@contracts/public/soundings/summary.example.json";
import { afterEach, expect, it, vi } from "vitest";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import type { SoundingSnapshot } from "../../lib/contracts/soundingRecovery";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { useSoundingRecoveryStore } from "../../stores/soundingRecoveryStore";
import { createSoundingRecovery } from "./soundingRecovery";
import { captureRecoverySession, type RecoverySession } from "./recoverySession";
import type { RecoveryRepository } from "./recoveryRepository";
import { appendEdit, INITIAL_VIEW } from "./viewHistory";

const summary = parseSoundingSummary(summaryFixture);
afterEach(() => {
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  useSoundingRecoveryStore.setState(useSoundingRecoveryStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
});

function storage() {
  let current: RecoverySession | null = null, previous: RecoverySession | null = null;
  const sources = new Map<string, SoundingSnapshot>();
  const repository: RecoveryRepository = {
    start: vi.fn(() => { previous = current ?? previous; current = null; return Promise.resolve(previous); }),
    source: vi.fn((id: string) => {
      const source = sources.get(id);
      return source === undefined ? Promise.reject(new Error("Saved source missing")) : Promise.resolve(source);
    }),
    save: vi.fn<RecoveryRepository["save"]>((session, added, prior = previous) => {
      current = session; previous = prior;
      for (const source of added) sources.set(source.soundingId, source);
      return Promise.resolve();
    }),
    close: vi.fn(),
  };
  return { repository, current: () => current, previous: () => previous, sources };
}
function backend() {
  return {
    getSoundingSnapshot: vi.fn((id: string) => Promise.resolve({ soundingId: id,
      payload: JSON.stringify(useSoundingStore.getState().open.find(item => item.soundingId === id) ?? { ...summary, soundingId: id }) })),
    restoreSoundingSnapshot: vi.fn((payload: string) => Promise.resolve({ ...parseSoundingSummary(JSON.parse(payload)), soundingId: "restoredid" })),
  };
}

it("recovers original sources, completed edits and undo history with new backend IDs after restart", async () => {
  const disk = storage(), client = backend();
  const first = createSoundingRecovery(client, disk.repository);
  await first.start();
  useSoundingStore.getState().add({ ...summary, timeCount: 2, times: [summary.validTime, summary.validTime] });
  useSoundingStore.getState().setMode("winter");
  useSoundingStore.getState().setChoice("ML");
  useSoundingStore.getState().setTimeIndex(summary.soundingId, 1);
  const edited = appendEdit({ ...INITIAL_VIEW, options: { chart: "1", panel: "HEIGHT CHARTS" } }, [{ index: 0, values: { tmpc: -7.25 } }]);
  const history = appendEdit(edited, [{ index: 0, values: { tmpc: -6.5 } }]);
  useSoundingStore.getState().setView(`${summary.soundingId}:1`, { ...history, cursor: 1 });
  await first.flush();
  expect(disk.current()?.views[`${summary.soundingId}:1`]?.history).toHaveLength(3);
  expect(client.getSoundingSnapshot).toHaveBeenCalledOnce();
  first.stop();
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  client.getSoundingSnapshot.mockRejectedValue(new Error("Old backend cache has been removed"));
  const next = createSoundingRecovery(client, disk.repository);
  await next.start();
  expect(useSoundingRecoveryStore.getState()).toMatchObject({ prompt: true, previous: { mode: "winter" } });
  expect(useSoundingStore.getState().open).toEqual([]);
  await useSoundingRecoveryStore.getState().restore();
  await next.flush();
  expect(useSoundingStore.getState()).toMatchObject({ activeId: "restoredid", mode: "winter", choice: "ML",
    views: { "restoredid:1": { history: history.history, cursor: 1, options: { chart: "1", panel: "HEIGHT CHARTS" } } },
    timeIndices: { restoredid: 1 } });
  expect(useAppStore.getState().activeWorkspace).toBe("sounding");
  expect(client.restoreSoundingSnapshot).toHaveBeenCalledOnce();
  expect(client.getSoundingSnapshot).toHaveBeenCalledOnce();
  expect(disk.previous()).toBeNull();
  expect(useSoundingRecoveryStore.getState().status).toBe("saved");
  next.stop();
});

it("keeps the previous complete checkpoint when saving fails and supports retry", async () => {
  const disk = storage(), client = backend(), service = createSoundingRecovery(client, disk.repository);
  await service.start();
  useSoundingStore.getState().add(summary);
  await service.flush();
  const saved = disk.current();
  vi.mocked(disk.repository.save).mockRejectedValueOnce(new Error("Storage full"));
  useSoundingStore.getState().setMode("winter");
  await service.flush();
  expect(useSoundingRecoveryStore.getState()).toMatchObject({ status: "error", message: "Storage full" });
  expect(disk.current()).toBe(saved);
  await service.flush();
  expect(disk.current()?.mode).toBe("winter");
  expect(useSoundingRecoveryStore.getState().status).toBe("saved");
  service.stop();
});

it("preserves a failed restore and current open work instead of replacing either", async () => {
  const disk = storage(), client = backend();
  await disk.repository.save({ ...captureRecoverySession({ ...useSoundingStore.getState(), open: [summary], activeId: summary.soundingId }) } as RecoverySession, [await client.getSoundingSnapshot(summary.soundingId)]);
  const service = createSoundingRecovery(client, disk.repository);
  await service.start();
  useSoundingStore.getState().add({ ...summary, soundingId: "current" });
  await service.flush();
  const current = useSoundingStore.getState();
  client.restoreSoundingSnapshot.mockRejectedValueOnce(new Error("Saved profile is damaged"));
  await useSoundingRecoveryStore.getState().restore();
  expect(useSoundingStore.getState()).toBe(current);
  expect(useSoundingRecoveryStore.getState()).toMatchObject({ status: "error", previous: { activeId: summary.soundingId } });
  expect(disk.previous()).not.toBeNull();
  service.stop();
});

it("does not revive explicitly closed soundings", async () => {
  const disk = storage(), client = backend(), service = createSoundingRecovery(client, disk.repository);
  await service.start();
  useSoundingStore.getState().add(summary);
  await service.flush();
  useSoundingStore.getState().close(summary.soundingId);
  await service.flush();
  expect(disk.current()).toBeNull();
  service.stop();
  const fresh = createSoundingRecovery(client, disk.repository);
  await fresh.start();
  expect(useSoundingRecoveryStore.getState().previous).toBeNull();
  fresh.stop();
});

it("discards a previous session without closing current open work", async () => {
  const disk = storage(), client = backend();
  const saved = captureRecoverySession({ ...useSoundingStore.getState(), open: [summary], activeId: summary.soundingId });
  await disk.repository.save(saved, [await client.getSoundingSnapshot(summary.soundingId)]);
  const service = createSoundingRecovery(client, disk.repository);
  await service.start();
  useSoundingStore.getState().add({ ...summary, soundingId: "current" });
  await service.flush();
  await useSoundingRecoveryStore.getState().discard();
  await service.flush();
  expect(disk.previous()).toBeNull();
  expect(disk.current()?.open.map(item => item.soundingId)).toEqual(["current"]);
  expect(useSoundingStore.getState().activeId).toBe("current");
  service.stop();
});

it("preserves the saved session when a backend restart clears invalid sounding IDs", async () => {
  const disk = storage(), client = backend(), service = createSoundingRecovery(client, disk.repository);
  await service.start();
  useSoundingStore.getState().add({ ...summary, timeCount: 2, times: [summary.validTime, summary.validTime] });
  useSoundingStore.getState().setTimeIndex(summary.soundingId, 1);
  useSoundingStore.getState().setMode("winter");
  await service.flush();
  useSoundingRecoveryStore.getState().backendRestart();
  useSoundingStore.setState({ open: [], activeId: null, views: {}, timeIndices: {}, pendingChanges: [] });
  await vi.waitFor(() => { expect(useSoundingRecoveryStore.getState().previous?.mode).toBe("winter"); });
  await service.flush();
  expect(disk.previous()?.open[0]?.soundingId).toBe(summary.soundingId);
  expect(useSoundingRecoveryStore.getState().prompt).toBe(true);
  await useSoundingRecoveryStore.getState().restore();
  await service.flush();
  expect(useSoundingStore.getState().activeId).toBe("restoredid");
  expect(useSoundingStore.getState().timeIndices).toEqual({ restoredid: 1 });
  service.stop();
});

it("stops saving when disposed and preserves the recovery source if initial storage is unavailable", async () => {
  const disk = storage(), client = backend();
  vi.mocked(disk.repository.start).mockRejectedValueOnce(new Error("Recovery storage unavailable"));
  const service = createSoundingRecovery(client, disk.repository);
  await service.start();
  expect(useSoundingRecoveryStore.getState().status).toBe("error");
  service.stop();
  useSoundingStore.getState().add(summary);
  expect(client.getSoundingSnapshot).not.toHaveBeenCalled();
});
