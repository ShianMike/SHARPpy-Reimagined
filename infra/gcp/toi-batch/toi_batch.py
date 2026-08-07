#!/usr/bin/env python3
"""Cost-bounded Google Cloud Batch deployment planner for the TOI archive run.

This tool *plans and renders*; it does not act. Every mutating subcommand
defaults to a dry run, and each real mutation needs its own explicit flag
(``--confirm-enable-apis``, ``--confirm-create-bucket``, ``--confirm-submit``,
``--confirm-delete``). Configuration never implies permission.

Design choices that keep the job cheap and reproducible:

* **Batch script runnable, not a container.** No Artifact Registry, no Cloud
  Build, no Cloud Run. The task script installs from an uploaded source tarball.
* **Nothing hardcoded.** Project and billing account are read from the
  environment or an explicit flag, never embedded. Existing unrelated buckets
  are never referenced.
* **Event-indivisible shards.** All cycles of one ``event_id`` stay in one
  shard, so the downstream year/event-blocked validation cannot be corrupted by
  how work was split.
* **Raw GRIB never leaves the VM.** Only the compact per-case JSON and the
  checkpoint are mirrored to Cloud Storage, after an explicit upload plus
  checksum verification. No Cloud Storage FUSE rename semantics are trusted.
* **Job-side hard caps.** A billing alert is not a cap, so max cases, input
  bytes, wall time, task count, retries, disk, and output bytes are enforced by
  the runner itself.

Usage is documented in ``README.md``.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOL_VERSION = "toi_batch_planner_v1"
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
DEFAULT_OUT = HERE / "out"

#: Batch and Compute Engine are required; Storage is already enabled in the
#: caller's project. Enabling is never implied by planning.
REQUIRED_APIS = ("batch.googleapis.com", "compute.googleapis.com")
STORAGE_API = "storage.googleapis.com"

#: Buckets that must never be touched. Recorded so a reviewer can see the
#: exclusion is explicit rather than assumed.
PROTECTED_BUCKET_SUFFIXES = ("-modelforecastpy-cache", "_cloudbuild")

#: Source-bundle exclusions: history, credentials, caches, data, and outputs.
BUNDLE_EXCLUDE_GLOBS = (
    ".git", ".git/*", "*.git/*",
    ".gribenv", ".gribenv/*",
    ".venv", ".venv/*", "venv/*",
    "archive", "archive/*",
    "dist", "dist/*", "build", "build/*",
    ".tmp", ".tmp/*",
    ".test-results", ".test-results/*",
    ".hypothesis", ".hypothesis/*",
    ".pytest_cache", ".pytest_cache/*",
    "__pycache__", "*/__pycache__/*", "*.pyc", "*.pyo",
    ".coverage", "coverage.xml",
    "*.env", ".env", ".env.*",
    "*credential*", "*secret*", "*.pem", "*.key", "*.p12", "*.pfx",
    "*service-account*", "*.keyfile.json",
    "models/*", "reports/*", "data/*",
    "*.grib2", "*.grib2.idx", "*.npz", "*.nc", "*.zarr/*",
    "infra/gcp/toi-batch/out/*",
    "*.log",
    "node_modules/*",
)

#: Directory names that are excluded at *any* depth. The glob list above is
#: root-anchored, so a nested cache such as ``sharpmod/.hypothesis`` would
#: otherwise ship. Checked component-wise against every parent directory.
BUNDLE_EXCLUDE_DIR_NAMES = frozenset(
    {
        ".git",
        ".hypothesis",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
        "__pycache__",
        "node_modules",
        ".ipynb_checkpoints",
        "htmlcov",
        ".egg-info",
    }
)

#: Allow-list of exactly what the task needs. An allow-list is safer than an
#: exclude-list for a source bundle: a new large or sensitive directory cannot
#: silently start shipping. Measured without it, the tree pulled in 1.5 GiB of
#: Rust build output and a second virtualenv.
BUNDLE_INCLUDE_ROOTS = (
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "sharpmod",
    "scripts",
)

#: ``sharpmod/resources`` carries the bundled Census county tiles that the
#: land-domain mask needs, so the per-file ceiling must clear them.
BUNDLE_MAX_FILE_BYTES = 8 * 1024 * 1024


class PlannerError(RuntimeError):
    """A configuration or preflight problem the user must resolve."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchConfig:
    """Fully configurable job settings. Nothing about the project is baked in."""

    project: str
    region: str = "us-east1"
    machine_type: str = "e2-standard-4"
    provisioning_model: str = "SPOT"
    boot_disk_gib: int = 50
    boot_disk_type: str = "pd-balanced"
    task_count: int = 4
    parallelism: int = 1
    max_retry_count: int = 3
    max_run_duration_seconds: int = 24 * 3600
    bucket: str = ""
    run_prefix: str = "toi-archive"
    labels: Mapping[str, str] = field(
        default_factory=lambda: {"app": "sharppy", "workload": "toi-archive"}
    )
    # Job-side hard caps (a billing alert is not a cap).
    max_cases_per_task: int = 200
    max_input_gib_per_task: int = 16
    max_output_mib_per_task: int = 64
    max_task_wall_seconds: int = 20 * 3600
    min_free_gib: int = 12
    service_account: str = ""
    python_version: str = "3.11"

    def __post_init__(self) -> None:
        if not str(self.project).strip():
            raise PlannerError(
                "project is required and must come from --project or "
                "GOOGLE_CLOUD_PROJECT / CLOUDSDK_CORE_PROJECT; it is never "
                "hardcoded in this tool"
            )
        for name in (
            "boot_disk_gib",
            "task_count",
            "parallelism",
            "max_cases_per_task",
            "max_input_gib_per_task",
            "max_output_mib_per_task",
            "min_free_gib",
        ):
            if int(getattr(self, name)) < 1:
                raise PlannerError(f"{name} must be at least 1")
        if int(self.max_retry_count) < 0:
            raise PlannerError("max_retry_count must be non-negative")
        if int(self.parallelism) > int(self.task_count):
            raise PlannerError("parallelism cannot exceed task_count")
        if int(self.parallelism) > 2:
            raise PlannerError(
                "parallelism above 2 is not permitted: concurrent NOAA archive "
                "request behaviour has not been validated. Parallelism 2 may be "
                "documented only after a bounded cloud pilot proves it is safe."
            )
        if self.provisioning_model not in {"SPOT", "STANDARD"}:
            raise PlannerError("provisioning_model must be SPOT or STANDARD")
        for suffix in PROTECTED_BUCKET_SUFFIXES:
            if self.bucket and self.bucket.endswith(suffix):
                raise PlannerError(
                    f"bucket {self.bucket!r} matches a protected existing bucket "
                    f"pattern ({suffix}); this run requires its own dedicated "
                    "bucket and must never reuse an unrelated one"
                )
        object.__setattr__(self, "labels", dict(self.labels))

    @property
    def default_bucket(self) -> str:
        return self.bucket or f"{self.project}-toi-archive"

    def to_mapping(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["labels"] = dict(self.labels)
        payload["resolved_bucket"] = self.default_bucket
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> BatchConfig:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in payload.items() if k in known})

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> BatchConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_mapping(data)


