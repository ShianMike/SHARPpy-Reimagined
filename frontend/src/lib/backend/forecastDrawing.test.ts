import { expect, it, vi } from "vitest";
import { BackendClient } from "./client";
import { createTransport } from "./fixtureTransport";
import type { Transport } from "./transport";

const polygon = {
  id: "risk",
  level: 1,
  hatch: false,
  points: [[-101, 35], [-99, 35], [-100, 37]],
} as const;
const area = {
  label: "Marginal",
  description: "Marginal risk",
  fill: "#66a366",
  stroke: "#005500",
  rank: 1,
  hatch: false,
  hatchLevel: 0,
  rings: [polygon.points],
};
const contour = { sourceId: polygon.id, level: 0, points: polygon.points };
const drawing = { areas: [area], clippedHatches: [], hiddenHatches: [], generatedContours: [contour] };

function clientWithReply(reply: unknown) {
  const call = vi.fn<Transport["call"]>(() => Promise.resolve(reply));
  const client = new BackendClient({ call, listen: () => Promise.resolve(() => undefined) });
  return { client, call };
}

it("sends the requested polygons and parses the public drawing", async () => {
  const { client, call } = clientWithReply(drawing);
  const signal = new AbortController().signal;
  await expect(client.buildForecastDrawing("cat", [polygon], signal)).resolves.toEqual(drawing);
  const requestId = call.mock.calls[0]?.[1]?.requestId;
  expect(requestId).toMatch(/^[a-f\d]{32}$/u);
  expect(call).toHaveBeenCalledWith("build_forecast_drawing", { product: "cat", polygons: [polygon], requestId });
});

it.each([
  ["extra area fields", { ...drawing, areas: [{ ...area, bounds: [-101, 35, -99, 37] }] }],
  ["invalid hatch IDs", { ...drawing, hiddenHatches: [1] }],
  ["invalid generated contour levels", { ...drawing, generatedContours: [{ ...contour, level: 10 }] }],
])("rejects a drawing with %s", async (_label, reply) => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const { client } = clientWithReply(reply);
  await expect(client.buildForecastDrawing("cat", [polygon])).rejects.toMatchObject({
    error: { code: "CONTRACT_VIOLATION" },
  });
});

it("returns a structured failure for fixture drawing", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const client = new BackendClient(createTransport(null));
  await expect(client.buildForecastDrawing("cat", [polygon])).rejects.toMatchObject({
    error: {
      code: "METHOD_NOT_FOUND",
      retryable: false,
      context: { operation: "build_forecast_drawing" },
    },
  });
});
