import { expect, it } from "vitest";
import { appendEdit, INITIAL_VIEW, replaceValues } from "./viewHistory";

it("merges edits at one level, branches after undo, and bounds history", () => {
  const edits = replaceValues([{ index: 2, values: { u: 10, v: 5 } }], 2, { u: 15 });
  expect(edits).toEqual([{ index: 2, values: { u: 15, v: 5 } }]);
  const first = appendEdit(INITIAL_VIEW, edits);
  const second = appendEdit(first, [{ index: 3, values: { u: 4 } }]);
  const branch = appendEdit({ ...second, cursor: 1 }, []);
  expect(branch.history).toEqual([[], edits, []]);
  let history = INITIAL_VIEW;
  for (let i = 0; i < 40; i++) history = appendEdit(history, [{ index: i, values: { u: i } }]);
  expect(history.history).toHaveLength(33);
  expect(history.history[history.cursor]?.[0]?.index).toBe(39);
});
