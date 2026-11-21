// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { MAX_FORECAST_POLYGONS, type ForecastDrawingResult, type ForecastPolygon, type ForecastProduct } from "../types/forecastDrawing";
import { readForecastDrawing, writeForecastDrawing } from "./forecastDrawingStorage";
import { useForecastDrawingStore } from "./forecastDrawingStore";
import { useForecastStore } from "./forecastStore";

const store = useForecastDrawingStore;
const high = { id: "cat-high", level: 5, hatch: false, points: [[-101, 38], [-99, 35], [-96, 38]] } as const;
const moderate = {
  id: "cat-moderate", sourceId: high.id, level: 4, hatch: false,
  points: [[-102, 39], [-99, 34], [-95, 39]],
} as const;
const emptyResult: ForecastDrawingResult = { areas: [], clippedHatches: [], hiddenHatches: [], generatedContours: [] };

function mockBackend(result = emptyResult) {
  return {
    buildForecastDrawing: vi.fn<(product: ForecastProduct, polygons: readonly ForecastPolygon[]) => Promise<ForecastDrawingResult>>()
      .mockResolvedValue(result),
  };
}

afterEach(() => {
  localStorage.clear();
  store.setState(store.getInitialState(), true);
  useForecastStore.setState(useForecastStore.getInitialState(), true);
});

it("copies editable vertices from saved categorical polygons and their authored adjustments", async () => {
  const thunderstorms = { ...high, id: "thunderstorms", level: 0 };
  const hatch = { ...high, id: "hatch", hatch: true };
  const backend = mockBackend();
  const actions = store.getState();
  store.setState((state) => ({ polygons: { ...state.polygons, cat: [high, moderate, thunderstorms, hatch] } }));

  expect(actions.copyCategoricalOutline(high.id)).toBe(false);
  actions.setEnabled(true);
  expect(actions.copyCategoricalOutline(high.id)).toBe(true);
  expect(store.getState().copiedOutline).toEqual({ level: 5, points: high.points });
  expect(actions.copyCategoricalOutline(moderate.id)).toBe(true);
  const copied = store.getState().copiedOutline;
  expect(copied).toEqual({ level: 4, points: moderate.points });
  expect(copied?.points).not.toBe(moderate.points);
  expect(copied?.points[0]).not.toBe(moderate.points[0]);

  expect(actions.copyCategoricalOutline(thunderstorms.id)).toBe(false);
  expect(actions.copyCategoricalOutline(hatch.id)).toBe(false);
  expect(actions.copyCategoricalOutline("missing")).toBe(false);
  useForecastStore.setState({ exporting: true });
  expect(actions.copyCategoricalOutline(high.id)).toBe(false);
  useForecastStore.setState({ exporting: false });

  actions.startEdit(moderate.id);
  actions.moveVertex(0, [-103, 39]);
  expect(actions.copyCategoricalOutline(moderate.id)).toBe(false);
  await actions.finish(backend);
  expect(store.getState().polygons.cat[1]?.points[0]).toEqual([-103, 39]);
  expect(store.getState().copiedOutline).toBe(copied);
});

