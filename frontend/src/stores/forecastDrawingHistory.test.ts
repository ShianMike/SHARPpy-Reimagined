// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { MAX_FORECAST_VERTICES, type ForecastPoint, type ForecastPolygon } from "../types/forecastDrawing";
import { useForecastDrawingStore } from "./forecastDrawingStore";

const store = useForecastDrawingStore;
const triangle = [[-101, 38], [-99, 35], [-96, 38]] as const satisfies readonly ForecastPoint[];
const emptyResult = { areas: [], clippedHatches: [], hiddenHatches: [], generatedContours: [] } as const;

afterEach(() => {
  localStorage.clear();
  store.setState(store.getInitialState(), true);
});

it("undoes drawing back to an empty draft and redoes each vertex without changing risk", () => {
  const actions = store.getState();
  actions.setEnabled(true);
  actions.startDraw();
  const empty = store.getState().points;
  triangle.forEach((point) => { actions.addVertex(point); });
  const drawn = store.getState().points;
  actions.setRisk(5, false);

  actions.undo();
  expect(store.getState().points).toEqual(triangle.slice(0, 2));
  actions.addVertex(triangle[1]);
  actions.undo();
  expect(store.getState().points).toEqual(triangle.slice(0, 1));
  actions.undo();
  expect(store.getState().points).toBe(empty);
  expect(store.getState()).toMatchObject({ mode: "draw", level: 5, undoSteps: [] });

  actions.redo();
  expect(store.getState().points).toEqual(triangle.slice(0, 1));
  actions.redo();
  expect(store.getState().points).toEqual(triangle.slice(0, 2));
  actions.redo();
  expect(store.getState().points).toBe(drawn);
  expect(store.getState().redoSteps).toEqual([]);
});

it("records vertex removals and nudges and clears redo after a new edit", () => {
  const original = { id: "outlook", level: 5, hatch: false, points: triangle };
  store.setState((state) => ({ enabled: true, polygons: { ...state.polygons, cat: [original] } }));
  const actions = store.getState();
  actions.startEdit(original.id);
  actions.removeVertex(1);
  expect(store.getState().undoSteps).toEqual([]);

  actions.insertVertex(0, [-100, 36.5]);
  const inserted = store.getState().points;
  actions.removeVertex(4);
  expect(store.getState().undoSteps).toHaveLength(1);
  actions.removeVertex(1);
  expect(store.getState().points).toEqual(triangle);
  actions.undo();
  expect(store.getState().points).toBe(inserted);

  const { undoSteps, redoSteps } = store.getState();
  actions.setRisk(3, false);
  actions.setEditTool("move");
  actions.replaceEditPoints([...inserted]);
  expect(store.getState().undoSteps).toBe(undoSteps);
  expect(store.getState().redoSteps).toBe(redoSteps);
  actions.redo();
  expect(store.getState()).toMatchObject({ level: 3, editTool: "move", points: triangle });

  actions.undo();
  actions.moveVertex(1, [-102, 37]);
  const nudged = store.getState().points;
  expect(nudged[1]).toEqual([-102, 37]);
  expect(store.getState().redoSteps).toEqual([]);
  actions.redo();
  expect(store.getState().points).toBe(nudged);
  actions.undo();
  expect(store.getState().points).toBe(inserted);
});

it("records a vertex drag once and rejects stray actions while the gesture is live", async () => {
  const original = { id: "outlook", level: 5, hatch: false, points: [...triangle, [-98, 40]] } satisfies ForecastPolygon;
  store.setState((state) => ({ enabled: true, polygons: { ...state.polygons, cat: [original] } }));
  const actions = store.getState();
  actions.startEdit(original.id);
  actions.moveVertex(1, [-99, 34]);
  actions.moveVertex(1, [-99, 33]);
  actions.undo();
  const baseline = store.getState();
  const gesture = actions.beginEditGesture();
  if (gesture === null) throw new Error("The vertex drag should start.");
  expect(store.getState().editGesture?.points).toBe(baseline.points);
  expect(actions.beginEditGesture()).toBeNull();
  expect(actions.startEdit(original.id)).toBe(false);

  actions.moveVertex(0, [-102, 38], gesture);
  actions.moveVertex(0, [-103, 38], gesture);
  const provisional = store.getState();
  expect(provisional.points[0]).toEqual([-103, 38]);
  expect(provisional.undoSteps).toBe(baseline.undoSteps);
  expect(provisional.redoSteps).toBe(baseline.redoSteps);

  actions.undo();
  actions.redo();
  actions.insertVertex(0, [-104, 38]);
  actions.removeVertex(0);
  actions.moveVertex(0, [-104, 38]);
  actions.replaceEditPoints(triangle);
  const backend = { buildForecastDrawing: vi.fn(() => Promise.resolve(emptyResult)) };
  await actions.finish(backend);
  expect(backend.buildForecastDrawing).not.toHaveBeenCalled();
  expect(store.getState()).toBe(provisional);

  actions.endEditGesture(gesture);
  expect(store.getState()).toMatchObject({ editGesture: null, undoSteps: [original.points, baseline.points], redoSteps: [] });
  actions.undo();
  expect(store.getState().points).toBe(baseline.points);
  actions.redo();
  expect(store.getState().points).toBe(provisional.points);
});

