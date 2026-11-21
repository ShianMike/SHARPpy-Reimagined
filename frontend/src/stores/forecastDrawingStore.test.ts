// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { type ForecastDrawingResult, type ForecastPoint, type ForecastPolygon, type ForecastProduct } from "../types/forecastDrawing";
import { readForecastDrawing } from "./forecastDrawingStorage";
import { useForecastDrawingStore } from "./forecastDrawingStore";

const store = useForecastDrawingStore;
const triangle = [[-101, 38], [-99, 35], [-96, 38]] as const satisfies readonly ForecastPoint[];
const emptyResult = { areas: [], clippedHatches: [], hiddenHatches: [], generatedContours: [] } as const;

afterEach(() => {
  localStorage.clear();
  store.setState(store.getInitialState(), true);
});

it("validates each change from authored polygons and only saves successful edits", async () => {
  const buildForecastDrawing = vi.fn<(product: ForecastProduct, polygons: readonly ForecastPolygon[]) => Promise<ForecastDrawingResult>>()
    .mockResolvedValue(emptyResult);
  const backend = { buildForecastDrawing };
  store.getState().setEnabled(true);
  store.getState().setRisk(5, false);
  store.getState().startDraw();
  triangle.forEach((point) => { store.getState().addVertex(point); });
  store.getState().addVertex([-95, 39]);
  store.getState().undo();
  const finished = store.getState().finish(backend);
  const saving = store.getState();
  expect(saving.saving).toBe(true);
  saving.undo();
  saving.redo();
  expect(store.getState()).toBe(saving);
  await finished;
  expect(store.getState()).toMatchObject({ mode: "idle", undoSteps: [], redoSteps: [] });
  const authored = store.getState().polygons.cat[0];
  if (authored === undefined) throw new Error("The authored High Risk should be saved.");
  expect(buildForecastDrawing).toHaveBeenLastCalledWith("cat", [{ id: authored.id, level: 5, hatch: false, points: triangle }]);
  expect(readForecastDrawing("cat")).toEqual([authored]);

  store.getState().startEdit(authored.id);
  store.getState().moveVertex(1, [-103, 36]);
  store.getState().moveVertex(1, [-104, 36]);
  store.getState().undo();
  const invalidDraft = store.getState();
  buildForecastDrawing.mockRejectedValueOnce(new Error("The outline crosses itself."));
  await store.getState().finish(backend);
  expect(store.getState()).toMatchObject({ mode: "edit", error: "The outline crosses itself.", saving: false });
  expect(store.getState().undoSteps).toBe(invalidDraft.undoSteps);
  expect(store.getState().redoSteps).toBe(invalidDraft.redoSteps);
  expect(store.getState().polygons.cat).toEqual([authored]);
  expect(readForecastDrawing("cat")).toEqual([authored]);

  store.getState().moveVertex(1, [-99, 34]);
  await store.getState().finish(backend);
  store.getState().undo();
  store.getState().redo();
  expect(store.getState()).toMatchObject({ mode: "idle", points: [], undoSteps: [], redoSteps: [] });
  expect(readForecastDrawing("cat")[0]?.points).toEqual([[-101, 38], [-99, 34], [-96, 38]]);
  store.getState().setProduct("wind");
  expect(store.getState().polygons.wind).toEqual([]);
  store.getState().setProduct("cat");
  await store.getState().deletePolygon(authored.id, backend);
  expect(buildForecastDrawing).toHaveBeenLastCalledWith("cat", []);
  expect(readForecastDrawing("cat")).toEqual([]);
});

it("keeps a saved hatch editable while its probability envelope is missing", async () => {
  const polygon = { id: "wind-hatch", level: 2, hatch: true, points: triangle };
  const result = { ...emptyResult, hiddenHatches: [polygon.id] };
  const backend = { buildForecastDrawing: vi.fn(() => Promise.resolve(result)) };
  store.setState((state) => ({ polygons: { ...state.polygons, wind: [polygon] } }));
  store.getState().setEnabled(true);
  store.getState().setProduct("wind");
  await store.getState().restore(backend);
  expect(backend.buildForecastDrawing).toHaveBeenCalledExactlyOnceWith("wind", [polygon]);
  expect(store.getState().results.wind).toEqual(result);
  store.getState().startEdit(polygon.id);
  expect(store.getState()).toMatchObject({ mode: "edit", points: triangle, selectedId: polygon.id });
});

