import summary from "@contracts/public/soundings/summary.example.json";
import { expect, it } from "vitest";
import { parseSoundingSnapshot } from "../../lib/contracts/soundingRecovery";
import { parseRecoverySession } from "./recoverySession";

const session = { version: 1, savedAt: 1, open: [summary], activeId: summary.soundingId, mode: "winter", choice: "MU",
  views: { [summary.soundingId]: { history: [[], [{ index: 0, values: { tmpc: -7.25 } }]], cursor: 0, options: { panel: "HEIGHT CHARTS" } } }, timeIndices: {} };

it("retains redo history and view selections without changing any profile values", () => {
  expect(parseRecoverySession(structuredClone(session))).toEqual(session);
});
it("rejects unsupported, inconsistent and malformed saved sessions", () => {
  for (const patch of [{ version: 2 }, { mode: "other" }, { activeId: "missing" }, { open: [] }, { open: [summary, summary] },
    { timeIndices: { [summary.soundingId]: summary.timeCount } }, { views: { missing: session.views[summary.soundingId] } },
    { views: { [summary.soundingId]: { ...session.views[summary.soundingId], cursor: 3 } } }]) {
    expect(() => parseRecoverySession({ ...session, ...patch })).toThrow();
  }
});
it("validates snapshot envelopes before they enter local recovery storage", () => {
  expect(parseSoundingSnapshot({ soundingId: "abc", payload: "source" })).toEqual({ soundingId: "abc", payload: "source" });
  for (const payload of [null, 123, ""]) expect(() => parseSoundingSnapshot({ soundingId: "abc", payload })).toThrow();
});
