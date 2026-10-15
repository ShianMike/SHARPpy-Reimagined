import summaryExample from "@contracts/public/soundings/summary.example.json";
import { beforeEach, describe, expect, it } from "vitest";
import { parseSoundingSummary } from "../lib/contracts/soundings";
import type { SoundingSummary } from "../types/soundings";
import { MAX_OPEN, useSoundingStore } from "./soundingStore";

const initial = useSoundingStore.getState();
const base = parseSoundingSummary(summaryExample);
const summary = (id: string): SoundingSummary => ({ ...base, soundingId: id, title: `Sounding ${id}` });
const ids = (): string[] => useSoundingStore.getState().open.map((item) => item.soundingId);

beforeEach(() => {
  useSoundingStore.setState(initial, true);
});

describe("soundingStore", () => {
  it("lists opened soundings newest first and makes the newest active", () => {
    const { add } = useSoundingStore.getState();
    add(summary("a"));
    add(summary("b"));
    expect(ids()).toEqual(["b", "a"]);
    expect(useSoundingStore.getState().activeId).toBe("b");
    add(summary("a"));
    expect(ids()).toEqual(["a", "b"]);
  });

  it(`keeps at most ${String(MAX_OPEN)} soundings`, () => {
    for (let index = 0; index < MAX_OPEN + 3; index += 1) {
      useSoundingStore.getState().add(summary(String(index)));
    }
    expect(ids()).toHaveLength(MAX_OPEN);
    expect(ids()[0]).toBe(String(MAX_OPEN + 2));
  });

  it("closing the active sounding activates the next one", () => {
    const store = useSoundingStore.getState();
    store.add(summary("a"));
    store.add(summary("b"));
    store.close("b");
    expect(ids()).toEqual(["a"]);
    expect(useSoundingStore.getState().activeId).toBe("a");
    useSoundingStore.getState().close("a");
    expect(useSoundingStore.getState().activeId).toBeNull();
  });

  it("ignores an unknown active id", () => {
    useSoundingStore.getState().add(summary("a"));
    useSoundingStore.getState().setActive("nope");
    expect(useSoundingStore.getState().activeId).toBe("a");
  });
});
