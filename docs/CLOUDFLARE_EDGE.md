# Cloudflare edge for the hosted web app

SHARPpy's Rust Worker handles a small set of HTTP routes. The OVH gateway still
owns scientific computation, provider selection, shared and disk caches,
browser verification, private profiles, request admission, jobs and storage.
The native application keeps its local backend and signed updater. Nothing
depends on Cloudflare in a default desktop or local browser build.

## HTTP routes

| Path | Purpose | Behavior |
| --- | --- | --- |
| `GET /__edge/status` | Independent snapshots | Returns the latest OVH, HRRR, MRMS and stable-release observations |
| `GET /__edge/health` | External uptime check | Returns 200 for a recent successful OVH liveness sample and 503 for a failed, missing or stale sample |
| `GET /__edge/config` | Public runtime configuration | Enables eligible browser reads only when KV key `config-v1` contains `{"schemaVersion":1,"routePublicReads":true}` |
| `POST /__edge/backend` | Optional read-only proxy | Forwards the existing request envelope and streams the gateway reply. Clients continue queued jobs on `/__jobs/{id}` |
| `GET /__edge/public/{name}` | Public image and icon cache | Mirrors the gateway's five public social-card and icon files |

The same five files can also be served on their original paths if those exact
paths are attached to the Worker. Leave all other paths with OVH. If the site
uses Turnstile or a preview key, configure the zone CDN to bypass protected
paths, including gated JavaScript assets, independently of the Worker cache.

Only credential-free 200 responses with explicit public freshness, an allowed
content type and a known size of at most 8 MiB can enter the shared edge cache.
The key includes the complete origin URI and typed read envelope. The Worker
caps freshness at the gateway's existing per-method lifetime. Private,
cookie-bearing, ranged, conditional, queued, and failed responses pass through.
Current hosted RPC responses are `no-store`, so they pass through without a
shared cache hit. The cache can serve the already-public assets today. It can
serve RPC results only if a future gateway explicitly exposes a credential-free
public read contract. The production Turnstile and session gates remain in
force.

API requests use an approximate, per-location limit of 180 requests per
minute per client IP. Status, config, and public assets share a separate
240-per-minute limit. The gateway's application-level limits continue to
apply. The proxy keeps 403 and 429 responses authoritative. A browser retries
directly only when a 404 or 503 carries
`X-Sharppy-Edge-Route: not-forwarded`. An ambiguous upstream or connection
failure can happen after the gateway accepted a job, so the transport returns
the error without automatic resubmission.

## Monitoring and request budget

One Cron Trigger runs every five minutes. It fetches the OVH
`/__health` JSON, checks one HRRR CONUS F00 object two UTC cycles back using a
one-entry S3 listing, and checks the NOAA MRMS lowest-altitude reflectivity
object with HEAD. MRMS is fresh for up to 15 minutes. These samples indicate
availability for their named feeds. Forecast inventories, all other models,
individual radar sites, sounding workers and numerical correctness have
their own existing application checks.

The Worker checks GitHub's latest stable release redirect once per hour and
retains the previous validated notice if GitHub cannot respond. The browser
links to the official release page only when its version is newer. The
Worker neither stores installers nor participates in signature verification.
The status snapshot lives in one KV key and becomes unknown after 15 minutes.
Browser refreshes stop in hidden tabs. Each visible opted-in browser refreshes
status and configuration no more often than every five minutes.

The schedule makes about 288 KV writes per day and 288 lightweight OVH
liveness requests per day. On the Workers Free plan the entire account
shares 100,000 requests/day, 10 ms CPU per invocation, 50 subrequests per
invocation, and five Cron Triggers. KV Free includes 100,000 reads and
1,000 writes per day. A continuously visible browser makes up to 576
status/config requests per day before any optional proxy traffic. Start with
monitoring and public files, and check actual volume before enabling proxy
routing. No paid database, queue, object storage or additional VPS is needed.