it("pastes an unadjusted edit seed from the current categorical result", async () => {
  const generated = { sourceId: high.id, level: 1, points: [[-104, 40], [-99, 33], [-94, 40]] } as const;
  const thunder = { ...generated, level: 0 };
  const result = { ...emptyResult, generatedContours: [generated, thunder] };
  const backend = mockBackend(result);
  const actions = store.getState();
  store.setState((state) => ({ polygons: { ...state.polygons, cat: [high] } }));
  actions.setEnabled(true);
  await actions.restore(backend);
  expect(actions.copyCategoricalOutline(thunder)).toBe(false);
  expect(actions.copyCategoricalOutline(generated)).toBe(true);
  const copied = store.getState().copiedOutline;
  expect(copied).toEqual({ level: 1, points: generated.points });
  expect(copied?.points).not.toBe(generated.points);

  const updated = { ...generated, points: [[-105, 40], [-99, 33], [-94, 40]] } as const;
  store.setState({ results: { cat: { ...result, generatedContours: [updated, thunder] } } });
  expect(actions.copyCategoricalOutline(generated)).toBe(false);
  actions.setProduct("torn");
  backend.buildForecastDrawing.mockResolvedValueOnce(emptyResult);
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(true);
  const [pasted] = store.getState().polygons.torn;
  if (pasted === undefined) throw new Error("The pasted edit seed should have been saved.");
  expect(backend.buildForecastDrawing).toHaveBeenLastCalledWith("torn", [
    { id: pasted.id, level: 2, hatch: false, points: generated.points },
  ]);
  expect(store.getState().copiedOutline).toBe(copied);
});

it.each([["torn", 10], ["hail", 15], ["wind", 30]] as const)(
  "pastes an independent %s area at %i percent and saves it",
  async (product, level) => {
    const existing = { ...high, id: "existing-probability", level };
    const backend = mockBackend();
    const actions = store.getState();
    store.setState((state) => ({ polygons: { ...state.polygons, cat: [high, moderate], [product]: [existing] } }));
    actions.setEnabled(true);
    expect(actions.copyCategoricalOutline(moderate.id)).toBe(true);
    const copied = store.getState().copiedOutline;
    actions.setProduct(product);
    actions.setRisk(level, false);

    await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(true);
    const state = store.getState();
    const [saved, pasted] = state.polygons[product];
    if (pasted === undefined) throw new Error("The pasted area should have been saved.");
    expect(saved).toBe(existing);
    expect(pasted.id).not.toBe(moderate.id);
    expect(pasted.id).not.toBe(existing.id);
    expect(pasted).toEqual({ id: pasted.id, level, hatch: false, points: moderate.points });
    expect(pasted.points).not.toBe(copied?.points);
    expect(state.selectedId).toBe(pasted.id);
    expect(state.results[product]).toBe(emptyResult);
    expect(state.polygons.cat).toEqual([high, moderate]);
    expect(backend.buildForecastDrawing).toHaveBeenCalledExactlyOnceWith(product, [existing, pasted]);
    expect(readForecastDrawing(product)).toEqual([existing, pasted]);
    expect(state.copiedOutline).toBe(copied);
    expect(store.getInitialState().copiedOutline).toBeNull();
  },
);

it("applies an explicit probability from a conditional intensity selection", async () => {
  const backend = mockBackend();
  const actions = store.getState();
  store.setState((state) => ({ polygons: { ...state.polygons, cat: [high] } }));
  actions.setEnabled(true);
  actions.copyCategoricalOutline(high.id);
  actions.setProduct("torn");
  actions.setRisk(2, true);

  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  await expect(actions.pasteCategoricalOutline(backend, 10)).resolves.toBe(true);

  const state = store.getState();
  const [pasted] = state.polygons.torn;
  expect(pasted).toMatchObject({ level: 10, hatch: false, points: high.points });
  expect(state).toMatchObject({ level: 10, hatch: false, selectedId: pasted?.id });
  expect(backend.buildForecastDrawing).toHaveBeenCalledExactlyOnceWith("torn", [pasted]);
});

it("rejects an exact duplicate after changing vertex order, and allows the same outline at another probability", async () => {
  const existing = { ...high, id: "existing", level: 2, points: high.points.toReversed() };
  const backend = mockBackend();
  const actions = store.getState();
  store.setState((state) => ({ polygons: { ...state.polygons, cat: [high], torn: [existing] } }));
  actions.setEnabled(true);
  actions.copyCategoricalOutline(high.id);
  actions.setProduct("torn");
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  actions.setRisk(5, false);
  await expect(actions.pasteCategoricalOutline(backend, 1)).resolves.toBe(false);
  await expect(actions.pasteCategoricalOutline(backend, 2)).resolves.toBe(false);
  expect(backend.buildForecastDrawing).not.toHaveBeenCalled();
  expect(store.getState().polygons.torn).toEqual([existing]);
  expect(store.getState().level).toBe(5);
  await expect(actions.pasteCategoricalOutline(backend, 10)).resolves.toBe(true);
  expect(store.getState().polygons.torn).toHaveLength(2);
  expect(store.getState().level).toBe(10);
});

