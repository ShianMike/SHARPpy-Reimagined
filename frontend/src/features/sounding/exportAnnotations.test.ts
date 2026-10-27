// @vitest-environment jsdom
import example from "@contracts/public/soundings/view.example.json";
import { afterEach, expect, it, vi } from "vitest";
import { parseSoundingView } from "../../lib/contracts/soundingView";
import { annotatedSoundingPng, editedLevelAnnotations } from "./exportAnnotations";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("annotates only changed fields in the same units and precision as the editor, including signed wind", () => {
  const view = parseSoundingView({ ...structuredClone(example), width: 4564, height: 3077, image: btoa("\x89PNG\r\n\x1a\n") });
  view.edits = [{ index: 0, values: { tmpc: 20, u: -20 } }];
  const temperature = view.points.find((point) => point.id === "tmpc:0")?.values[0];
  const wind = view.points.find((point) => point.id === "wind:0")?.values[0];
  if (temperature === undefined || wind === undefined) throw new Error("Missing editable fixture fields");
  temperature.value = 20; wind.value = -20;
  const source = view.image;
  const lines = editedLevelAnnotations(view, { temperature: "°F", wind: "km/h" });
  expect(lines).toHaveLength(2);
  expect(lines[0]).toBe("Temperature · 986 hPa: Temperature 68 °F");
  expect(lines[1]).toContain("U wind -37.04 km/h");
  expect(temperature.value).toBe(20);
  expect(wind.value).toBe(-20);
  expect(view.image).toBe(source);
  view.edits = [];
  expect(editedLevelAnnotations(view, { temperature: "°F", wind: "mph" })).toEqual([]);
});

it("keeps unedited PNG bytes and appends annotations at full chart resolution for either export path", async () => {
  const view = parseSoundingView({ ...structuredClone(example), width: 4564, height: 3077, image: btoa("\x89PNG\r\n\x1a\n") });
  expect(await annotatedSoundingPng(view, [])).toBe(view.image);
  const bitmap = { close: vi.fn() };
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue(bitmap));
  const context = { drawImage: vi.fn(), fillRect: vi.fn(), fillText: vi.fn(), font: "", fillStyle: "", textBaseline: "" };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  const png = new Blob(["annotated"], { type: "image/png" });
  let size = { width: 0, height: 0 };
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(function (this: HTMLCanvasElement, callback) {
    size = { width: this.width, height: this.height }; callback(png);
  });
  expect(await annotatedSoundingPng(view, ["Temperature · 986 hPa: Temperature 68 °F"])).toBe(png);
  expect(size.width).toBe(view.width);
  expect(size.height).toBeGreaterThan(view.height);
  expect(context.drawImage).toHaveBeenCalledWith(bitmap, 0, 0);
  expect(context.fillText.mock.calls.at(-1)?.[0]).toContain("68 °F");
  expect(Number(context.fillText.mock.calls.at(-1)?.[2])).toBeGreaterThan(view.height);
  expect(bitmap.close).toHaveBeenCalledOnce();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  await expect(annotatedSoundingPng(view, ["Changed level"])).rejects.toThrow("annotations are unavailable");
  expect(bitmap.close).toHaveBeenCalledTimes(2);
});
