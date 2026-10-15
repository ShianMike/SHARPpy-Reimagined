import type { ViewEdit, ViewOptions } from "../../types/soundingView";

export interface ViewState { history: ViewEdit[][]; cursor: number; options: ViewOptions }
export const INITIAL_VIEW: ViewState = { history: [[]], cursor: 0, options: {} };
export function appendEdit(state: ViewState, edits: ViewEdit[]): ViewState {
  // shortcut: retain 32 undo steps; increase only with a measured memory budget.
  const history = [...state.history.slice(0, state.cursor + 1), edits].slice(-33);
  return { ...state, history, cursor: history.length - 1 };
}
export function replaceValues(edits: ViewEdit[], index: number, values: Record<string, number>): ViewEdit[] {
  const old = edits.find((edit) => edit.index === index);
  return [...edits.filter((edit) => edit.index !== index), { index, values: { ...old?.values, ...values } }];
}