[Workers limits](https://developers.cloudflare.com/workers/platform/limits/),
[KV limits](https://developers.cloudflare.com/kv/platform/limits/),
[Rate Limiting binding](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/),
[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/).

## Local validation

Use the `SHARPpyBuilder` WSL Docker preview with separate data, plus Wrangler
in local mode. The checked-in `wrangler.toml` contains a local origin and a
dummy KV namespace ID. Use Rust 1.91 or newer, the
`wasm32-unknown-unknown` target, `worker-build` 0.9.0 and Wrangler 4.158.0.
For example, with the repository mounted in a Linux validation container:

```sh
cd rust/sharppy-edge
cargo test --locked
npx --yes wrangler@4.158.0 dev --local --ip 127.0.0.1 --port 8787 --test-scheduled
```

The preview's `/__backend` can be routed in pass-through mode for a
before/after measurement. Its local Vite process does not serve the hosted
`/__health` DTO. For a health and cache test, start the benchmark's origin
fixture on port 8788 using real responses from a cached REFS run in the
development preview:

```sh
node benchmarks/cloudflare-edge.mjs origin http://127.0.0.1:4198 2026-11-25T00:00:00Z 8788
```

Run Wrangler with the additional argument
`--var OVH_ORIGIN:http://127.0.0.1:8788`, then compare the two paths from the
repository root:

```sh
node benchmarks/cloudflare-edge.mjs compare http://127.0.0.1:8788 http://127.0.0.1:8787 \
  2026-11-25T00:00:00Z .test-results/cloudflare-edge/fixture.json --fixture
```

This compares response digests and p50/p95 latency and counts requests that
reach the origin fixture. Drop `--fixture` and point the Worker and direct
baseline at the Rust-backed preview to measure pass-through separately.
The direct catalog's generation timestamp varies, so only that test compares
the catalog content after excluding `generatedAt`. The frame digest covers
every byte.
Use a populated forecast run if the example run has aged out. Local workerd
results do not measure an Internet round trip or cache sharing across
Cloudflare locations.

### Local measurements on November 25, 2026

The direct and Worker requests were alternated in the same WSL Docker
environment. Each path used 30 catalog samples and 12 frame samples after a
warm-up. The pass-through run used the Rust-backed v3.10.0 preview.

| Preview payload | Direct p50 / p95 | Worker pass-through p50 / p95 |
| --- | ---: | ---: |
| 99 kB model catalog | 19.7 / 28.0 ms | 43.1 / 53.1 ms |
| 4.2 MB REFS Mean F001 field | 89.2 / 150.6 ms | 125.5 / 231.4 ms |

The REFS response matched byte for byte. The catalog matched after omitting
its generation timestamp. No pass-through response was cached. A fixture
served the same payloads as explicitly public responses. For that fixture,
the Worker served all 42 warmed requests from cache. Origin requests fell
from 31 to 1 for the catalog and from 13 to 1 for the frame, including the
initial warm-up request. Local cache hits took longer than the fixture's
direct loopback responses. Production latency and cache savings require
measurements on the deployed route.

## Prepare a hosted rollout

1. Provision a dedicated Workers KV namespace on the intended Free account,
   and copy the local Wrangler manifest to a deployment configuration.
   Replace the dummy namespace ID and set `OVH_ORIGIN` to the existing
   same-origin public app HTTPS origin. Use `OVH_HEALTH_ORIGIN` for a direct
   OVH health-only hostname with a matching TLS virtual host if the app
   hostname is proxied. Keep the only Cron Trigger at five minutes and leave
   routing permission false initially.
2. Put status on a Worker hostname that remains reachable when the VPS is
   offline. A dedicated Worker custom domain or a `workers.dev` hostname can
   serve monitoring independently. The Worker proxies authenticated RPC only
   on the configured app origin. Confirm its Cron reaches `/__health` and
   verify the freshness and error states before adding a browser route.
3. The hosted build overlay owns the real HTTP transport, including
   `202` job polling and Turnstile handling. It should import
   `fetchHostedBackendWithEdge` from the shared frontend module and replace
   only its initial `fetch("/__backend", init)` with
   `fetchHostedBackendWithEdge(command, init)`. Keep the same request
   options, job loop and response decoding. Inject
   `VITE_SHARPPY_EDGE_PATH=/__edge` into the hosted Vite build to opt in.
   Rebase the v3.9.0 hosted overlay and its RPC allowlist onto the v3.10.0
   candidate before deployment. Confirm the new candidate's method and DTO
   contracts in that overlay.
4. Attach only `app.sharppyre.online/__edge/*` and any explicitly approved
   public image paths. Cloudflare Routes require a proxied DNS record.
   Use an explicit origin fallback for the edge RPC path. The example below
   keeps the existing Caddy gateway target.

   ```caddyfile
   app.sharppyre.online {
     handle /__edge/backend {
       header X-Sharppy-Edge-Route not-forwarded
       header Cache-Control no-store
       respond 503
     }
     handle {
       reverse_proxy 127.0.0.1:18080
     }
   }
   ```

   This lets a client with a recently cached opt-in return to the original
   API when a request bypasses the Worker. Preserve the gateway's existing
   default route and strict TLS.
5. Benchmark the direct and candidate paths before setting the KV opt-in.
   Confirm frame bytes and the catalog contents apart from its timestamp. Verify a
   private session, a queued job, a Turnstile rejection and a 429, and compare
   origin requests, application and edge latency, and the account's Worker
   usage. To revert routing, set the KV flag to false. Remove the route or
   rebuild without `VITE_SHARPPY_EDGE_PATH` if the edge feature must be disabled
   immediately.

[Cloudflare routes](https://developers.cloudflare.com/workers/configuration/routing/routes/),
[Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/).
