import { expectExactKeys, expectObject, expectString } from "./guards";
export interface SoundingSnapshot { soundingId: string; payload: string }
export const MAX_SNAPSHOT_CHARS = 7 * 1024 * 1024 + 512 * 1024;
export function parseSoundingSnapshot(value: unknown): SoundingSnapshot {
  const raw = expectObject(value, "snapshot");
  expectExactKeys(raw, "snapshot", ["soundingId", "payload"]);
  return { soundingId: expectString(raw.soundingId, "snapshot.soundingId", { minLength: 1, maxLength: 64 }),
    payload: expectString(raw.payload, "snapshot.payload", { minLength: 1, maxLength: MAX_SNAPSHOT_CHARS }) };
}
