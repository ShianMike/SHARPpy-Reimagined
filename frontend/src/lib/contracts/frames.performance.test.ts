// Opt-in body-to-Blob benchmark; functional parser coverage lives in map.test.ts.
// SHARPPY_SHRF_BENCHMARK=1 npm exec -- vitest run src/lib/contracts/frames.performance.test.ts --maxWorkers=1
import { env, versions } from "node:process";
import radar from "@contracts/public/map/radar.shrf?url&inline";
import site from "@contracts/public/map/radar-site.shrf?url&inline";
import satellite from "@contracts/public/map/satellite.shrf?url&inline";
import { expect, it } from "vitest";
import { encodeShrf, parseRadarFrame, parseRadarSiteFrame, parseSatelliteFrame, parseShrf } from "./frames";

it.skipIf(env.SHARPPY_SHRF_BENCHMARK !== "1")("compares PNG Blob copies with identical parser bodies", async () => {
  const iterations = Number(env.SHARPPY_SHRF_ITERS ?? 20);
  if (!Number.isInteger(iterations) || iterations < 2 || iterations > 1000) throw new Error("SHARPPY_SHRF_ITERS must be 2 to 1000");
  const results: object[] = [];
  for (const [route, fixture, parse] of [
    ["radar", radar, parseRadarFrame],
    ["radar-site", site, parseRadarSiteFrame],
    ["satellite", satellite, parseSatelliteFrame],
  ] as const) {
    const bytes = Uint8Array.from(atob(fixture.split(",")[1] ?? ""), (c) => c.charCodeAt(0));
    const original = parseShrf(bytes);
    for (const synthetic of [false, true]) {
      const body = synthetic ? new Uint8Array(1024 * 1024) : original.body;
      if (synthetic) for (let offset = 0; offset < body.length; offset += original.body.length) {
        body.set(original.body.subarray(0, body.length - offset), offset);
      }
      const operation = (old: boolean): Blob => new Blob([old ? body.slice() : body], { type: "image/png" });
      const parsed = parse(encodeShrf(original.header, body));
      expect(new Uint8Array(await parsed.png.arrayBuffer())).toEqual(body);
      for (const old of [true, false]) {
        expect(new Uint8Array(await operation(old).arrayBuffer())).toEqual(body);
        for (let warmup = 0; warmup < 3; warmup++) expect(operation(old).size).toBe(body.length);
      }
      const before: number[] = [], after: number[] = [];
      for (let i = 0; i < iterations; i++) {
        for (const old of i % 2 === 0 ? [true, false] : [false, true]) {
          const start = performance.now();
          const blob = operation(old);
          const elapsed = performance.now() - start;
          if (blob.size !== body.length) throw new Error("PNG byte count changed");
          (old ? before : after).push(elapsed);
        }
      }
      results.push({ route, dataset: synthetic ? "synthetic-transport" : "committed-fixture",
        body_bytes: body.length, body_equal: true, before_ms: before, after_ms: after,
        source_extra_copy_before_bytes: body.length, source_extra_copy_after_bytes: 0 });
    }
  }
  console.log("SHRF_PNG_BENCHMARK " + JSON.stringify({
    runtime: versions, iterations, scope: "Node Blob construction only, not WebView PNG decoding; copy byte counts are source arithmetic, not heap/peak measurements",
    synthetic_scope: "repeated fixture bytes for transport only, not valid PNG codec timing", results,
  }));
}, 60_000);
