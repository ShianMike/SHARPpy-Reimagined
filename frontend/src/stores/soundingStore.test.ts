import summaryExample from "@contracts/public/soundings/summary.example.json";
import { beforeEach, describe, expect, it } from "vitest";
import { parseSoundingSummary } from "../lib/contracts/soundings";
import { appendEdit, INITIAL_VIEW } from "../features/sounding/viewHistory";
import type { SoundingSummary } from "../types/soundings";
import { MAX_OPEN, pendingDiscardSounding, useSoundingStore } from "./soundingStore";

const initial = useSoundingStore.getState();
const base = parseSoundingSummary(summaryExample);
const summary = (id: string): SoundingSummary => ({ ...base, soundingId: id, title: `Sounding ${id}` });
const ids = (): string[] => useSoundingStore.getState().open.map((item) => item.soundingId);
const edited = appendEdit(INITIAL_VIEW, [{ index: 0, values: { tmpc: 30 } }]);

function fillEdited(): void {
  const { add, setView } = useSoundingStore.getState();
  for (let index = 0; index < MAX_OPEN; index++) {
    add(summary(String(index)));
    setView(String(index), edited);
  }
}

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

  it("evicts the oldest unedited sounding and preserves an older modified view through further opens", () => {
    const { add, setView } = useSoundingStore.getState();
    const original = summary("edited");
    add(original);
    setView("edited", edited);
    for (let index = 0; index < MAX_OPEN; index++) add(summary(String(index)));
    expect(ids()).toHaveLength(MAX_OPEN);
    expect(ids()).toContain("edited");
    expect(ids()).not.toContain("0");
    expect(useSoundingStore.getState().views.edited).toBe(edited);
    expect(useSoundingStore.getState().open.at(-1)).toBe(original);
    expect(original).toEqual(summary("edited"));
    expect(useSoundingStore.getState().pendingChanges).toEqual([]);
  });

  it("waits for discard or cancel if every eviction candidate has local work", () => {
    fillEdited();
    const before = useSoundingStore.getState();
    before.add(summary("new"));
    expect(useSoundingStore.getState().open).toBe(before.open);
    expect(useSoundingStore.getState().views).toBe(before.views);
    expect(useSoundingStore.getState().activeId).toBe(before.activeId);
    expect(pendingDiscardSounding(useSoundingStore.getState())?.soundingId).toBe("0");
    before.resolveDiscard(false);
    expect(useSoundingStore.getState().open).toBe(before.open);
    expect(useSoundingStore.getState().pendingChanges).toEqual([]);
    before.add(summary("new"));
    before.resolveDiscard(true);
    expect(ids()).toHaveLength(MAX_OPEN);
    expect(ids()).not.toContain("0");
    expect(ids()[0]).toBe("new");
    expect(useSoundingStore.getState().activeId).toBe("new");
    expect(useSoundingStore.getState().views["0"]).toBeUndefined();
    expect(useSoundingStore.getState().views["1"]).toBe(edited);
  });

  it("keeps a reopened modified sounding and its history without eviction or confirmation", () => {
    fillEdited();
    useSoundingStore.getState().add(summary("0"));
    expect(ids()).toHaveLength(MAX_OPEN);
    expect(ids()[0]).toBe("0");
    expect(useSoundingStore.getState().views["0"]).toBe(edited);
    expect(useSoundingStore.getState().pendingChanges).toEqual([]);
  });

  it("guards Close, retains edits on cancel, and discards only the confirmed sounding", () => {
    const { add, setView, close, resolveDiscard } = useSoundingStore.getState();
    add(summary("a"));
    add(summary("b"));
    setView("b", edited);
    close("b");
    close("b");
    expect(ids()).toEqual(["b", "a"]);
    expect(useSoundingStore.getState().activeId).toBe("b");
    expect(useSoundingStore.getState().pendingChanges).toHaveLength(1);
    resolveDiscard(false);
    expect(useSoundingStore.getState().views.b).toBe(edited);
    close("b");
    resolveDiscard(true);
    expect(ids()).toEqual(["a"]);
    expect(useSoundingStore.getState().activeId).toBe("a");
    expect(useSoundingStore.getState().views.b).toBeUndefined();
  });

  it("protects redo work after undo to the original, and chart options without profile edits", () => {
    const { add, setView, close, resolveDiscard } = useSoundingStore.getState();
    add(summary("undo"));
    const undone = { ...edited, cursor: 0 };
    expect(undone.history[undone.cursor]).toEqual([]);
    setView("undo", undone);
    close("undo");
    expect(ids()).toEqual(["undo"]);
    resolveDiscard(false);
    expect(useSoundingStore.getState().views.undo).toBe(undone);
    add(summary("options"));
    setView("options", { ...INITIAL_VIEW, options: { chart: "winter" } });
    close("options");
    expect(pendingDiscardSounding(useSoundingStore.getState())?.soundingId).toBe("options");
    resolveDiscard(false);
    setView("options", INITIAL_VIEW);
    close("options");
    expect(ids()).toEqual(["undo"]);
  });

  it("does not let late view updates recreate closed or evicted local work", () => {
    const { add, close, setView } = useSoundingStore.getState();
    add(summary("closed"));
    close("closed");
    setView("closed", edited);
    add(summary("evicted"));
    for (let index = 0; index < MAX_OPEN; index++) add(summary(String(index)));
    setView("evicted", edited);
    expect(useSoundingStore.getState().views).toEqual({});
    expect(ids()).not.toContain("closed");
    expect(ids()).not.toContain("evicted");
  });

  it("serializes concurrent opens and closes without exceeding the cap or reusing discard approval", () => {
    fillEdited();
    const { add, close, resolveDiscard } = useSoundingStore.getState();
    add(summary("first"));
    add(summary("second"));
    close("1");
    expect(useSoundingStore.getState().pendingChanges).toHaveLength(3);
    resolveDiscard(true);
    expect(ids()).toHaveLength(MAX_OPEN);
    expect(ids()[0]).toBe("second");
    expect(ids()).not.toContain("first");
    expect(ids()).toContain("1");
    expect(pendingDiscardSounding(useSoundingStore.getState())?.soundingId).toBe("1");
    resolveDiscard(false);
    expect(ids()).toContain("1");
    expect(useSoundingStore.getState().views["1"]).toBe(edited);
    expect(useSoundingStore.getState().pendingChanges).toEqual([]);
  });
});