it("keeps inserted vertices and whole-area moves in the draft until Finish area", async () => {
  const original = { id: "outlook", level: 5, hatch: false, points: triangle };
  const translated = [[-100, 39], [-99, 37.5], [-98, 36], [-95, 39]] as const;
  const backend = { buildForecastDrawing: vi.fn<(_product: ForecastProduct, _polygons: readonly ForecastPolygon[]) => Promise<ForecastDrawingResult>>()
    .mockResolvedValue(emptyResult) };
  store.setState((state) => ({ enabled: true, polygons: { ...state.polygons, cat: [original] } }));
  store.getState().startEdit(original.id);
  store.getState().insertVertex(0, [-100, 36.5]);
  expect(store.getState().points).toEqual([triangle[0], [-100, 36.5], triangle[1], triangle[2]]);
  const inserted = store.getState().points;
  store.getState().replaceEditPoints(translated);
  store.getState().undo();
  expect(store.getState().points).toBe(inserted);
  expect(store.getState().polygons.cat).toEqual([original]);
  store.getState().cancel();
  expect(store.getState()).toMatchObject({ undoSteps: [], redoSteps: [] });
  expect(store.getState().polygons.cat).toEqual([original]);
  expect(backend.buildForecastDrawing).not.toHaveBeenCalled();

  store.getState().startEdit(original.id);
  store.getState().setEditTool("move");
  store.getState().replaceEditPoints(translated);
  store.getState().setEditTool("reshape");
  expect(store.getState().points).toEqual(translated);
  store.getState().setEditTool("move");
  await store.getState().finish(backend);
  expect(backend.buildForecastDrawing).toHaveBeenCalledExactlyOnceWith("cat", [{ ...original, points: translated }]);
  expect(readForecastDrawing("cat")).toEqual([{ ...original, points: translated }]);
  expect(store.getState()).toMatchObject({ mode: "idle", editTool: "reshape" });
});

it("only persists changes to a generated contour and keeps its source and risk on re-edit", async () => {
  const source = { id: "high", level: 5, hatch: false, points: triangle };
  const generated = { sourceId: source.id, level: 4, points: [[-102, 39], [-99, 34], [-95, 39]] as const };
  const backend = { buildForecastDrawing: vi.fn<(_product: ForecastProduct, _polygons: readonly ForecastPolygon[]) => Promise<ForecastDrawingResult>>()
    .mockResolvedValue(emptyResult) };
  store.setState((state) => ({ enabled: true, polygons: { ...state.polygons, cat: [source] } }));

  expect(store.getState().startEdit(source.id)).toBe(true);
  expect(store.getState().startEdit(generated)).toBe(true);
  expect(store.getState()).toMatchObject({ mode: "edit", level: 4, points: generated.points });
  const generatedId = store.getState().selectedId;
  store.getState().moveVertex(0, [-104, 39]);
  store.getState().undo();
  expect(store.getState().points).toBe(generated.points);
  expect(store.getState().startEdit(generated)).toBe(true);
  expect(store.getState().selectedId).toBe(generatedId);
  expect(store.getState().redoSteps).toHaveLength(1);
  await store.getState().finish(backend);
  expect(backend.buildForecastDrawing).not.toHaveBeenCalled();
  expect(store.getState()).toMatchObject({ mode: "idle", selectedId: null, undoSteps: [], redoSteps: [] });

  store.getState().startEdit(generated);
  store.getState().moveVertex(0, [-104, 39]);
  expect(store.getState().startEdit(source.id)).toBe(false);
  store.getState().setRisk(1, false);
  expect(store.getState().level).toBe(4);
  const id = store.getState().selectedId;
  const points = store.getState().points;
  await store.getState().finish(backend);
  const edited = { id, level: 4, hatch: false, points, sourceId: source.id };
  expect(backend.buildForecastDrawing).toHaveBeenCalledExactlyOnceWith("cat", [source, edited]);
  expect(readForecastDrawing("cat")).toEqual([source, edited]);

  if (id === null) throw new Error("The adjusted contour should have an ID.");
  store.getState().startEdit(id);
  store.getState().moveVertex(1, [-99, 33]);
  await store.getState().finish(backend);
  expect(readForecastDrawing("cat")).toEqual([source, { ...edited, points: points.with(1, [-99, 33]) }]);
  await store.getState().deletePolygon(id, backend);
  expect(backend.buildForecastDrawing).toHaveBeenLastCalledWith("cat", [source]);
});

it("keeps adjusted areas when their source changes risk level or is removed", async () => {
  const source = { id: "high", level: 5, hatch: false, points: triangle };
  const moderate = { ...source, id: "moderate", level: 4 };
  const slight = { ...source, id: "slight", level: 2 };
  const moderateAdjustment = { ...moderate, sourceId: source.id };
  const slightAdjustment = { ...slight, sourceId: source.id };
  const backend = { buildForecastDrawing: vi.fn<(_product: ForecastProduct, _polygons: readonly ForecastPolygon[]) => Promise<ForecastDrawingResult>>()
    .mockResolvedValue(emptyResult) };
  store.setState((state) => ({ enabled: true, polygons: { ...state.polygons, cat: [source, moderateAdjustment, slightAdjustment] } }));

  store.getState().startEdit(source.id);
  store.getState().setRisk(3, false);
  await store.getState().finish(backend);
  expect(readForecastDrawing("cat")).toEqual([{ ...source, level: 3 }, moderate, slightAdjustment]);
  await store.getState().deletePolygon(source.id, backend);
  expect(readForecastDrawing("cat")).toEqual([moderate, slight]);
});
