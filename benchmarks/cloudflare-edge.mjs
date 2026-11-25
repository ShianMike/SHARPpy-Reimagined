import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname } from "node:path";

const args = process.argv.slice(2);
const command = args.shift();
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+h42YAAAAASUVORK5CYII=", "base64");

function loopback(value) {
  const url = new URL(value);
  assert.equal(url.protocol, "http:");
  assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(url.hostname));
  return url.origin;
}

function requests(runTime) {
  return [
    { name: "model-catalog", method: "get_model_catalog", params: {}, count: 30 },
    {
      name: "refs-frame", method: "get_weather_field", count: 12,
      params: { model: "refs", product: "qpf-1h-ptype", runTime, forecastHour: 1 },
    },
  ];
}

async function post(base, path, item, headers = {}) {
  const started = performance.now();
  const response = await fetch(base + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify({ id: 1, method: item.method, params: item.params }),
    signal: AbortSignal.timeout(20_000),
  });
  const bytes = Buffer.from(await response.arrayBuffer());
  return {
    status: response.status,
    headers: response.headers,
    bytes,
    milliseconds: performance.now() - started,
  };
}

function hash(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function responseDigest(bytes, catalogHasTimestamp) {
  if (!catalogHasTimestamp) return hash(bytes);
  const catalog = JSON.parse(bytes.toString("utf8"));
  assert.equal(typeof catalog.generatedAt, "string");
  assert.ok(Number.isFinite(Date.parse(catalog.generatedAt)));
  delete catalog.generatedAt;
  return hash(Buffer.from(JSON.stringify(catalog)));
}

function latency(samples) {
  const sorted = [...samples].sort((a, b) => a - b);
  const percentile = (fraction) => Number(sorted[Math.ceil(fraction * sorted.length) - 1].toFixed(2));
  return { p50ms: percentile(0.5), p95ms: percentile(0.95) };
}

async function fixture(preview, runTime, port) {
  const bodies = new Map();
  for (const item of requests(runTime)) {
    const result = await post(preview, "/__backend", item);
    assert.equal(result.status, 200);
    bodies.set(item.method, { bytes: result.bytes, type: result.headers.get("Content-Type") });
  }
  const counts = { rpc: 0, publicFiles: 0 };
  const serve = async (req, res) => {
    const path = new URL(req.url, "http://localhost").pathname;
    const reply = (status, type, bytes, headers) => {
      res.writeHead(status, { "Content-Type": type, "Content-Length": bytes.length, ...headers });
      res.end(bytes);
    };
    const json = (status, data, headers = {}) =>
      reply(status, "application/json", Buffer.from(JSON.stringify(data)), headers);
    if (path === "/__fixture/counts") {
      json(200, counts);
      return;
    }
    if (path === "/__health") {
      json(200, { ok: true, version: "3.10.0" }, { "Cache-Control": "no-store" });
      return;
    }
    if (path === "/share-card-v3.png") {
      counts.publicFiles++;
      reply(200, "image/png", png, { "Cache-Control": "public, max-age=86400" });
      return;
    }
    if (path !== "/__backend" || req.method !== "POST") {
      json(404, { error: "Unknown fixture route." });
      return;
    }
    counts.rpc++;
    const chunks = [];
    let length = 0;
    for await (const chunk of req) {
      length += chunk.length;
      if (length > 64 * 1024) {
        json(413, { error: "Fixture request is too large." });
        return;
      }
      chunks.push(chunk);
    }
    const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const headers = { "Cache-Control": "no-store" };
    if (request.params.mode === "job") {
      assert.equal(req.headers.cookie, "sharppy_session=fixture");
      json(202, { jobId: "3".repeat(32) }, {
        ...headers,
        "Set-Cookie": ["sharppy_session=fixture; Path=/; HttpOnly", "second=fixture; Path=/"],
      });
    } else if (request.params.mode === "captcha") {
      json(403, { code: "CAPTCHA_REQUIRED", message: "Verify your browser." }, headers);
    } else if (request.params.mode === "set-cookie") {
      json(200, { scoped: true }, {
        "Cache-Control": "public, max-age=120",
        "Set-Cookie": "scoped=fixture; Path=/; HttpOnly",
      });
    } else {
      const result = bodies.get(request.method);
      if (result) {
        reply(200, result.type, result.bytes, {
          "Cache-Control": request.params.mode === "private" ? "no-store" : "public, max-age=120",
        });
      } else {
        json(404, { error: "Unknown fixture method." });
      }
    }
  };
  createServer((req, res) => {
    void serve(req, res).catch((error) => {
      console.error(error.message);
      res.writeHead(500).end();
    });
  }).listen(port, "127.0.0.1", () => console.log(`Origin fixture listening on 127.0.0.1:${port}`));
}

async function comparison(direct, edge, runTime, output, isFixture) {
  const rows = [];
  for (const item of requests(runTime)) {
    if (isFixture) item.params.benchRun = Date.now();
    const counts = async () => (await fetch(direct + "/__fixture/counts")).json();
    const beforeCount = isFixture ? (await counts()).rpc : null;
    const firstDirect = await post(direct, "/__backend", item);
    assert.equal(firstDirect.status, 200);
    const catalogHasTimestamp = !isFixture && item.name === "model-catalog";
    const digest = responseDigest(firstDirect.bytes, catalogHasTimestamp);
    const firstEdge = await post(edge, "/__edge/backend", item);
    assert.equal(firstEdge.status, 200);
    assert.equal(responseDigest(firstEdge.bytes, catalogHasTimestamp), digest);

    const directSamples = [];
    const measureDirect = async () => {
      const next = await post(direct, "/__backend", item);
      assert.equal(next.status, 200);
      directSamples.push(next.milliseconds);
    };
    const edgeSamples = [];
    let hits = 0;
    const measureEdge = async () => {
      const next = await post(edge, "/__edge/backend", item);
      assert.equal(next.status, 200);
      assert.equal(responseDigest(next.bytes, catalogHasTimestamp), digest);
      if (next.headers.get("X-Sharppy-Edge-Cache") === "HIT") hits++;
      edgeSamples.push(next.milliseconds);
    };
    for (let i = 0; i < item.count; i++) {
      // Alternate the order so the second path does not always get the warmer origin.
      if (i % 2 === 0) {
        await measureDirect();
        await measureEdge();
      } else {
        await measureEdge();
        await measureDirect();
      }
    }
    const after = isFixture ? (await counts()).rpc : null;
    if (isFixture) {
      assert.equal(firstEdge.headers.get("X-Sharppy-Edge-Cache"), "MISS");
      assert.ok(hits >= item.count - 1, `Only ${hits} edge hits for ${item.name}`);
    } else {
      assert.equal(hits, 0);
    }
    rows.push({
      name: item.name, bytes: firstDirect.bytes.length, sha256: digest,
      digestScope: catalogHasTimestamp ? "JSON excluding its generatedAt timestamp" : "entire response body",
      samplesPerPath: item.count, direct: latency(directSamples),
      edge: { ...latency(edgeSamples), hits, firstMs: Number(firstEdge.milliseconds.toFixed(2)) },
      upstreamRequests: isFixture
        ? { direct: item.count + 1, edge: after - beforeCount - item.count - 1 }
        : null,
    });
  }
  if (isFixture) {
    const item = requests(runTime)[0];
    const request = (mode, headers) => post(edge, "/__edge/backend", { ...item, params: { mode } }, headers);
    for (const mode of ["private", "set-cookie"]) {
      for (let i = 0; i < 2; i++) {
        assert.equal((await request(mode)).headers.get("X-Sharppy-Edge-Cache"), "BYPASS");
      }
    }
    const job = await request("job", { Cookie: "sharppy_session=fixture" });
    assert.equal(job.status, 202);
    assert.deepEqual(job.headers.getSetCookie(), [
      "sharppy_session=fixture; Path=/; HttpOnly", "second=fixture; Path=/",
    ]);
    assert.equal((await request("captcha")).status, 403);
    const mutation = await post(edge, "/__edge/backend", { method: "open_sounding", params: {} });
    assert.equal(mutation.status, 404);
    assert.equal(mutation.headers.get("X-Sharppy-Edge-Route"), "not-forwarded");
    const publicImage = edge + "/__edge/public/share-card-v3.png?v=" + Math.floor(Date.now() / 100);
    for (const expected of ["MISS", "HIT"]) {
      const response = await fetch(publicImage);
      assert.equal(response.headers.get("X-Sharppy-Edge-Cache"), expected);
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), png);
    }
  }
  const report = { measuredAt: new Date().toISOString(), fixture: isFixture, runTime, rows };
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report, null, 2));
}

if (command === "origin" && args.length === 3) {
  await fixture(loopback(args[0]), args[1], Number(args[2]));
} else if (command === "compare" && (args.length === 4 || (args.length === 5 && args[4] === "--fixture"))) {
  await comparison(loopback(args[0]), loopback(args[1]), args[2], args[3], args[4] === "--fixture");
} else {
  throw new Error("Use origin <preview-url> <run-utc> <port> or compare <origin-url> <edge-url> <run-utc> <output-json> [--fixture].");
}