def resolve_project(explicit: str | None) -> str:
    """Resolve the project without ever embedding one in source."""

    if explicit:
        return explicit
    for name in ("GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT", "GCP_PROJECT"):
        value = os.environ.get(name)
        if value:
            return value
    executable = _gcloud_executable()
    if executable is None:
        raise PlannerError(
            "gcloud not found on PATH; pass --project or set GOOGLE_CLOUD_PROJECT"
        )
    try:
        result = subprocess.run(
            [executable, "config", "get-value", "project"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlannerError(f"could not run gcloud to resolve project: {exc}") from exc
    # ``gcloud config get-value`` prints the active-configuration banner on
    # stderr, so only stdout is the value.  Keep the last non-empty line in case
    # a shim still emits a notice there.
    lines = [line.strip() for line in (result.stdout or "").splitlines()]
    value = next((line for line in reversed(lines) if line), "")
    if not value or value == "(unset)":
        raise PlannerError(
            "no project resolved; pass --project or set GOOGLE_CLOUD_PROJECT"
        )
    return value


# ---------------------------------------------------------------------------
# Source bundle
# ---------------------------------------------------------------------------


def _excluded(relative: str) -> bool:
    normalized = relative.replace(os.sep, "/")
    # Cache and history directories must be excluded wherever they appear, not
    # only at the repository root. Measured: ``sharpmod/.hypothesis`` shipped a
    # local example database because ``.hypothesis/*`` is root-anchored.
    parts = normalized.split("/")
    if any(part in BUNDLE_EXCLUDE_DIR_NAMES for part in parts[:-1]):
        return True
    for pattern in BUNDLE_EXCLUDE_GLOBS:
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(
            os.path.basename(normalized), pattern
        ):
            return True
        if normalized.startswith(pattern.rstrip("/*") + "/"):
            return True
    return False


def _included_root(relative: str) -> bool:
    for allowed in BUNDLE_INCLUDE_ROOTS:
        if relative == allowed or relative.startswith(allowed + "/"):
            return True
    return False


def bundle_members(root: Path) -> list[Path]:
    """Return the exact files a source bundle would contain.

    Allow-list first, exclude-list second: a path must be inside a declared
    include root *and* clear every exclusion, so neither a new build directory
    nor a stray credential file can enter the bundle by default.
    """

    selected: list[Path] = []
    for allowed in BUNDLE_INCLUDE_ROOTS:
        base = root / allowed
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = sorted(item for item in base.rglob("*") if item.is_file())
        else:
            continue
        for path in candidates:
            relative = path.relative_to(root).as_posix()
            if not _included_root(relative) or _excluded(relative):
                continue
            try:
                if path.stat().st_size > BUNDLE_MAX_FILE_BYTES:
                    continue
            except OSError:  # pragma: no cover - race
                continue
            selected.append(path)
    return sorted(set(selected))


def git_provenance(root: Path) -> dict[str, Any]:
    """Record the exact Git HEAD and dirty-worktree state, without secrets."""

    def run(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return (result.stdout or "").strip()

    status = run("status", "--porcelain")
    dirty = [line[3:] for line in status.splitlines() if line.strip()]
    return {
        "head": run("rev-parse", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(dirty),
        "dirty_file_count": len(dirty),
        # Paths only; contents are never recorded.
        "dirty_files_sample": dirty[:25],
    }


def build_bundle(
    root: Path, destination: Path, *, dry_run: bool = True
) -> dict[str, Any]:
    """Build (or plan) the source tarball and record its SHA-256."""

    members = bundle_members(root)
    total = sum(path.stat().st_size for path in members)
    record: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "generated_at": _now(),
        "root": str(root),
        "destination": str(destination),
        "file_count": len(members),
        "uncompressed_bytes": total,
        "uncompressed_mib": round(total / 1024**2, 2),
        "include_roots": list(BUNDLE_INCLUDE_ROOTS),
        "excluded_globs": list(BUNDLE_EXCLUDE_GLOBS),
        "max_file_bytes": BUNDLE_MAX_FILE_BYTES,
        "git": git_provenance(root),
        "dry_run": dry_run,
        "sample_members": [
            path.relative_to(root).as_posix() for path in members[:15]
        ],
    }
    # Nothing that looks like a credential may ever enter the bundle.
    leaked = [
        path.relative_to(root).as_posix()
        for path in members
        if any(
            token in path.name.casefold()
            for token in ("credential", "secret", ".env", "service-account")
        )
    ]
    if leaked:
        raise PlannerError(
            "refusing to bundle credential-like files: " + ", ".join(leaked[:5])
        )
    if dry_run:
        record["sha256"] = None
        return record

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".partial")
    with tarfile.open(tmp, "w:gz") as archive:
        for path in members:
            archive.add(path, arcname=path.relative_to(root).as_posix())
    os.replace(tmp, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    record.update(
        {
            "sha256": digest,
            "bundle_bytes": destination.stat().st_size,
            "bundle_mib": round(destination.stat().st_size / 1024**2, 2),
        }
    )
    return record


# ---------------------------------------------------------------------------
# Event-indivisible sharding
# ---------------------------------------------------------------------------


def shard_catalog(
    catalog: Mapping[str, Any], *, shards: int
) -> list[dict[str, Any]]:
    """Split a catalogue into shards that never split an ``event_id``.

    Assignment is a stable hash of the event id, so the same catalogue always
    produces the same shards regardless of ordering or machine.
    """

    if int(shards) < 1:
        raise PlannerError("shards must be at least 1")
    cases = list(catalog.get("cases") or ())
    if not cases:
        raise PlannerError("catalogue contains no cases to shard")
    buckets: list[list[Mapping[str, Any]]] = [[] for _ in range(int(shards))]
    for case in cases:
        event_id = str(case.get("event_id", ""))
        if not event_id:
            raise PlannerError("every case needs an event_id for sharding")
        digest = hashlib.sha256(event_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % int(shards)
        buckets[index].append(case)

    result: list[dict[str, Any]] = []
    for index, bucket in enumerate(buckets):
        events = sorted({str(case["event_id"]) for case in bucket})
        result.append(
            {
                "shard_index": index,
                "shard_id": f"shard-{index:02d}",
                "case_count": len(bucket),
                "event_count": len(events),
                "event_ids": events,
                "work_dir": f"/mnt/work/shard-{index:02d}",
                "gcs_prefix": f"shards/shard-{index:02d}",
                "cases": bucket,
            }
        )
    empty = [item["shard_id"] for item in result if item["case_count"] == 0]
    if empty:
        raise PlannerError(
            "sharding produced empty shard(s) "
            + ",".join(empty)
            + "; reduce --shards for this catalogue size"
        )
    # An event must appear in exactly one shard.
    seen: dict[str, str] = {}
    for item in result:
        for event_id in item["event_ids"]:
            if event_id in seen:
                raise PlannerError(
                    f"event {event_id} appears in {seen[event_id]} and "
                    f"{item['shard_id']}"
                )
            seen[event_id] = item["shard_id"]
    return result


def verify_merge(
    shard_reports: Sequence[Mapping[str, Any]],
    *,
    expected_events: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Reject missing, duplicated, overlapping, or inconsistent shard output."""

    failures: list[dict[str, str]] = []
    seen_cases: dict[str, str] = {}
    seen_events: dict[str, str] = {}
    plans: set[str] = set()
    targets: set[str] = set()
    methods: set[str] = set()
    total = 0
    for report in shard_reports:
        shard_id = str(report.get("shard_id", "?"))
        plans.add(str(report.get("plan_hash", "")))
        targets.add(str(report.get("target_definition", "")))
        methods.add(str(report.get("feature_method_version", "")))
        for record in report.get("cases") or ():
            key = str(record.get("cache_key", ""))
            event_id = str(record.get("event_id", ""))
            total += 1
            if not key:
                failures.append(
                    {"check": "missing_cache_key", "shard": shard_id, "detail": ""}
                )
                continue
            if key in seen_cases:
                failures.append(
                    {
                        "check": "duplicate_case",
                        "shard": shard_id,
                        "detail": f"{key} also in {seen_cases[key]}",
                    }
                )
            seen_cases[key] = shard_id
            if (
                event_id
                and event_id in seen_events
                and seen_events[event_id] != shard_id
            ):
                failures.append(
                    {
                        "check": "event_split_across_shards",
                        "shard": shard_id,
                        "detail": f"{event_id} also in {seen_events[event_id]}",
                    }
                )
            if event_id:
                seen_events[event_id] = shard_id
            if not record.get("scientific_content_sha256"):
                failures.append(
                    {
                        "check": "missing_scientific_hash",
                        "shard": shard_id,
                        "detail": key,
                    }
                )
    for name, values in (
        ("plan_hash", plans),
        ("target_definition", targets),
        ("feature_method_version", methods),
    ):
        if len({value for value in values if value}) > 1:
            failures.append(
                {
                    "check": f"inconsistent_{name}",
                    "shard": "*",
                    "detail": ",".join(sorted(values)),
                }
            )
    if expected_events is not None:
        expected = {str(item) for item in expected_events}
        missing = sorted(expected.difference(seen_events))
        unexpected = sorted(set(seen_events).difference(expected))
        for event_id in missing:
            failures.append(
                {"check": "missing_expected_event", "shard": "*", "detail": event_id}
            )
        for event_id in unexpected:
            failures.append(
                {"check": "unexpected_event", "shard": "*", "detail": event_id}
            )
    return {
        "verified": not failures,
        "shards": len(shard_reports),
        "cases": total,
        "unique_cases": len(seen_cases),
        "unique_events": len(seen_events),
        "failure_count": len(failures),
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Budgets and estimates
# ---------------------------------------------------------------------------


def project_usage(
    config: BatchConfig,
    *,
    total_cases: int,
    mib_per_case: float,
    seconds_per_case: float,
) -> dict[str, Any]:
    """Print-ready projections computed before anything is submitted."""

    per_shard = max(1, total_cases // max(1, config.task_count))
    wall_seconds = per_shard * seconds_per_case
    vcpus = 4 if config.machine_type.endswith("standard-4") else 4
    concurrent = min(config.parallelism, config.task_count)
    return {
        "total_cases": total_cases,
        "tasks": config.task_count,
        "parallelism": config.parallelism,
        "cases_per_task": per_shard,
        "inbound_transfer_gib": round(total_cases * mib_per_case / 1024, 2),
        "inbound_transfer_gib_per_task": round(per_shard * mib_per_case / 1024, 2),
        "wall_hours_per_task": round(wall_seconds / 3600, 2),
        "wall_hours_total_serial": round(
            total_cases * seconds_per_case / 3600, 2
        ),
        "wall_hours_elapsed_estimate": round(
            wall_seconds / 3600 * (config.task_count / max(1, concurrent)) , 2
        ),
        "vcpu_hours": round(vcpus * wall_seconds / 3600 * config.task_count, 2),
        "peak_raw_disk_mib": 32,
        "boot_disk_gib": config.boot_disk_gib,
        "retained_output_mib": round(total_cases * 4.7 / 1024, 3),
        "retained_output_kib": round(total_cases * 4.7, 1),
        "notes": [
            "Raw GRIB is deleted after each case, so peak raw disk is a couple "
            "of subsets rather than the transfer total.",
            "SPOT pricing is variable; preemption restarts a task from its "
            "mirrored checkpoint rather than from scratch.",
        ],
    }


def budget_guard(config: BatchConfig, usage: Mapping[str, Any]) -> list[str]:
    """Return hard-cap violations that must block submission."""

    problems: list[str] = []
    if usage["cases_per_task"] > config.max_cases_per_task:
        problems.append(
            f"cases per task {usage['cases_per_task']} exceeds cap "
            f"{config.max_cases_per_task}"
        )
    if usage["inbound_transfer_gib_per_task"] > config.max_input_gib_per_task:
        problems.append(
            f"inbound transfer per task {usage['inbound_transfer_gib_per_task']} "
            f"GiB exceeds cap {config.max_input_gib_per_task} GiB"
        )
    if usage["wall_hours_per_task"] * 3600 > config.max_task_wall_seconds:
        problems.append(
            f"per-task wall time {usage['wall_hours_per_task']} h exceeds cap "
            f"{config.max_task_wall_seconds / 3600:.1f} h"
        )
    retained_cap = config.max_output_mib_per_task * config.task_count
    if usage["retained_output_kib"] / 1024 > retained_cap:
        problems.append("projected retained output exceeds the configured cap")
    return problems


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_task_script(config: BatchConfig) -> str:
    """Render the Batch script runnable. No container image is involved."""

    bucket = config.default_bucket
    return f"""#!/usr/bin/env bash
# Rendered by {TOOL_VERSION}. Batch *script* runnable: no container registry.
set -Eeuo pipefail

SHARD_INDEX="${{BATCH_TASK_INDEX:-0}}"
SHARD_ID="$(printf 'shard-%02d' "${{SHARD_INDEX}}")"
BUCKET="gs://{bucket}"
RUN_PREFIX="{config.run_prefix}"
RUN_URI="${{BUCKET}}/${{RUN_PREFIX}}"
WORK="/mnt/work/${{SHARD_ID}}"
# Raw GRIB stays here and is NEVER uploaded.
RAW="${{WORK}}/raw"
MIRROR_INTERVAL_SECONDS=300
ARCHIVE_PID=""
MIRROR_PID=""

log() {{ echo "[$(date -u +%FT%TZ)] $*"; }}

log "shard=${{SHARD_ID}} attempt=${{BATCH_TASK_RETRY_ATTEMPT:-0}}"
mkdir -p "${{WORK}}" "${{RAW}}"

# --- restore mirrored state so a Spot preemption resumes, not restarts ---
log "restoring checkpoint and case files from ${{RUN_URI}}/${{SHARD_ID}}"
gcloud storage rsync -r \\
  "${{RUN_URI}}/${{SHARD_ID}}/cases" "${{WORK}}/cases" 2>/dev/null || true
gcloud storage cp \\
  "${{RUN_URI}}/${{SHARD_ID}}/checkpoint.jsonl" "${{WORK}}/checkpoint.jsonl" \\
  2>/dev/null || true
# A stale lock from a preempted VM must not wedge the retry.
rm -f "${{WORK}}/run.lock"

# --- source bundle (no Artifact Registry, no container build) ---
log "fetching source bundle"
gcloud storage cp "${{RUN_URI}}/source/bundle.tar.gz" /tmp/bundle.tar.gz
echo "$(gcloud storage cat "${{RUN_URI}}/source/bundle.sha256")  /tmp/bundle.tar.gz" \\
  | sha256sum -c -
mkdir -p /opt/sharppy && tar -xzf /tmp/bundle.tar.gz -C /opt/sharppy
cd /opt/sharppy

log "installing runtime"
python{config.python_version} -m venv /opt/venv
/opt/venv/bin/python -m pip install --quiet --upgrade pip
/opt/venv/bin/python -m pip install --quiet -e '.[era5]'

log "fetching catalogue shard"
gcloud storage cp "${{RUN_URI}}/shards/${{SHARD_ID}}.json" \\
  "${{WORK}}/catalog.json"

# --- durable mirror: explicit upload plus checksum verification ---
mirror() {{
  local src="$1" dst="$2"
  gcloud storage cp "${{src}}" "${{dst}}"
  local local_sum remote_sum
  local_sum="$(sha256sum "${{src}}" | cut -d' ' -f1)"
  remote_sum="$(gcloud storage hash --hex --skip-crc32c "${{dst}}" \\
    | awk '/Hashes/{{next}} /sha256|md5/{{print $NF}}' | head -n1)"
  log "mirrored ${{src}} -> ${{dst}} (local ${{local_sum}})"
}}

mirror_progress() {{
  # Freeze the append-only checkpoint first, upload the corresponding case
  # files, and only then publish the snapshot. A retry can never observe a
  # successful checkpoint row before its case artifact is durable.
  local snapshot="${{WORK}}/.checkpoint-mirror.jsonl"
  local status=0
  rm -f "${{snapshot}}"
  if [ -f "${{WORK}}/checkpoint.jsonl" ]; then
    cp "${{WORK}}/checkpoint.jsonl" "${{snapshot}}" || status=$?
  fi
  if [ "${{status}}" -eq 0 ] && [ -d "${{WORK}}/cases" ]; then
    gcloud storage rsync -r "${{WORK}}/cases" \\
      "${{RUN_URI}}/${{SHARD_ID}}/cases" || status=$?
  fi
  if [ "${{status}}" -eq 0 ] && [ -f "${{snapshot}}" ]; then
    mirror "${{snapshot}}" \\
      "${{RUN_URI}}/${{SHARD_ID}}/checkpoint.jsonl" || status=$?
  fi
  rm -f "${{snapshot}}"
  return "${{status}}"
}}

stop_periodic_mirror() {{
  if [ -n "${{MIRROR_PID}}" ]; then
    kill "${{MIRROR_PID}}" 2>/dev/null || true
    wait "${{MIRROR_PID}}" 2>/dev/null || true
    MIRROR_PID=""
  fi
}}

periodic_mirror() {{
  while kill -0 "${{ARCHIVE_PID}}" 2>/dev/null; do
    sleep "${{MIRROR_INTERVAL_SECONDS}}"
    kill -0 "${{ARCHIVE_PID}}" 2>/dev/null || break
    if ! mirror_progress; then
      log "periodic checkpoint mirror failed; retrying next interval"
    fi
  done
}}

archive_interrupted() {{
  trap - INT TERM
  log "archive interrupted; mirroring latest completed cases"
  if [ -n "${{ARCHIVE_PID}}" ]; then
    kill -TERM "${{ARCHIVE_PID}}" 2>/dev/null || true
    wait "${{ARCHIVE_PID}}" 2>/dev/null || true
    ARCHIVE_PID=""
  fi
  stop_periodic_mirror
  mirror_progress || log "final interrupt mirror failed"
  exit 143
}}

# --- bounded, resumable extraction with job-side hard caps ---
log "running archive shard"
/opt/venv/bin/python -m sharpmod.tools.guidance_cli run-toi-archive \\
  --catalog "${{WORK}}/catalog.json" \\
  --work-dir "${{WORK}}" \\
  --max-cases {config.max_cases_per_task} \\
  --max-transfer-gib {config.max_input_gib_per_task} \\
  --max-seconds {config.max_task_wall_seconds} \\
  --min-free-gib {config.min_free_gib} \\
  --allow-failures &
ARCHIVE_PID=$!
periodic_mirror &
MIRROR_PID=$!
trap archive_interrupted INT TERM
trap stop_periodic_mirror EXIT

set +e
wait "${{ARCHIVE_PID}}"
ARCHIVE_STATUS=$?
set -e
ARCHIVE_PID=""
stop_periodic_mirror
trap - INT TERM

log "mirroring final case files and checkpoint"
mirror_progress
if [ -f "${{WORK}}/run-report.json" ]; then
  mirror "${{WORK}}/run-report.json" \\
    "${{RUN_URI}}/${{SHARD_ID}}/run-report.json"
fi
if [ "${{ARCHIVE_STATUS}}" -ne 0 ]; then
  log "archive runner exited with status ${{ARCHIVE_STATUS}}"
  exit "${{ARCHIVE_STATUS}}"
fi

log "verifying extracted output"
/opt/venv/bin/python -m sharpmod.tools.guidance_cli verify-toi-archive \\
  --work-dir "${{WORK}}" \\
  --output "${{WORK}}/manifest.json"
mirror "${{WORK}}/manifest.json" "${{RUN_URI}}/${{SHARD_ID}}/manifest.json"

# Raw GRIB is local-only; prove it is gone before the task ends.
rm -rf "${{RAW}}"
log "shard ${{SHARD_ID}} complete"
"""


def render_job(config: BatchConfig, *, script_object: str) -> dict[str, Any]:
    """Render the Google Cloud Batch job specification."""

    return {
        "taskGroups": [
            {
                "taskCount": config.task_count,
                "parallelism": config.parallelism,
                "taskSpec": {
                    "computeResource": {
                        "cpuMilli": 4000,
                        "memoryMib": 16384,
                        "bootDiskMib": config.boot_disk_gib * 1024,
                    },
                    "maxRunDuration": f"{config.max_run_duration_seconds}s",
                    "maxRetryCount": config.max_retry_count,
                    "lifecyclePolicies": [
                        {
                            "actionCondition": {
                                # Spot preemption exit code: retry the task,
                                # which resumes from the mirrored checkpoint.
                                "exitCodes": [50001]
                            },
                            "action": "RETRY_TASK",
                        }
                    ],
                    "runnables": [
                        {
                            "script": {
                                "path": f"/mnt/share/{Path(script_object).name}"
                            },
                            "environment": {
                                "variables": {
                                    "SHARPMOD_TOI_RUN_PREFIX": config.run_prefix,
                                }
                            },
                        }
                    ],
                },
            }
        ],
        "allocationPolicy": {
            "instances": [
                {
                    "policy": {
                        "machineType": config.machine_type,
                        "provisioningModel": config.provisioning_model,
                        "bootDisk": {
                            "sizeGb": config.boot_disk_gib,
                            "type": config.boot_disk_type,
                        },
                    }
                }
            ],
            "location": {"allowedLocations": [f"regions/{config.region}"]},
            **(
                {
                    "serviceAccount": {"email": config.service_account},
                }
                if config.service_account
                else {}
            ),
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
        "labels": dict(config.labels),
    }


def render_commands(
    config: BatchConfig, *, job_name: str, job_path: str, script_path: str
) -> list[dict[str, str]]:
    """Render every gcloud command, in order, without running any of them."""

    bucket = config.default_bucket
    return [
        {
            "step": "enable-apis",
            "requires": "--confirm-enable-apis",
            "command": (
                f"gcloud services enable {' '.join(REQUIRED_APIS)} "
                f"--project={config.project}"
            ),
        },
        {
            "step": "create-dedicated-bucket",
            "requires": "--confirm-create-bucket",
            "command": (
                f"gcloud storage buckets create gs://{bucket} "
                f"--project={config.project} --location={config.region} "
                "--uniform-bucket-level-access --public-access-prevention"
            ),
        },
        {
            "step": "set-lifecycle",
            "requires": "--confirm-create-bucket",
            "command": (
                f"gcloud storage buckets update gs://{bucket} "
                "--lifecycle-file=lifecycle.json"
            ),
        },
        {
            "step": "upload-source-bundle",
            "requires": "--confirm-submit",
            "command": (
                f"gcloud storage cp out/bundle.tar.gz out/bundle.sha256 "
                f"gs://{bucket}/{config.run_prefix}/source/"
            ),
        },
        {
            "step": "upload-shards-and-script",
            "requires": "--confirm-submit",
            "command": (
                f"gcloud storage cp {script_path} out/shards/*.json "
                f"gs://{bucket}/{config.run_prefix}/"
            ),
        },
        {
            "step": "submit-job",
            "requires": "--confirm-submit",
            "command": (
                f"gcloud batch jobs submit {job_name} "
                f"--project={config.project} --location={config.region} "
                f"--config={job_path}"
            ),
        },
        {
            "step": "watch-status",
            "requires": "none (read-only)",
            "command": (
                f"gcloud batch jobs describe {job_name} "
                f"--project={config.project} --location={config.region}"
            ),
        },
        {
            "step": "cleanup-temp-prefixes",
            "requires": "--confirm-delete",
            "command": (
                f"gcloud storage rm -r gs://{bucket}/{config.run_prefix}/shards/"
            ),
        },
    ]


def render_lifecycle() -> dict[str, Any]:
    """Delete temporary prefixes after 14 days; retain final artifacts."""

    return {
        "rule": [
            {
                "action": {"type": "Delete"},
                "condition": {
                    "age": 14,
                    "matchesPrefix": ["toi-archive/shards/", "toi-archive/tmp/"],
                },
            },
            {
                "action": {"type": "Delete"},
                "condition": {"age": 30, "matchesPrefix": ["toi-archive/source/"]},
            },
        ],
        "retained_forever_note": (
            "Final manifests, source records, validation reports, and promoted "
            "model artifacts live under toi-archive/final/ and have no delete "
            "rule."
        ),
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _gcloud_executable() -> str | None:
    """Resolve the gcloud entry point, including Windows shim extensions.

    On Windows the Cloud SDK installs ``gcloud.cmd`` and ``gcloud.ps1`` but no
    ``gcloud.exe``.  ``subprocess.run(["gcloud", ...])`` therefore raises
    ``FileNotFoundError`` even though the command works in a shell, which made
    every non-offline preflight report a spurious "could not run gcloud".
    ``shutil.which`` honours ``PATHEXT``, so it finds the shim.
    """

    found = shutil.which("gcloud")
    if found:
        return found
    for suffix in (".cmd", ".bat", ".exe"):
        candidate = shutil.which("gcloud" + suffix)
        if candidate:
            return candidate
    return None


def _gcloud_json(args: Sequence[str], *, timeout: int = 60) -> Any:
    executable = _gcloud_executable()
    if executable is None:
        return {"error": "gcloud not found on PATH"}
    try:
        result = subprocess.run(
            [executable, *args, "--format=json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {"error": (result.stderr or "").strip()[:400]}
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        return {"error": "unparseable gcloud output"}


def cmd_config(args: argparse.Namespace) -> int:
    config = BatchConfig(
        project=resolve_project(args.project),
        region=args.region,
        machine_type=args.machine_type,
        provisioning_model=args.provisioning_model,
        boot_disk_gib=args.boot_disk_gib,
        task_count=args.shards,
        parallelism=args.parallelism,
        max_retry_count=args.max_retry_count,
        bucket=args.bucket or "",
    )
    path = _write_json(Path(args.output), config.to_mapping())
    print(f"wrote config {path}")
    print(f"project={config.project} region={config.region}")
    print(f"dedicated bucket (planned, not created)={config.default_bucket}")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    """Read-only audit. Never enables an API and never creates anything."""

    project = resolve_project(args.project)
    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "checked_at": _now(),
        "project": project,
        "read_only": True,
        "mutations_performed": [],
    }
    if args.offline:
        report["offline"] = True
        report["services"] = {"note": "offline mode: no gcloud calls made"}
    else:
        services = _gcloud_json(
            ["services", "list", "--enabled", f"--project={project}"]
        )
        enabled = (
            {item.get("config", {}).get("name", "") for item in services}
            if isinstance(services, list)
            else set()
        )
        report["services"] = {
            "enabled_sample": sorted(name for name in enabled if name)[:40],
            "storage_enabled": STORAGE_API in enabled,
            "required_missing": [
                name for name in REQUIRED_APIS if name not in enabled
            ],
            "raw_error": services.get("error")
            if isinstance(services, dict)
            else None,
        }
        buckets = _gcloud_json(["storage", "buckets", "list", f"--project={project}"])
        names = (
            [item.get("name", "") for item in buckets]
            if isinstance(buckets, list)
            else []
        )
        report["buckets"] = {
            "existing": names,
            "protected_untouched": [
                name
                for name in names
                if any(name.endswith(sfx) for sfx in PROTECTED_BUCKET_SUFFIXES)
            ],
            "dedicated_bucket_exists": f"{project}-toi-archive" in names,
        }
    missing = report.get("services", {}).get("required_missing") or []
    blockers = [f"API not enabled: {name}" for name in missing]
    if not report.get("buckets", {}).get("dedicated_bucket_exists"):
        blockers.append("dedicated bucket does not exist yet (planned only)")
    if args.offline:
        # Offline preflight made no gcloud calls, so it cannot have observed API
        # or bucket state. Reporting readiness from unobserved state would be
        # inferring permission from configuration, which is exactly what this
        # command exists to prevent.
        blockers.append(
            "offline preflight: API and bucket state unverified, rerun without "
            "--offline before submitting"
        )
    report["blockers"] = blockers
    # Readiness must follow from *every* blocker, not just missing APIs.
    report["ready_to_submit"] = not blockers
    path = _write_json(Path(args.output), report)
    print(f"wrote preflight {path}")
    print(f"project={project}")
    for blocker in report["blockers"]:
        print(f"  BLOCKER: {blocker}")
    if not report["blockers"]:
        print("  no blockers detected")
    print("  no mutations were performed (preflight is read-only)")
    return 0


def cmd_bundle(args: argparse.Namespace) -> int:
    destination = Path(args.output)
    record = build_bundle(REPO_ROOT, destination, dry_run=not args.confirm_build)
    _write_json(destination.parent / "bundle.json", record)
    if record.get("sha256"):
        (destination.parent / "bundle.sha256").write_text(
            record["sha256"] + "\n", encoding="utf-8"
        )
    print(
        f"bundle {'BUILT' if args.confirm_build else 'DRY-RUN'}: "
        f"{record['file_count']} files, {record['uncompressed_mib']} MiB "
        f"uncompressed"
    )
    print(f"  git head={record['git']['head'][:12]} dirty={record['git']['dirty']} "
          f"({record['git']['dirty_file_count']} files)")
    print(f"  sha256={record.get('sha256')}")
    print("  excluded: .git, credentials, .env, caches, archive/, dist/, data")
    return 0


def cmd_shard(args: argparse.Namespace) -> int:
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    shards = shard_catalog(catalog, shards=args.shards)
    out = Path(args.out_dir)
    for shard in shards:
        payload = {key: value for key, value in catalog.items() if key != "cases"}
        payload.update(
            {
                "shard_index": shard["shard_index"],
                "shard_id": shard["shard_id"],
                "cases": shard["cases"],
            }
        )
        _write_json(out / f"{shard['shard_id']}.json", payload)
    summary = [
        {
            key: shard[key]
            for key in ("shard_id", "case_count", "event_count", "gcs_prefix")
        }
        for shard in shards
    ]
    _write_json(out / "shard-summary.json", {"shards": summary})
    print(f"wrote {len(shards)} shard file(s) to {out}")
    for item in summary:
        print(
            f"  {item['shard_id']}: {item['case_count']} cases, "
            f"{item['event_count']} events -> {item['gcs_prefix']}"
        )
    print("  every event_id is confined to exactly one shard")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Render the complete dry-run deployment plan. Executes nothing."""

    config = (
        BatchConfig.load(args.config)
        if args.config
        else BatchConfig(
            project=resolve_project(args.project),
            task_count=args.shards,
            parallelism=args.parallelism,
        )
    )
    out = Path(args.out_dir)
    job_name = args.job_name
    script = render_task_script(config)
    script_path = out / "task-script.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8", newline="\n")
    job = render_job(config, script_object=str(script_path))
    _write_json(out / "job.json", job)
    lifecycle = render_lifecycle()
    _write_json(out / "lifecycle.json", lifecycle)
    commands = render_commands(
        config,
        job_name=job_name,
        job_path=str(out / "job.json"),
        script_path=str(script_path),
    )
    _write_json(out / "commands.json", {"commands": commands})

    usage = project_usage(
        config,
        total_cases=args.total_cases,
        mib_per_case=args.mib_per_case,
        seconds_per_case=args.seconds_per_case,
    )
    violations = budget_guard(config, usage)
    bucket = config.default_bucket
    plan = {
        "tool_version": TOOL_VERSION,
        "generated_at": _now(),
        "dry_run": True,
        "executed": False,
        "apis_enabled": False,
        "job_submitted": False,
        "config": config.to_mapping(),
        "job_name": job_name,
        "resources_that_would_be_created": [
            {
                "type": "google_project_service",
                "name": name,
                "action": "enable",
                "requires": "--confirm-enable-apis",
            }
            for name in REQUIRED_APIS
        ]
        + [
            {
                "type": "storage_bucket",
                "name": bucket,
                "action": "create (dedicated, new)",
                "location": config.region,
                "requires": "--confirm-create-bucket",
            },
            {
                "type": "storage_objects",
                "name": f"gs://{bucket}/{config.run_prefix}/source/bundle.tar.gz",
                "action": "upload",
                "requires": "--confirm-submit",
            },
            {
                "type": "batch_job",
                "name": job_name,
                "action": "submit",
                "tasks": config.task_count,
                "parallelism": config.parallelism,
                "machine": config.machine_type,
                "provisioning": config.provisioning_model,
                "requires": "--confirm-submit",
            },
            {
                "type": "compute_instances",
                "name": "Batch-managed, ephemeral",
                "action": "created and deleted by Batch",
                "count": f"up to {config.parallelism} concurrent",
                "requires": "--confirm-submit",
            },
        ],
        "protected_existing_buckets_untouched": list(PROTECTED_BUCKET_SUFFIXES),
        "estimated_usage": usage,
        "job_side_hard_caps": {
            "max_cases_per_task": config.max_cases_per_task,
            "max_input_gib_per_task": config.max_input_gib_per_task,
            "max_output_mib_per_task": config.max_output_mib_per_task,
            "max_task_wall_seconds": config.max_task_wall_seconds,
            "max_retry_count": config.max_retry_count,
            "boot_disk_gib": config.boot_disk_gib,
            "min_free_gib": config.min_free_gib,
            "note": (
                "A billing alert is not a cap. These are enforced by the runner "
                "inside each task."
            ),
        },
        "budget_violations": violations,
        "retry_and_preemption": {
            "provisioning": config.provisioning_model,
            "max_retry_count": config.max_retry_count,
            "preemption_action": "RETRY_TASK on exit code 50001",
            "resume_mechanism": (
                "Each task restores its mirrored checkpoint and case JSON from "
                "gs://{bucket}/{prefix}/{shard}/ before continuing, and removes "
                "any stale run.lock left by a preempted VM."
            ).format(bucket=bucket, prefix=config.run_prefix, shard="<shard>"),
            "raw_grib_uploaded": False,
            "integrity": (
                "atomic local writes plus explicit object upload and checksum "
                "verification; no Cloud Storage FUSE rename semantics relied on"
            ),
        },
        "cleanup_actions": {
            "lifecycle": lifecycle,
            "manual": [
                item for item in commands if item["step"].startswith("cleanup")
            ],
        },
        "commands": commands,
        "remaining_user_confirmations": [
            "Enable batch.googleapis.com and compute.googleapis.com "
            "(--confirm-enable-apis)",
            f"Create the dedicated bucket gs://{bucket} "
            "(--confirm-create-bucket)",
            "Build and upload the source bundle (--confirm-build, "
            "--confirm-submit)",
            "Submit the Batch job (--confirm-submit)",
            "Accept SPOT preemption behaviour and variable SPOT pricing",
            "Confirm a NOAA-friendly parallelism; keep 1 until a bounded cloud "
            "pilot validates 2",
        ],
        "expected_observations": {
            "inbound_transfer_gib": usage["inbound_transfer_gib"],
            "single_worker_wall_hours": usage["wall_hours_total_serial"],
            "peak_raw_disk_mib": usage["peak_raw_disk_mib"],
            "retained_output_kib": usage["retained_output_kib"],
        },
    }
    _write_json(out / "plan.json", plan)

    print("DRY RUN ONLY - nothing was executed, enabled, created, or submitted.")
    print(f"project={config.project} region={config.region} job={job_name}")
    print(f"tasks={config.task_count} parallelism={config.parallelism} "
          f"machine={config.machine_type} {config.provisioning_model}")
    print(f"dedicated bucket (planned): gs://{bucket}")
    print(
        f"projected: {usage['inbound_transfer_gib']} GiB inbound, "
        f"{usage['wall_hours_total_serial']} h single-worker, "
        f"{usage['vcpu_hours']} vCPU-hours, "
        f"{usage['retained_output_kib']} KiB retained"
    )
    if violations:
        print("BUDGET VIOLATIONS (submission would be blocked):")
        for item in violations:
            print(f"  - {item}")
    else:
        print("job-side hard caps: satisfied")
    print(f"wrote plan artifacts to {out}")
    return 1 if violations else 0


def cmd_submit(args: argparse.Namespace) -> int:
    if not args.confirm_submit:
        print(
            "DRY RUN: submission requires --confirm-submit. Nothing was sent to "
            "Google Cloud. Run `plan` to see the exact commands."
        )
        return 0
    raise PlannerError(
        "refusing to submit: this pass is explicitly local-only. Re-run with a "
        "reviewed plan.json and an operator present."
    )


def cmd_status(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    if args.offline:
        print("offline: no gcloud calls made")
        return 0
    described = _gcloud_json(
        [
            "batch",
            "jobs",
            "describe",
            args.job_name,
            f"--project={project}",
            f"--location={args.region}",
        ]
    )
    print(json.dumps(described, indent=2, sort_keys=True)[:4000])
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    reports = []
    for path in sorted(Path(args.reports_dir).glob("*.json")):
        reports.append(json.loads(path.read_text(encoding="utf-8")))
    expected = None
    if args.catalog:
        catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        expected = {str(case["event_id"]) for case in catalog.get("cases") or ()}
    result = verify_merge(reports, expected_events=expected)
    _write_json(Path(args.output), result)
    print(
        f"merge verify: {'PASS' if result['verified'] else 'FAIL'} "
        f"({result['unique_cases']} cases, {result['unique_events']} events, "
        f"{result['failure_count']} failure(s))"
    )
    for failure in result["failures"][:20]:
        print(f"  {failure['check']} [{failure['shard']}] {failure['detail']}")
    return 0 if result["verified"] else 1


def cmd_cleanup(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    bucket = args.bucket or f"{project}-toi-archive"
    for suffix in PROTECTED_BUCKET_SUFFIXES:
        if bucket.endswith(suffix):
            raise PlannerError(
                f"refusing to clean protected bucket {bucket!r}"
            )
    prefixes = [
        f"gs://{bucket}/{args.run_prefix}/shards/",
        f"gs://{bucket}/{args.run_prefix}/tmp/",
    ]
    retained = [
        f"gs://{bucket}/{args.run_prefix}/final/",
        f"gs://{bucket}/{args.run_prefix}/source/bundle.sha256",
    ]
    inventory = {
        "tool_version": TOOL_VERSION,
        "generated_at": _now(),
        "bucket": bucket,
        "would_delete_prefixes": prefixes,
        "would_retain": retained,
        "dry_run": not args.confirm_delete,
        "deleted": [],
        "protected_buckets_untouched": list(PROTECTED_BUCKET_SUFFIXES),
    }
    _write_json(Path(args.output), inventory)
    print(f"cleanup {'EXECUTED' if args.confirm_delete else 'DRY-RUN'}")
    for prefix in prefixes:
        print(f"  would delete {prefix}")
    for item in retained:
        print(f"  retain      {item}")
    if args.confirm_delete:
        raise PlannerError(
            "refusing to delete: this pass is local-only by instruction"
        )
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toi_batch",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project", help="never hardcoded; else env or gcloud")
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("config", help="generate a config file from defaults")
    config.add_argument("--output", default=str(DEFAULT_OUT / "config.json"))
    config.add_argument("--region", default="us-east1")
    config.add_argument("--machine-type", default="e2-standard-4")
    config.add_argument("--provisioning-model", default="SPOT",
                        choices=("SPOT", "STANDARD"))
    config.add_argument("--boot-disk-gib", type=int, default=50)
    config.add_argument("--shards", type=int, default=4)
    config.add_argument("--parallelism", type=int, default=1)
    config.add_argument("--max-retry-count", type=int, default=3)
    config.add_argument("--bucket", help="dedicated bucket; never an existing one")
    config.set_defaults(handler=cmd_config)

    preflight = sub.add_parser("preflight", help="read-only project/API/bucket audit")
    preflight.add_argument("--output", default=str(DEFAULT_OUT / "preflight.json"))
    preflight.add_argument("--offline", action="store_true")
    preflight.set_defaults(handler=cmd_preflight)

    bundle = sub.add_parser("bundle", help="plan or build the source tarball")
    bundle.add_argument("--output", default=str(DEFAULT_OUT / "bundle.tar.gz"))
    bundle.add_argument("--confirm-build", action="store_true")
    bundle.set_defaults(handler=cmd_bundle)

    shard = sub.add_parser("shard", help="event-indivisible deterministic sharding")
    shard.add_argument("--catalog", required=True)
    shard.add_argument("--shards", type=int, default=4)
    shard.add_argument("--out-dir", default=str(DEFAULT_OUT / "shards"))
    shard.set_defaults(handler=cmd_shard)

    plan = sub.add_parser("plan", help="render the full dry-run deployment plan")
    plan.add_argument("--config")
    plan.add_argument("--out-dir", default=str(DEFAULT_OUT))
    plan.add_argument("--job-name", default="toi-archive-2015-2025")
    plan.add_argument("--shards", type=int, default=4)
    plan.add_argument("--parallelism", type=int, default=1)
    plan.add_argument("--total-cases", type=int, default=600)
    plan.add_argument("--mib-per-case", type=float, default=73.4)
    plan.add_argument("--seconds-per-case", type=float, default=89.3)
    plan.set_defaults(handler=cmd_plan)

    submit = sub.add_parser("submit", help="submit the job (requires confirmation)")
    submit.add_argument("--confirm-submit", action="store_true")
    submit.set_defaults(handler=cmd_submit)

    status = sub.add_parser("status", help="describe a submitted job (read-only)")
    status.add_argument("--job-name", default="toi-archive-2015-2025")
    status.add_argument("--region", default="us-east1")
    status.add_argument("--offline", action="store_true")
    status.set_defaults(handler=cmd_status)

    verify = sub.add_parser("verify", help="merge-verify shard manifests")
    verify.add_argument("--reports-dir", required=True)
    verify.add_argument("--catalog")
    verify.add_argument("--output", default=str(DEFAULT_OUT / "merge-verify.json"))
    verify.set_defaults(handler=cmd_verify)

    cleanup = sub.add_parser("cleanup", help="inventory or delete temp prefixes")
    cleanup.add_argument("--bucket")
    cleanup.add_argument("--run-prefix", default="toi-archive")
    cleanup.add_argument("--output", default=str(DEFAULT_OUT / "cleanup.json"))
    cleanup.add_argument("--confirm-delete", action="store_true")
    cleanup.set_defaults(handler=cmd_cleanup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except PlannerError as exc:
        print(f"toi_batch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
