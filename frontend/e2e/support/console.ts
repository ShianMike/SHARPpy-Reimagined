// Console warning assertions (DESIGN-tooling T8): each spec states the
// console.warn entries it expects; any other warning fails it, except the
// browser's own GPU driver notices listed here.
import type { ConsoleEntry } from "./test";
import { expect } from "./test";

/**
 * Warnings the browser itself logs about WebGL (not the app): Edge reports
 * one texSubImage2D notice per map load (PROGRESS, FEAT-003 note).
 */
export const BROWSER_NOISE: readonly RegExp[] = [/^WebGL: INVALID_VALUE: texSubImage2D: no canvas/u, /GPU stall due to ReadPixels/u];

export function warnings(messages: readonly ConsoleEntry[]): string[] {
  return messages.filter((entry) => entry.type === "warning").map((entry) => entry.text);
}

/**
 * Every expected pattern matches the stated number of warnings (or at least
 * one when no count is given), and no other app warning appears.
 */
export function expectWarnings(
  messages: readonly ConsoleEntry[],
  expected: readonly { pattern: RegExp; count?: number }[],
): void {
  const all = warnings(messages);
  for (const { pattern, count } of expected) {
    const matched = all.filter((text) => pattern.test(text)).length;
    if (count === undefined) {
      expect(matched, `warnings matching ${String(pattern)}`).toBeGreaterThan(0);
    } else {
      expect(matched, `warnings matching ${String(pattern)}`).toBe(count);
    }
  }
  const unexpected = all.filter(
    (text) => !expected.some(({ pattern }) => pattern.test(text)) && !BROWSER_NOISE.some((noise) => noise.test(text)),
  );
  expect(unexpected, "unexpected console.warn entries").toEqual([]);
}
