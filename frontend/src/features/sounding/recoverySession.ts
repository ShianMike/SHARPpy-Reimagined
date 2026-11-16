import { at, ContractError, expectArray, expectExactKeys, expectInteger, expectObject, expectOneOf, expectString } from "../../lib/contracts/guards";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { parseViewEdits } from "../../lib/contracts/soundingView";
import { MAX_OPEN, type SoundingState } from "../../stores/soundingStore";
import { RENDER_CHOICES, type SoundingSummary } from "../../types/soundings";
import type { SoundingMode } from "../../types/soundingView";
import type { ViewState } from "./viewHistory";

export interface RecoverySession {
  version: 1; savedAt: number; open: SoundingSummary[]; activeId: string | null;
  mode: SoundingMode; choice: SoundingState["choice"];
  views: Record<string, ViewState>; timeIndices: Record<string, number>;
}
export function parseRecoverySession(value: unknown): RecoverySession {
  const raw = expectObject(value, "session");
  expectExactKeys(raw, "session", ["version", "savedAt", "open", "activeId", "mode", "choice", "views", "timeIndices"]);
  expectOneOf(raw.version, "session.version", [1]);
  const open = expectArray(raw.open, "session.open", value => parseSoundingSummary(value));
  if (open.length === 0 || open.length > MAX_OPEN || new Set(open.map(item => item.soundingId)).size !== open.length)
    throw new ContractError("session.open", "must contain 1–12 distinct soundings");
  const activeId = raw.activeId === null ? null : expectString(raw.activeId, "session.activeId");
  if (activeId !== null && !open.some(item => item.soundingId === activeId)) throw new ContractError("session.activeId", "must refer to an open sounding");
  const views = Object.fromEntries(Object.entries(expectObject(raw.views, "session.views")).map(([key, value]) => {
    const owner = open.find(item => key === item.soundingId || key.startsWith(`${item.soundingId}:`));
    const time = owner === undefined ? NaN : key === owner.soundingId ? 0 : Number(key.slice(owner.soundingId.length + 1));
    if (owner === undefined || !Number.isInteger(time) || time < 0 || time >= owner.timeCount) throw new ContractError(`session.views.${key}`, "has no matching sounding time");
    const view = expectObject(value, key);
    expectExactKeys(view, key, ["history", "cursor", "options"]);
    const history = expectArray(view.history, at(key, "history"), (value, path) => {
      const edits = parseViewEdits(value, path);
      if (edits.length > 2000) throw new ContractError(path, "has too many edited levels");
      return edits;
    });
    if (history.length === 0 || history.length > 33) throw new ContractError(key, "has invalid edit history");
    const options = expectObject(view.options, at(key, "options"));
    expectExactKeys(options, key, [], ["chart", "panel"]);
    return [key, { history, cursor: expectInteger(view.cursor, at(key, "cursor"), { min: 0, max: history.length - 1 }),
      options: Object.fromEntries(Object.entries(options).map(([name, value]) => [name, expectString(value, at(key, name), { minLength: 1, maxLength: 300 })])) }];
  }));
  const timeIndices = Object.fromEntries(Object.entries(expectObject(raw.timeIndices, "session.timeIndices")).map(([id, index]) => {
    const owner = open.find(item => item.soundingId === id);
    if (owner === undefined) throw new ContractError(id, "has no matching sounding");
    return [id, expectInteger(index, id, { min: 0, max: owner.timeCount - 1 })];
  }));
  return { version: 1, savedAt: expectInteger(raw.savedAt, "session.savedAt", { min: 0 }), open, activeId,
    mode: expectOneOf(raw.mode, "session.mode", ["severe", "winter"]), choice: expectOneOf(raw.choice, "session.choice", RENDER_CHOICES), views, timeIndices };
}

export function captureRecoverySession(state: SoundingState): RecoverySession | null {
  return state.open.length === 0 ? null : { version: 1, savedAt: Date.now(), open: state.open, activeId: state.activeId,
    choice: state.choice, mode: state.mode, views: state.views, timeIndices: state.timeIndices };
}