it("requires a saved outline, an idle enabled tool, a probability, and room for another area", async () => {
  const wind = { id: "existing-wind", level: 15, hatch: false, points: moderate.points };
  const backend = mockBackend();
  const actions = store.getState();
  store.setState((state) => ({ polygons: { ...state.polygons, cat: [high], wind: [wind] } }));
  actions.setEnabled(true);
  actions.setProduct("wind");
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  actions.setProduct("cat");
  actions.copyCategoricalOutline(high.id);
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  actions.setProduct("wind");
  expect(actions.copyCategoricalOutline(high.id)).toBe(false);

  actions.setEnabled(false);
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  actions.setEnabled(true);
  useForecastStore.setState({ exporting: true });
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  useForecastStore.setState({ exporting: false });
  actions.startDraw();
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  actions.cancel();
  actions.startEdit(wind.id);
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  actions.cancel();
  actions.setRisk(2, true);
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  actions.setRisk(2, false);
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);

  const full = Array.from({ length: MAX_FORECAST_POLYGONS }, (_, index) => ({ ...wind, id: String(index) }));
  store.setState((state) => ({ polygons: { ...state.polygons, wind: full } }));
  actions.setRisk(15, false);
  await expect(actions.pasteCategoricalOutline(backend)).resolves.toBe(false);
  expect(store.getState().error).toContain("Remove a shape");
  expect(store.getState().copiedOutline).toEqual({ level: 5, points: high.points });
  expect(backend.buildForecastDrawing).not.toHaveBeenCalled();
});

it("keeps the selection, saved geometry and clipboard intact when an explicit paste fails", async () => {
  const existing = { ...high, id: "existing-hail", level: 15 };
  const current = [existing];
  const backend = mockBackend();
  backend.buildForecastDrawing.mockRejectedValueOnce(new Error("The outline crosses itself."));
  writeForecastDrawing("hail", current);
  store.setState((state) => ({
    polygons: { ...state.polygons, cat: [high, moderate], hail: current },
    results: { hail: emptyResult },
  }));
  const actions = store.getState();
  actions.setEnabled(true);
  actions.copyCategoricalOutline(moderate.id);
  const copied = store.getState().copiedOutline;
  actions.setProduct("hail");
  actions.selectPolygon(existing.id);
  actions.setRisk(2, true);
  const selection = { selectedId: existing.id, level: 2, hatch: true };
  const pending = actions.pasteCategoricalOutline(backend, 30);
  expect(store.getState()).toMatchObject({ ...selection, saving: true });
  expect(store.getState().polygons.hail).toBe(current);
  await expect(actions.pasteCategoricalOutline(backend, 45)).resolves.toBe(false);
  expect(backend.buildForecastDrawing).toHaveBeenCalledTimes(1);

  await expect(pending).resolves.toBe(false);
  expect(store.getState()).toMatchObject({ ...selection, saving: false, error: "The outline crosses itself." });
  expect(store.getState().polygons.hail).toBe(current);
  expect(store.getState().results.hail).toBe(emptyResult);
  expect(store.getState().copiedOutline).toBe(copied);
  expect(readForecastDrawing("hail")).toEqual(current);
  await expect(actions.pasteCategoricalOutline(backend, 30)).resolves.toBe(true);
  expect(store.getState().polygons.hail).toHaveLength(2);
  expect(store.getState()).toMatchObject({ level: 30, hatch: false });
});
