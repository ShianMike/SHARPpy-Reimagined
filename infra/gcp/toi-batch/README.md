# TOI archive on Google Cloud Batch

Cost-bounded packaging for the one-time 2015-2025 HRRR archive collection that
feeds the experimental Tornado Outbreak Indicator calibration.

**Status: planned and rendered, not deployed.** Nothing in this directory has
been executed against Google Cloud. No API was enabled, no bucket created, no VM
started, no Batch job submitted, no object uploaded, and no cloud cost incurred.
The live project has Storage and Artifact Registry enabled; **Batch and Compute
Engine are not**, and this tool does not enable them.

## Design constraints

| Constraint | How it is enforced |
|---|---|
| No container registry | Batch **script** runnable; no Artifact Registry, Cloud Build, or Cloud Run |
| Project never hardcoded | Resolved only from `--project` or `GOOGLE_CLOUD_PROJECT` / `CLOUDSDK_CORE_PROJECT` / `GCP_PROJECT`, else `gcloud config get-value project` |
| Billing never hardcoded | Never read or recorded; billing is a project property |
| Existing buckets untouched | Names ending `-modelforecastpy-cache` or `_cloudbuild` are rejected at config construction and in `cleanup` |
| Inert by default | Every mutating subcommand dry-runs; each real mutation needs its own flag (`--confirm-enable-apis`, `--confirm-create-bucket`, `--confirm-build`, `--confirm-submit`, `--confirm-delete`) |
| Configuration is not permission | `preflight` only *reports* that Batch and Compute are disabled, and an `--offline` preflight can never report `ready_to_submit` because it observed nothing |
| Budget is a cap, not an alert | Job-side maxima on cases, input bytes, wall time, task count, retries, disk, and output; `plan` exits non-zero if a projection breaches one |

## Commands

The tool is a single script, run with the repository's interpreter. There is no
installed console entry point.

```bash
export GOOGLE_CLOUD_PROJECT=<your-project>     # or pass --project
cd infra/gcp/toi-batch

# 1. Read-only audit. Reports API and bucket status; enables nothing.
#    --offline skips all gcloud calls and therefore always reports a blocker.
python toi_batch.py preflight --offline --output out/preflight.json
python toi_batch.py preflight --output out/preflight.json      # live read-only

# 2. Config generator. Every default is overridable.
python toi_batch.py config --output out/config.json

# 3. Source bundle. Dry run by default; --confirm-build writes the tarball.
python toi_batch.py bundle --output out/bundle.tar.gz
python toi_batch.py bundle --output out/bundle.tar.gz --confirm-build

# 4. Deterministic, event-indivisible sharding.
python toi_batch.py shard --catalog ../../../archive/catalog-2015-2025.json \
    --shards 4 --out-dir out/shards

# 5. Fully rendered dry-run plan, job spec, task script, lifecycle, commands.
python toi_batch.py plan --total-cases 600 --mib-per-case 73.4 \
    --seconds-per-case 89.3 --shards 4 --parallelism 1 --out-dir out

# 6. Submission. Prints the exact gcloud command and refuses to act without
#    --confirm-submit. It has never been run.
python toi_batch.py submit

# 7. Job status (read-only; --offline prints what would be queried).
python toi_batch.py status --job-name toi-archive-2015-2025 --offline

# 8. Merge-verify shard manifests: rejects missing, duplicate, overlapping, or
#    split events, inconsistent plans/sources, and hash failures.
python toi_batch.py verify --reports-dir out/reports \
    --catalog ../../../archive/catalog-2015-2025.json

# 9. Cleanup inventory. Deletes nothing without --confirm-delete.
python toi_batch.py cleanup --output out/cleanup.json
```

## Defaults

- Region `us-east1`, `e2-standard-4` (4 vCPU / 16 GiB), **SPOT**, no GPU
- 50 GiB `pd-balanced` boot disk, Cloud Logging
- `maxRunDuration` 24 h, `maxRetryCount` 3, Spot preemption retried
- 4 shards, **parallelism 1**
- Labels include `app=sharppy`, `workload=toi-archive`

Project, region, machine type, disk, retry count, shard count, and every budget
are configurable. **Parallelism above 1 is refused in code** until a bounded
cloud pilot shows concurrent NOAA archive requests are safe.

## Spot resume

Raw GRIB stays VM-local and is never uploaded. After each case the runner writes
atomically to local disk; the task then mirrors the compact case JSON and the
checkpoint to the run's state prefix and verifies the upload by checksum. A
preempted retry restores that state, clears any stale `run.lock`, and the runner
skips already-completed cache keys, so each task is idempotent. Cloud Storage
FUSE rename semantics are not relied upon as an integrity guarantee.

## Projected usage

Rendered by `plan` at the measured pilot rates (73.4 MiB and 89.3 s per case,
600 cases, 4 shards, parallelism 1):

| Quantity | Projected |
|---|---|
| Inbound transfer | 43.01 GiB |
| Single-worker wall time | 14.88 h |
| vCPU-hours | 59.53 |
| Retained output | 2820 KiB |
| Peak raw disk | ~20 MiB (each subset deleted after extraction) |
| Job-side hard caps | satisfied |

## Source bundle

Allow-list based, so a new large or sensitive directory cannot silently start
shipping. Cache and history directories are excluded at **any** depth, not only
at the repository root.

| Quantity | Measured |
|---|---|
| Files | 274 |
| Uncompressed | 14.21 MiB |
| Compressed | 7.89 MiB |
| SHA-256 | `e4231b925927211376bae387cbedeb34249f47c20821d4583d040599e777d0fc` |
| Git HEAD | `9cc7ab7c3e79`, dirty worktree recorded (not uploaded) |

Excluded: `.git`, credentials and `*.env`, virtualenvs, `archive/`, `data/`,
`models/`, `reports/`, build output, GRIB/NetCDF/NPZ artifacts, logs, and every
`__pycache__` / `.hypothesis` / `.pytest_cache` / `.ruff_cache` / `.mypy_cache`
directory at any depth.

## Lifecycle

Temporary prefixes (`runs/`, `tmp/`, `source/`) expire after 14 days. Final
manifests, source records, validation reports, and promoted model artifacts have
no delete rule.

## Generated artifacts

`out/` holds the rendered dry run: `plan.json`, `job.json`, `task-script.sh`,
`lifecycle.json`, `commands.json`, `preflight.json`, `bundle.json`,
`bundle.sha256`, `cleanup.json`, and `shards/`. They are descriptions, not
deployments. `out/bundle.tar.gz` is a build product and is git-ignored.

## Remaining user confirmations before anything runs

1. Enable the Batch and Compute Engine APIs on the project.
2. Create the dedicated bucket (`<project>-toi-archive`); the two existing
   buckets are unrelated and must not be reused.
3. Apply the lifecycle rules.
4. Build and upload the source bundle.
5. Run `preflight` **without** `--offline` and confirm zero blockers.
6. Submit with `--confirm-submit`.

A real catalogue must also exist. The shard artifacts in `out/shards/` were
rendered from the 8-case pilot catalogue, not from a 600-case one.
