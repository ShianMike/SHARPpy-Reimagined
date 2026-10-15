import { afterEach, describe, expect, it, vi } from "vitest";
import { BackendClient } from "./client";
import { createTransport } from "./httpTransport";
import { BackendFailure } from "./errors";
import { encodeShrf } from "../contracts/frames";

afterEach(() => { vi.unstubAllGlobals(); });

describe("live browser transport", () => {
  it("posts the operation and preserves JSON and binary responses", async () => {
    const frame = encodeShrf({ kind: "radar" }, new Uint8Array([1, 2, 3]));
    const request = vi.fn()
      .mockResolvedValueOnce(new Response('{"places":[]}'))
      .mockResolvedValueOnce(new Response(frame, { headers: { "Content-Type": "application/octet-stream" } }));
    vi.stubGlobal("fetch", request);
    const transport = createTransport();
    await expect(transport.call("get_place_labels")).resolves.toEqual({ places: [] });
    expect(request).toHaveBeenCalledWith("/__backend", expect.objectContaining({
      method: "POST", body: JSON.stringify({ id: 1, method: "get_place_labels", params: {} }),
    }));
    await expect(transport.call("get_radar_frame", { product: "reflectivity" })).resolves.toEqual(frame);
  });

  it("passes provider failures through BackendClient without replacing them with fixtures", async () => {
    const error = { code: "OVERLAY_FETCH_FAILED", message: "Provider unavailable.", retryable: true };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(error), { status: 502 })));
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await expect(new BackendClient(createTransport()).getRadarFrame(null)).rejects.toMatchObject({
      error, name: "BackendFailure",
    });
  });

  it("uses the existing polling fallback and maps connection loss to a transport failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = new BackendClient(createTransport());
    await expect(client.onBridgeStatus(() => undefined)).rejects.toBeInstanceOf(BackendFailure);
    await expect(client.getBackendHealth()).rejects.toMatchObject({ error: { code: "TRANSPORT_FAILED" } });
  });
});