it("keeps the exact baseline and redo after a cancelled or zero-net area drag", () => {
  const original = { id: "outlook", level: 5, hatch: false, points: triangle };
  const translated = [[-100, 39], [-98, 36], [-95, 39]] as const;
  store.setState((state) => ({ enabled: true, polygons: { ...state.polygons, cat: [original] } }));
  const actions = store.getState();
  actions.startEdit(original.id);
  actions.replaceEditPoints(translated);
  actions.undo();
  const { undoSteps, redoSteps } = store.getState();
  const cancelled = actions.beginEditGesture();
  if (cancelled === null) throw new Error("The first area drag should start.");
  actions.replaceEditPoints(translated, cancelled);
  expect(store.getState().points).toBe(translated);
  actions.endEditGesture(cancelled, true);
  expect(store.getState().points).toBe(triangle);
  expect(store.getState().redoSteps).toBe(redoSteps);
  actions.replaceEditPoints(translated, cancelled);
  expect(store.getState().points).toBe(triangle);

  const gesture = actions.beginEditGesture();
  if (gesture === null) throw new Error("The second area drag should start.");
  actions.replaceEditPoints(translated, cancelled);
  actions.endEditGesture(cancelled);
  expect(store.getState().points).toBe(triangle);
  expect(store.getState().editGesture?.id).toBe(gesture);
  actions.replaceEditPoints(translated, gesture);
  actions.replaceEditPoints(triangle.map(([longitude, latitude]) => [longitude, latitude]), gesture);
  actions.endEditGesture(gesture);
  expect(store.getState().points).toBe(triangle);
  expect(store.getState().undoSteps).toBe(undoSteps);
  expect(store.getState().redoSteps).toBe(redoSteps);
  actions.redo();
  expect(store.getState().points).toBe(translated);
});

it("retains the most recent geometry steps up to the vertex limit", () => {
  const original = { id: "outlook", level: 5, hatch: false, points: triangle };
  store.setState((state) => ({ enabled: true, polygons: { ...state.polygons, cat: [original] } }));
  const actions = store.getState();
  actions.startEdit(original.id);
  for (let step = 1; step <= MAX_FORECAST_VERTICES + 1; step += 1) {
    actions.moveVertex(0, [-101, 38 + step / 1000]);
  }
  expect(store.getState().undoSteps).toHaveLength(MAX_FORECAST_VERTICES);
  for (let step = 0; step <= MAX_FORECAST_VERTICES; step += 1) actions.undo();
  expect(store.getState().points[0]).toEqual([-101, 38.001]);
  expect(store.getState().undoSteps).toEqual([]);
  expect(store.getState().redoSteps).toHaveLength(MAX_FORECAST_VERTICES);
});

it("clears history when changing areas, switching products, or disabling drawing", () => {
  const first = { id: "first", level: 5, hatch: false, points: triangle };
  const second = { ...first, id: "second" };
  store.setState((state) => ({ enabled: true, polygons: { ...state.polygons, cat: [first, second] } }));
  const actions = store.getState();
  actions.startEdit(first.id);
  actions.moveVertex(0, [-102, 38]);
  actions.undo();
  expect(store.getState().redoSteps).toHaveLength(1);
  expect(actions.startEdit(second.id)).toBe(true);
  expect(store.getState()).toMatchObject({ selectedId: second.id, undoSteps: [], redoSteps: [] });

  const gesture = actions.beginEditGesture();
  if (gesture === null) throw new Error("The area gesture should start.");
  expect(actions.startEdit(first.id)).toBe(false);
  actions.moveVertex(0, [-102, 38], gesture);
  actions.setProduct("wind");
  expect(store.getState()).toMatchObject({ mode: "idle", product: "wind", undoSteps: [], redoSteps: [], editGesture: null });
  actions.setProduct("cat");
  actions.startEdit(first.id);
  actions.moveVertex(0, [-103, 38], gesture);
  expect(store.getState().points).toBe(triangle);

  actions.moveVertex(0, [-102, 38]);
  expect(store.getState().undoSteps).toHaveLength(1);
  actions.setEnabled(false);
  expect(store.getState()).toMatchObject({ mode: "idle", points: [], undoSteps: [], redoSteps: [], editGesture: null });
});
