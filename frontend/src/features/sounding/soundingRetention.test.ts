import { afterEach, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { useSoundingStore } from "../../stores/soundingStore";
import { startSoundingRetention } from "./soundingRetention";

afterEach(() => { useSoundingStore.setState({ open: [], activeId: null, views: {}, timeIndices: {}, pendingChanges: [] }); });
afterEach(() => { vi.restoreAllMocks(); });
const summary = (soundingId: string) => ({ ...parseSoundingSummary(summaryExample), soundingId });

it("forwards retention and rejects malformed or unsuccessful acknowledgements", async () => {
  const call = vi.fn().mockResolvedValue({ retained: true });
  const client = new BackendClient({ call, listen: () => Promise.resolve(() => undefined) });
  await expect(client.retainSoundings(["abc123"])).resolves.toBe(true);
  expect(call).toHaveBeenCalledWith("retain_soundings", { soundingIds: ["abc123"] });
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  for (const reply of [{ retained: false }, { retained: "true" }, { retained: true, extra: 1 }]) {
    call.mockResolvedValue(reply);
    await expect(client.retainSoundings([])).rejects.toMatchObject({ error: { code: "CONTRACT_VIOLATION" } });
  }
});

it("serializes retention changes and sends the latest open list after a slow acknowledgement", async () => {
  let finish: (value: boolean) => void = () => undefined;
  const retainSoundings = vi.fn<(ids: readonly string[]) => Promise<boolean>>()
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue(true);
  const stop = startSoundingRetention({ retainSoundings });
  const store = useSoundingStore.getState();
  store.add(summary("edited"));
  store.add(summary("next"));
  store.close("next");
  store.add(summary("latest"));
  expect(retainSoundings.mock.calls).toEqual([[["edited"]]]);
  finish(true);
  await vi.waitFor(() => { expect(retainSoundings).toHaveBeenCalledTimes(2); });
  expect(retainSoundings.mock.calls[1]).toEqual([["latest", "edited"]]);
  stop();
  store.close("edited");
  expect(retainSoundings).toHaveBeenCalledTimes(2);
});

it("releases closed profiles and recovers on the next list change after a failed request", async () => {
  useSoundingStore.getState().add(summary("first"));
  const retainSoundings = vi.fn<(ids: readonly string[]) => Promise<boolean>>()
    .mockRejectedValueOnce(new Error("backend restarting")).mockResolvedValue(true);
  const stop = startSoundingRetention({ retainSoundings });
  await vi.waitFor(() => { expect(retainSoundings).toHaveBeenCalledTimes(1); });
  useSoundingStore.getState().close("first");
  await vi.waitFor(() => { expect(retainSoundings).toHaveBeenLastCalledWith([]); });
  stop();
});
