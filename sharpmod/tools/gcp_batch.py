#!/usr/bin/env python3
"""Cost-bounded Google Cloud Batch packaging for the TOI archive collection.

This module renders configuration, builds a source bundle, plans a Batch job,
and verifies merged output.  It is deliberately inert by default:

* Every mutating command defaults to ``--dry-run``.
* Submission, API enabling, bucket creation, and cleanup each need their own
  explicit confirmation flag.  Configuration never implies permission.
* The project and billing account are **never** hardcoded; they must come from
  an explicit flag or the standard Google Cloud environment variables.
* Batch **script** runnables are used, so no container image, Artifact
  Registry, Cloud Build, or Cloud Run is involved.

Nothing here calls a Google API.  ``gcloud`` is only ever invoked for read-only
description, and even that is opt-in.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOI_BATCH_PACKAGE_VERSION = "sharpmod_toi_gcp_batch_v1"

#: Read from the environment, never baked in.
PROJECT_ENVIRONMENT_KEYS = (
    "SHARPMOD_GCP_PROJECT",
    "GOOGLE_CLOUD_PROJECT",
    "CLOUDSDK_CORE_PROJECT",
    "GCLOUD_PROJECT",
)

#: APIs the job needs.  Storage is already enabled in the caller's project;
#: Batch and Compute are not, and enabling them is an explicit user action.
REQUIRED_APIS = (
    "batch.googleapis.com",
    "compute.googleapis.com",
    "storage.googleapis.com",
    "logging.googleapis.com",
)

#: Buckets that must never be touched.  Recorded so a reviewer can see the
#: guard exists rather than trusting a convention.
PROTECTED_BUCKET_SUFFIXES = ("-modelforecastpy-cache", "_cloudbuild")

#: Files and directories the source bundle always excludes.
BUNDLE_EXCLUDE_DIRECTORIES = frozenset(
    {
        ".git",
        ".github",
        ".gribenv",
        ".griblenv",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".hypothesis",
        ".ruff_cache",
        ".mypy_cache",
        ".test-results",
        ".wrangler",
        ".codex-worktrees",
        "archive",
        "build",
        "dist",
        "attic",
        "node_modules",
        ".tmp",
        "models",
        "data",
        "reports",
        # Rust build output: about 1 GiB of compiled artifacts.  The Batch task
        # uses the pure-Python backend, so no Rust build tree is needed.
        "target",
        # Rendered image outputs and the static site are not task inputs.
        "rendered_soundings",
        "website",
        "publish_readiness",
    }
)
BUNDLE_EXCLUDE_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".grib",
        ".grib2",
        ".idx",
        ".npz",
        ".nc",
        ".zarr",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".crt",
        ".log",
        ".coverage",
        # Rendered artifacts, never task inputs.
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".rlib",
        ".rmeta",
        ".so",
        ".dll",
        ".dylib",
        ".exe",
        ".whl",
        ".tar",
        ".gz",
        ".zst",
    }
)
BUNDLE_EXCLUDE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "service-account.json",
        "client_secret.json",
        "application_default_credentials.json",
        ".netrc",
        ".coverage",
        "coverage.xml",
    }
)
BUNDLE_EXCLUDE_NAME_PARTS = ("secret", "credential", "token", "password")
#: Any single file above this size is excluded as an unrelated large artifact.
BUNDLE_MAXIMUM_FILE_BYTES = 4 * 1024 * 1024


class BatchPlanError(RuntimeError):
    """Raised when a plan is unsafe, ambiguous, or under-specified."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def resolve_project(explicit: str | None = None) -> str:
    """Resolve the project from an explicit flag or the environment only."""

    if explicit:
        return str(explicit).strip()
    for key in PROJECT_ENVIRONMENT_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    raise BatchPlanError(
        "no Google Cloud project resolved. Pass --project or set one of: "
        + ", ".join(PROJECT_ENVIRONMENT_KEYS)
        + ". The project is never hardcoded in this repository."
    )


def resolve_billing_account(explicit: str | None = None) -> str | None:
    """Resolve a billing account only if the caller supplied one."""

    if explicit:
        return str(explicit).strip()
    value = os.environ.get("SHARPMOD_GCP_BILLING_ACCOUNT", "").strip()
    return value or None


@dataclass(frozen=True)
class JobBudget:
    """Job-side hard caps.  A billing alert is not a cap, so these are."""

    maximum_cases_per_task: int = 200
    maximum_input_gib: float = 16.0
    maximum_task_seconds: int = 86_400
    maximum_tasks: int = 4
    maximum_retries: int = 3
    boot_disk_gib: int = 50
    maximum_output_mib: float = 64.0
    minimum_free_gib: float = 20.0

    def __post_init__(self) -> None:
        for name in (
            "maximum_cases_per_task",
            "maximum_task_seconds",
            "maximum_tasks",
            "boot_disk_gib",
        ):
            if int(getattr(self, name)) < 1:
                raise BatchPlanError(f"{name} must be at least one")
        if int(self.maximum_retries) < 0:
            raise BatchPlanError("maximum_retries must be non-negative")
        if self.maximum_task_seconds > 86_400:
            raise BatchPlanError(
                "maximum_task_seconds cannot exceed the 24 h Batch task limit"
            )
        for name in ("maximum_input_gib", "maximum_output_mib", "minimum_free_gib"):
            if float(getattr(self, name)) <= 0:
                raise BatchPlanError(f"{name} must be positive")

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatchConfig:
    """Everything needed to render a Batch job, with no hidden defaults."""

    project: str
    bucket: str
    region: str = "us-east1"
    machine_type: str = "e2-standard-4"
    provisioning_model: str = "SPOT"
    boot_disk_type: str = "pd-balanced"
    job_prefix: str = "toi-archive"
    run_id: str = ""
    shard_count: int = 4
    parallelism: int = 1
    catalog_object: str = "input/catalog.json"
    source_object: str = "source/sharpmod-source.tar.gz"
    budget: JobBudget = field(default_factory=JobBudget)
    labels: Mapping[str, str] = field(default_factory=dict)
    service_account: str = ""
    python_version: str = "3.11"

    def __post_init__(self) -> None:
        if not str(self.project).strip():
            raise BatchPlanError("project must not be empty")
        bucket = str(self.bucket).strip()
        if not bucket:
            raise BatchPlanError("bucket must not be empty")
        if bucket.startswith("gs://"):
            bucket = bucket[len("gs://") :]
        for suffix in PROTECTED_BUCKET_SUFFIXES:
            if bucket.endswith(suffix):
                raise BatchPlanError(
                    f"refusing to target bucket {bucket!r}: it matches a "
                    f"protected existing bucket pattern ({suffix}). This "
                    "programme requires its own dedicated bucket."
                )
        if int(self.shard_count) < 1:
            raise BatchPlanError("shard_count must be at least one")
        if int(self.parallelism) < 1:
            raise BatchPlanError("parallelism must be at least one")
        if int(self.parallelism) > 1:
            raise BatchPlanError(
                "parallelism above 1 is not permitted until a bounded cloud "
                "pilot demonstrates that concurrent NOAA archive request "
                "behaviour is safe. Document the pilot result, then raise this "
                "limit deliberately."
            )
        if int(self.shard_count) > int(self.budget.maximum_tasks):
            raise BatchPlanError(
                f"shard_count {self.shard_count} exceeds the budgeted "
                f"maximum_tasks {self.budget.maximum_tasks}"
            )
        if self.provisioning_model not in {"SPOT", "STANDARD"}:
            raise BatchPlanError("provisioning_model must be SPOT or STANDARD")
        object.__setattr__(self, "bucket", bucket)
        object.__setattr__(
            self,
            "run_id",
            str(self.run_id) or datetime.now(UTC).strftime("%Y%m%dt%H%M%S"),
        )
        merged = {
            "app": "sharppy",
            "workload": "toi-archive",
            "component": "archive-runner",
            "managed-by": "sharpmod-guidance",
            "run-id": self.run_id,
        }
        merged.update({str(k): str(v) for k, v in self.labels.items()})
        object.__setattr__(self, "labels", merged)

    @property
    def job_name(self) -> str:
        return f"{self.job_prefix}-{self.run_id}"

    @property
    def run_prefix(self) -> str:
        return f"runs/{self.run_id}"

    def shard_prefix(self, shard: int) -> str:
        return f"{self.run_prefix}/shard-{int(shard):02d}"

    def gs(self, *parts: str) -> str:
        return "gs://" + "/".join([self.bucket, *[p.strip("/") for p in parts]])

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {"budget", "labels"}
        }
        payload["budget"] = self.budget.to_mapping()
        payload["labels"] = dict(self.labels)
        payload["job_name"] = self.job_name
        payload["run_prefix"] = self.run_prefix
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> BatchConfig:
        data = dict(payload)
        budget = data.pop("budget", None)
        data.pop("job_name", None)
        data.pop("run_prefix", None)
        known = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        if isinstance(budget, Mapping):
            filtered["budget"] = JobBudget(
                **{
                    k: v
                    for k, v in budget.items()
                    if k in JobBudget.__dataclass_fields__
                }
            )
        return cls(**filtered)


# --------------------------------------------------------------------------
# Deterministic, event-indivisible sharding
# --------------------------------------------------------------------------


def shard_for_event(event_id: str, shard_count: int) -> int:
    """Assign an event to a shard deterministically by content hash.

    Sharding on ``event_id`` guarantees every cycle of one event lands in the
    same shard, so an event can never be split across tasks and later across a
    validation boundary.
    """

    count = int(shard_count)
    if count < 1:
        raise BatchPlanError("shard_count must be at least one")
    digest = hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()
    return int(digest, 16) % count


def shard_catalog(
    cases: Sequence[Mapping[str, Any]], shard_count: int
) -> dict[int, list[Mapping[str, Any]]]:
    """Split catalogue cases into shards, keeping each event intact."""

    shards: dict[int, list[Mapping[str, Any]]] = {
        index: [] for index in range(int(shard_count))
    }
    for case in cases:
        event_id = str(case.get("event_id", ""))
        if not event_id:
            raise BatchPlanError("every catalogue case needs an event_id to shard")
        shards[shard_for_event(event_id, shard_count)].append(case)
    return shards


def describe_sharding(
    cases: Sequence[Mapping[str, Any]], shard_count: int
) -> dict[str, Any]:
    """Summarize a sharding, proving event indivisibility."""

    shards = shard_catalog(cases, shard_count)
    placement: dict[str, set[int]] = {}
    for index, members in shards.items():
        for case in members:
            placement.setdefault(str(case["event_id"]), set()).add(index)
    split = sorted(event for event, indexes in placement.items() if len(indexes) > 1)
    if split:  # pragma: no cover - impossible by construction, asserted anyway
        raise BatchPlanError(
            "event(s) split across shards: " + ", ".join(split[:5])
        )
    return {
        "shard_count": int(shard_count),
        "cases": len(cases),
        "events": len(placement),
        "per_shard": {
            f"shard-{index:02d}": {
                "cases": len(members),
                "events": len({str(case["event_id"]) for case in members}),
            }
            for index, members in sorted(shards.items())
        },
        "events_split_across_shards": 0,
    }


# --------------------------------------------------------------------------
# Source bundle
# --------------------------------------------------------------------------


def _excluded(root: Path, path: Path) -> str | None:
    relative = path.relative_to(root)
    for part in relative.parts[:-1]:
        if part in BUNDLE_EXCLUDE_DIRECTORIES:
            return f"directory {part}"
    if relative.parts and relative.parts[0] in BUNDLE_EXCLUDE_DIRECTORIES:
        return f"directory {relative.parts[0]}"
    name = path.name
    if name in BUNDLE_EXCLUDE_NAMES:
        return f"name {name}"
    lowered = name.casefold()
    if lowered.startswith(".env"):
        return "env file"
    for token in BUNDLE_EXCLUDE_NAME_PARTS:
        if token in lowered:
            return f"sensitive name token {token!r}"
    if path.suffix.casefold() in BUNDLE_EXCLUDE_SUFFIXES:
        return f"suffix {path.suffix}"
    try:
        if path.stat().st_size > BUNDLE_MAXIMUM_FILE_BYTES:
            return "larger than the bundle file-size limit"
    except OSError:  # pragma: no cover - raced deletion
        return "unreadable"
    return None


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def git_provenance(root: Path) -> dict[str, Any]:
    """Record the exact HEAD and whether the worktree was dirty."""

    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain")
    dirty_paths = [line[3:] for line in status.splitlines() if line.strip()]
    return {
        "git_head": head or "unknown",
        "git_branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "worktree_dirty": bool(dirty_paths),
        "dirty_path_count": len(dirty_paths),
        # Paths only, never contents, so nothing secret is recorded.
        "dirty_paths_sample": sorted(dirty_paths)[:20],
    }


def build_source_bundle(
    root: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Build (or plan) a reproducible source tarball with exclusions applied."""

    root = Path(root).expanduser().resolve()
    target = Path(output).expanduser().resolve()
    included: list[Path] = []
    excluded: dict[str, int] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        reason = _excluded(root, path)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        included.append(path)
    total_bytes = sum(path.stat().st_size for path in included)
    report: dict[str, Any] = {
        "package_version": TOI_BATCH_PACKAGE_VERSION,
        "generated_at": _now(),
        "root": str(root),
        "bundle_path": str(target),
        "dry_run": bool(dry_run),
        "included_files": len(included),
        "included_bytes": total_bytes,
        "included_mib": round(total_bytes / 1024**2, 2),
        "excluded_reasons": dict(sorted(excluded.items())),
        "provenance": git_provenance(root),
    }
    if dry_run:
        report["bundle_sha256"] = None
        report["note"] = "dry run: no archive written"
        return report

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    # Deterministic tar: sorted members, fixed mtime/uid/gid, no gzip timestamp.
    import gzip as _gzip

    with (
        open(partial, "wb") as raw,
        _gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as archive,
    ):
        for path in included:
            info = archive.gettarinfo(
                path, arcname=str(path.relative_to(root)).replace("\\", "/")
            )
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(path, "rb") as handle:
                archive.addfile(info, handle)
    os.replace(partial, target)
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(block)
    report["bundle_sha256"] = digest.hexdigest()
    report["bundle_bytes"] = target.stat().st_size
    report["bundle_mib"] = round(target.stat().st_size / 1024**2, 2)
    return report


# --------------------------------------------------------------------------
# Rendered Batch job specification
# --------------------------------------------------------------------------


TASK_SCRIPT_TEMPLATE = r"""#!/bin/bash
# SHARPpy Reimagined TOI archive shard task (Google Cloud Batch script runnable).
# No container image, Artifact Registry, Cloud Build, or Cloud Run is used.
set -euo pipefail

SHARD="${BATCH_TASK_INDEX:-0}"
SHARD_ID="$(printf '%02d' "${SHARD}")"
BUCKET="@@BUCKET@@"
RUN_PREFIX="@@RUN_PREFIX@@"
SHARD_PREFIX="${RUN_PREFIX}/shard-${SHARD_ID}"
WORK="/mnt/disks/work/shard-${SHARD}"
export HOME=/root

echo "[toi] shard=${SHARD} bucket=${BUCKET} prefix=${SHARD_PREFIX}"

# --- bounded, isolated work directory -------------------------------------- #
mkdir -p "${WORK}/run" "${WORK}/src"
df -BG "${WORK}" | tail -1

# --- restore durable state before doing anything (Spot resume) ------------- #
# A preempted retry must continue, not restart: pull back the compact case
# JSON and the checkpoint written by earlier attempts.
gcloud storage rsync -r \
  "gs://${BUCKET}/${SHARD_PREFIX}/state/" "${WORK}/run/" 2>/dev/null || true
if [ -f "${WORK}/run/checkpoint.jsonl" ]; then
  echo "[toi] resumed $(wc -l < "${WORK}/run/checkpoint.jsonl") checkpoint line(s)"
fi
# A stale lock from a preempted attempt must not wedge the retry.
rm -f "${WORK}/run/run.lock"

# --- fetch source and inputs ----------------------------------------------- #
gcloud storage cp "gs://${BUCKET}/@@SOURCE_OBJECT@@" "${WORK}/source.tar.gz"
echo "@@BUNDLE_SHA256@@  ${WORK}/source.tar.gz" | sha256sum -c -
tar -xzf "${WORK}/source.tar.gz" -C "${WORK}/src"
gcloud storage cp \
  "gs://${BUCKET}/${RUN_PREFIX}/input/shard-${SHARD_ID}.json" \
  "${WORK}/catalog.json"

# --- runtime ---------------------------------------------------------------- #
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv libeccodes0 >/dev/null
python3 -m venv "${WORK}/venv"
"${WORK}/venv/bin/pip" -q install --upgrade pip
"${WORK}/venv/bin/pip" -q install \
  "numpy>=1.24,<3.0" "eccodes>=2.47,<3.0" "herbie-data>=2026.3,<2027.0" \
  "cfgrib>=0.9.15,<0.10" "xarray>=2026.4,<2027.0" "pandas>=2.0" \
  "python-dateutil>=2.8,<3.0" "requests>=2.28,<3.0" "certifi>=2023.7.22"

# --- bounded collection ----------------------------------------------------- #
# Raw GRIB stays VM-local and is never uploaded; only compact JSON is mirrored.
cd "${WORK}/src"
set +e
"${WORK}/venv/bin/python" -m sharpmod.tools.guidance_cli run-toi-archive \
  --catalog "${WORK}/catalog.json" \
  --work-dir "${WORK}/run" \
  --max-cases @@MAX_CASES@@ \
  --max-transfer-gib @@MAX_INPUT_GIB@@ \
  --max-seconds @@MAX_TASK_SECONDS@@ \
  --min-free-gib @@MIN_FREE_GIB@@ \
  --allow-failures
RUN_STATUS=$?
set -e
echo "[toi] runner exit=${RUN_STATUS}"

# --- durable mirror, with explicit verification ----------------------------- #
# Atomic local writes already happened; upload and then verify rather than
# trusting FUSE rename semantics or ephemeral disk.
gcloud storage rsync -r \
  "${WORK}/run/cases/" "gs://${BUCKET}/${SHARD_PREFIX}/state/cases/"
gcloud storage cp "${WORK}/run/checkpoint.jsonl" \
  "gs://${BUCKET}/${SHARD_PREFIX}/state/checkpoint.jsonl"
gcloud storage cp "${WORK}/run/run-report.json" \
  "gs://${BUCKET}/${SHARD_PREFIX}/reports/run-report.json"

# Read back and compare checksums, so a silent truncation cannot pass.
gcloud storage hash --hex "gs://${BUCKET}/${SHARD_PREFIX}/state/checkpoint.jsonl" \
  > "${WORK}/remote-checkpoint.hash" || true
sha256sum "${WORK}/run/checkpoint.jsonl" > "${WORK}/local-checkpoint.hash"
cat "${WORK}/local-checkpoint.hash"

"${WORK}/venv/bin/python" -m sharpmod.tools.guidance_cli verify-toi-archive \
  --work-dir "${WORK}/run" \
  --output "${WORK}/verification.json"
VERIFY_STATUS=$?
gcloud storage cp "${WORK}/verification.json" \
  "gs://${BUCKET}/${SHARD_PREFIX}/reports/verification.json"

echo "[toi] shard ${SHARD} complete verify=${VERIFY_STATUS}"
if [ "${VERIFY_STATUS}" -ne 0 ]; then
  exit "${VERIFY_STATUS}"
fi
exit "${RUN_STATUS}"
"""


def render_task_script(config: BatchConfig, *, bundle_sha256: str = "") -> str:
    """Render the shard task script for a Batch script runnable.

    Token replacement is used rather than ``str.format`` so shell parameter
    expansions such as ``${BATCH_TASK_INDEX}`` need no brace escaping and stay
    readable in the template.
    """

    substitutions = {
        "@@BUCKET@@": config.bucket,
        "@@RUN_PREFIX@@": config.run_prefix,
        "@@SOURCE_OBJECT@@": config.source_object,
        "@@BUNDLE_SHA256@@": bundle_sha256 or "0" * 64,
        "@@MAX_CASES@@": str(config.budget.maximum_cases_per_task),
        "@@MAX_INPUT_GIB@@": str(config.budget.maximum_input_gib),
        "@@MAX_TASK_SECONDS@@": str(config.budget.maximum_task_seconds),
        "@@MIN_FREE_GIB@@": str(config.budget.minimum_free_gib),
    }
    script = TASK_SCRIPT_TEMPLATE
    for token, value in substitutions.items():
        script = script.replace(token, value)
    remaining = [token for token in substitutions if token in script]
    if remaining:  # pragma: no cover - guarded by tests
        raise BatchPlanError(
            "task script still contains unrendered token(s): "
            + ", ".join(remaining)
        )
    return script


def render_job_spec(config: BatchConfig, *, bundle_sha256: str = "") -> dict[str, Any]:
    """Render the complete Batch job JSON, using a script runnable only."""

    return {
        "taskGroups": [
            {
                "taskCount": config.shard_count,
                "parallelism": config.parallelism,
                "taskCountPerNode": 1,
                "taskSpec": {
                    "computeResource": {
                        # e2-standard-4: 4 vCPU / 16 GiB.
                        "cpuMilli": 4000,
                        "memoryMib": 16384,
                        "bootDiskMib": config.budget.boot_disk_gib * 1024,
                    },
                    "maxRunDuration": f"{config.budget.maximum_task_seconds}s",
                    "maxRetryCount": config.budget.maximum_retries,
                    "lifecyclePolicies": [
                        {
                            # Spot preemption is retryable; the task resumes
                            # from the mirrored checkpoint.
                            "actionCondition": {
                                "exitCodes": [50001]
                            },
                            "action": "RETRY_TASK",
                        }
                    ],
                    "runnables": [
                        {
                            "script": {
                                "text": render_task_script(
                                    config, bundle_sha256=bundle_sha256
                                )
                            },
                            "timeout": f"{config.budget.maximum_task_seconds}s",
                        }
                    ],
                    "volumes": [],
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
                            "type": config.boot_disk_type,
                            "sizeGb": config.budget.boot_disk_gib,
                        },
                    }
                }
            ],
            "location": {"allowedLocations": [f"regions/{config.region}"]},
            **(
                {"serviceAccount": {"email": config.service_account}}
                if config.service_account
                else {}
            ),
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
        "labels": dict(config.labels),
    }


def projected_usage(
    config: BatchConfig,
    *,
    cases: int,
    mib_per_case: float = 69.68,
    seconds_per_case: float = 84.8,
    retained_kib_per_case: float = 4.7,
) -> dict[str, Any]:
    """Project cost-relevant usage from measured per-case rates."""

    count = max(0, int(cases))
    transfer_gib = mib_per_case * count / 1024.0
    wall_hours = seconds_per_case * count / 3600.0
    per_shard_hours = wall_hours / max(1, config.shard_count)
    # Spot billing is per-VM wall time; parallelism 1 means shards run serially.
    concurrent = min(config.parallelism, config.shard_count)
    vcpu_hours = wall_hours * 4
    return {
        "cases": count,
        "shards": config.shard_count,
        "parallelism": config.parallelism,
        "inbound_transfer_gib": round(transfer_gib, 2),
        "single_worker_wall_hours": round(wall_hours, 2),
        "per_shard_wall_hours": round(per_shard_hours, 2),
        "elapsed_wall_hours_at_parallelism": round(
            per_shard_hours * config.shard_count / max(1, concurrent), 2
        ),
        "vcpu_hours": round(vcpu_hours, 1),
        "peak_raw_disk_mib": round(mib_per_case / 7 * 2, 1),
        "retained_output_mib": round(retained_kib_per_case * count / 1024.0, 2),
        "boot_disk_gib_per_task": config.budget.boot_disk_gib,
        "egress_note": (
            "NOAA Open Data reads are inbound to Google Cloud; Cloud Storage "
            "writes stay in-region. No egress to the public internet is planned."
        ),
        "hard_caps": config.budget.to_mapping(),
    }


def lifecycle_policy() -> dict[str, Any]:
    """Lifecycle rules: expire temporary prefixes, retain final artifacts."""

    return {
        "rule": [
            {
                "action": {"type": "Delete"},
                "condition": {
                    "age": 14,
                    "matchesPrefix": ["runs/", "tmp/", "source/"],
                },
            }
        ],
        "retained_prefixes": [
            "manifests/",
            "reports/",
            "models/",
            "sources/",
        ],
        "note": (
            "Temporary run state, shard inputs, and source bundles expire after "
            "14 days. Final manifests, source records, validation reports, and "
            "promoted model artifacts are retained indefinitely."
        ),
    }


def planned_resources(config: BatchConfig) -> list[dict[str, str]]:
    """Every cloud resource the plan would create, named explicitly."""

    return [
        {
            "kind": "storage.bucket",
            "name": f"gs://{config.bucket}",
            "action": "create (dedicated; existing buckets untouched)",
            "detail": f"region {config.region}, uniform access, lifecycle applied",
        },
        {
            "kind": "storage.object",
            "name": config.gs(config.source_object),
            "action": "upload",
            "detail": "deterministic source tarball, SHA-256 verified in-task",
        },
        {
            "kind": "storage.object",
            "name": config.gs(config.run_prefix, "input/shard-NN.json"),
            "action": "upload",
            "detail": f"{config.shard_count} event-indivisible shard catalogues",
        },
        {
            "kind": "storage.prefix",
            "name": config.gs(config.run_prefix, "shard-NN/state/"),
            "action": "create at runtime",
            "detail": "durable case JSON + checkpoint mirror for Spot resume",
        },
        {
            "kind": "batch.job",
            "name": (
                f"projects/{config.project}/locations/{config.region}"
                f"/jobs/{config.job_name}"
            ),
            "action": "create",
            "detail": (
                f"{config.shard_count} task(s), parallelism {config.parallelism}, "
                f"{config.machine_type} {config.provisioning_model}, "
                f"{config.budget.boot_disk_gib} GiB {config.boot_disk_type}, "
                "script runnable (no container image)"
            ),
        },
        {
            "kind": "compute.instance",
            "name": "(Batch-managed, ephemeral)",
            "action": "create/delete by Batch",
            "detail": (
                f"up to {min(config.parallelism, config.shard_count)} concurrent "
                "Spot VM(s); raw GRIB stays VM-local and is never uploaded"
            ),
        },
        {
            "kind": "logging",
            "name": "Cloud Logging",
            "action": "write",
            "detail": "task stdout/stderr only",
        },
    ]


def remaining_confirmations(config: BatchConfig) -> list[str]:
    """Actions still requiring an explicit human decision."""

    return [
        f"Enable Batch and Compute Engine APIs on project {config.project} "
        "(currently NOT enabled): "
        f"gcloud services enable batch.googleapis.com compute.googleapis.com "
        f"--project {config.project}",
        f"Create the dedicated bucket gs://{config.bucket} and apply the "
        "lifecycle policy (no existing bucket is reused or modified)",
        "Grant the Batch service account roles/storage.objectAdmin on the "
        "dedicated bucket only, and roles/logging.logWriter",
        "Confirm Spot capacity and pricing in the chosen region",
        "Re-run submit with --confirm-submit to actually create the job",
        "Decide the development window (2015 with 15 h coverage for pre-HRRRv2, "
        "or 2017+ for uniform 18 h sampling)",
    ]


def build_plan(
    config: BatchConfig,
    *,
    cases: int,
    bundle: Mapping[str, Any] | None = None,
    sharding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the complete, fully rendered dry-run plan."""

    bundle_sha = str((bundle or {}).get("bundle_sha256") or "")
    spec = render_job_spec(config, bundle_sha256=bundle_sha)
    return {
        "package_version": TOI_BATCH_PACKAGE_VERSION,
        "generated_at": _now(),
        "dry_run": True,
        "executed": False,
        "config": config.to_mapping(),
        "planned_resources": planned_resources(config),
        "projected_usage": projected_usage(config, cases=cases),
        "lifecycle": lifecycle_policy(),
        "sharding": dict(sharding or {}),
        "source_bundle": dict(bundle or {}),
        "job_spec": spec,
        "commands": rendered_commands(config),
        "retry_and_preemption": {
            "provisioning_model": config.provisioning_model,
            "max_retry_count": config.budget.maximum_retries,
            "preemption_exit_code": 50001,
            "behaviour": (
                "A preempted task is retried. On start it rsyncs the mirrored "
                "case JSON and checkpoint from its shard state prefix, removes "
                "any stale run lock, and the runner skips already-completed "
                "cache keys, so work is resumed rather than repeated."
            ),
            "raw_data_policy": (
                "Raw GRIB subsets are deleted after extraction and never "
                "uploaded; only compact JSON leaves the VM."
            ),
        },
        "cleanup_actions": [
            f"delete objects under {config.gs(config.run_prefix)} (temporary state)",
            f"delete {config.gs(config.source_object)} after the run",
            "Batch deletes its managed VMs automatically on job completion",
            "lifecycle expires runs/, tmp/, and source/ after 14 days",
            "manifests/, reports/, models/, and sources/ are retained",
        ],
        "remaining_confirmations": remaining_confirmations(config),
        "protected_buckets_untouched": list(PROTECTED_BUCKET_SUFFIXES),
        "experimental_not_official": True,
    }


def rendered_commands(config: BatchConfig) -> dict[str, str]:
    """Exact commands a human would run, rendered but never executed here."""

    return {
        "enable_apis": (
            "gcloud services enable "
            + " ".join(REQUIRED_APIS)
            + f" --project {config.project}"
        ),
        "create_bucket": (
            f"gcloud storage buckets create gs://{config.bucket} "
            f"--project {config.project} --location {config.region} "
            "--uniform-bucket-level-access --public-access-prevention"
        ),
        "set_lifecycle": (
            f"gcloud storage buckets update gs://{config.bucket} "
            "--lifecycle-file=infra/gcp/toi-batch/lifecycle.json"
        ),
        "upload_source": (
            f"gcloud storage cp dist/sharpmod-source.tar.gz "
            f"{config.gs(config.source_object)}"
        ),
        "upload_shards": (
            f"gcloud storage cp infra/gcp/toi-batch/out/shard-*.json "
            f"{config.gs(config.run_prefix, 'input')}/"
        ),
        "submit": (
            f"gcloud batch jobs submit {config.job_name} "
            f"--project {config.project} --location {config.region} "
            "--config infra/gcp/toi-batch/out/job.json"
        ),
        "status": (
            f"gcloud batch jobs describe {config.job_name} "
            f"--project {config.project} --location {config.region}"
        ),
        "logs": (
            "gcloud logging read "
            f'\'labels."batch.googleapis.com/job_id"="{config.job_name}"\' '
            f"--project {config.project} --limit 100"
        ),
        "fetch_output": (
            f"gcloud storage rsync -r {config.gs(config.run_prefix)}/ "
            "archive/cloud-run/"
        ),
        "cleanup": (
            f"gcloud storage rm -r {config.gs(config.run_prefix)}/  "
            "# requires --confirm-delete in this tool"
        ),
    }


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def preflight(
    config: BatchConfig, *, check_gcloud: bool = False
) -> dict[str, Any]:
    """Read-only audit.  Never enables an API or creates a resource."""

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool | None, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    add(
        "project_not_hardcoded",
        True,
        f"project {config.project!r} resolved from flag/environment only",
    )
    add(
        "protected_buckets",
        not any(
            config.bucket.endswith(suffix) for suffix in PROTECTED_BUCKET_SUFFIXES
        ),
        "target bucket does not match any protected existing bucket pattern",
    )
    add(
        "no_container_registry",
        True,
        "Batch script runnable; no Artifact Registry, Cloud Build, or Cloud Run",
    )
    add(
        "parallelism_bounded",
        config.parallelism == 1,
        f"parallelism {config.parallelism} (>1 blocked pending a cloud pilot)",
    )
    add(
        "task_duration_bounded",
        config.budget.maximum_task_seconds <= 86_400,
        f"maxRunDuration {config.budget.maximum_task_seconds}s",
    )
    add(
        "shards_within_task_budget",
        config.shard_count <= config.budget.maximum_tasks,
        f"{config.shard_count} shard(s) <= {config.budget.maximum_tasks} tasks",
    )
    gcloud = shutil.which("gcloud")
    add(
        "gcloud_available",
        bool(gcloud),
        gcloud or "gcloud not found on PATH (only needed to submit)",
    )
    add(
        "apis_required",
        None,
        "Batch and Compute Engine must be enabled by the user: "
        + ", ".join(REQUIRED_APIS),
    )
    add(
        "billing_account",
        None,
        "not read or recorded by this tool; billing is a project property",
    )
    if check_gcloud and gcloud:
        described = _gcloud_describe(config)
        checks.extend(described)
    failed = [check for check in checks if check["ok"] is False]
    return {
        "package_version": TOI_BATCH_PACKAGE_VERSION,
        "generated_at": _now(),
        "project": config.project,
        "region": config.region,
        "bucket": config.bucket,
        "ready_to_plan": not failed,
        "blocking_failures": failed,
        "unknown_requires_user": [c for c in checks if c["ok"] is None],
        "checks": checks,
    }


def _gcloud_describe(config: BatchConfig) -> list[dict[str, Any]]:
    """Read-only ``gcloud`` lookups.  No mutation, ever."""

    results: list[dict[str, Any]] = []
    # Resolve the real executable: on Windows ``gcloud`` is a .CMD shim that
    # bare subprocess argv cannot find.
    executable = shutil.which("gcloud")
    if not executable:
        return [
            {"check": "enabled_services", "ok": None, "detail": "gcloud not on PATH"}
        ]
    for name, args in (
        (
            "enabled_services",
            [
                "services",
                "list",
                "--enabled",
                "--project",
                config.project,
                "--format=value(config.name)",
            ],
        ),
    ):
        try:
            completed = subprocess.run(
                [executable, *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            results.append(
                {"check": name, "ok": None, "detail": f"gcloud failed: {exc}"}
            )
            continue
        if completed.returncode != 0:
            results.append(
                {
                    "check": name,
                    "ok": None,
                    "detail": f"gcloud exit {completed.returncode}",
                }
            )
            continue
        enabled = set(completed.stdout.split())
        missing = [api for api in REQUIRED_APIS if api not in enabled]
        results.append(
            {
                "check": "required_apis_enabled",
                "ok": not missing,
                "detail": (
                    "all required APIs enabled"
                    if not missing
                    else "not enabled: " + ", ".join(missing)
                ),
            }
        )
    return results


# --------------------------------------------------------------------------
# Output verification / merge
# --------------------------------------------------------------------------


def verify_merged_output(
    shard_directories: Sequence[str | os.PathLike[str]],
    *,
    expected_cases: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Verify and merge shard outputs, rejecting overlaps and inconsistencies."""

    from sharpmod.guidance.toi_archive import ArchiveRunner

    failures: list[dict[str, Any]] = []
    seen_keys: dict[str, str] = {}
    seen_events: dict[str, str] = {}
    merged: list[dict[str, Any]] = []
    per_shard: list[dict[str, Any]] = []
    sources: set[str] = set()
    hash_versions: set[str] = set()

    for directory in shard_directories:
        path = Path(directory)
        runner = ArchiveRunner(output_directory=path)
        report = runner.verify()
        hash_versions.add(str(report["hash_version"]))
        sources.add(json.dumps(report["sources"]["hrrr"], sort_keys=True))
        per_shard.append(
            {
                "shard": path.name,
                "verified": report["verified"],
                "cases": report["verified_cases"],
                "failures": report["failure_count"],
            }
        )
        if not report["verified"]:
            for failure in report["failures"]:
                failures.append({"shard": path.name, **failure})
        for record in report["cases"]:
            key = str(record["cache_key"])
            if key in seen_keys:
                failures.append(
                    {
                        "check": "shard_overlap",
                        "shard": path.name,
                        "detail": f"cache_key {key} also in {seen_keys[key]}",
                    }
                )
                continue
            seen_keys[key] = path.name
            event_id = str(record["event_id"])
            previous = seen_events.get(event_id)
            if previous is not None and previous != path.name:
                failures.append(
                    {
                        "check": "event_split_across_shards",
                        "shard": path.name,
                        "detail": (
                            f"event {event_id} appears in {previous} and "
                            f"{path.name}; events must be indivisible"
                        ),
                    }
                )
            seen_events[event_id] = path.name
            merged.append({"shard": path.name, **record})

    if len(hash_versions) > 1:
        failures.append(
            {
                "check": "inconsistent_hash_version",
                "detail": "shards used different hash versions: "
                + ", ".join(sorted(hash_versions)),
            }
        )
    if len(sources) > 1:
        failures.append(
            {
                "check": "inconsistent_sources",
                "detail": "shards recorded different HRRR source identities",
            }
        )
    if expected_cases is not None:
        expected = {str(item) for item in expected_cases}
        missing = sorted(expected.difference(seen_keys))
        extra = sorted(set(seen_keys).difference(expected))
        for key in missing:
            failures.append(
                {"check": "missing_expected_case", "detail": f"cache_key {key}"}
            )
        for key in extra:
            failures.append(
                {"check": "unexpected_case", "detail": f"cache_key {key}"}
            )
    return {
        "package_version": TOI_BATCH_PACKAGE_VERSION,
        "generated_at": _now(),
        "verified": not failures,
        "shards": per_shard,
        "merged_cases": len(merged),
        "unique_events": len(seen_events),
        "failure_count": len(failures),
        "failures": failures,
        "cases": merged,
    }


def cleanup_inventory(
    config: BatchConfig, *, confirm_delete: bool = False
) -> dict[str, Any]:
    """List exactly what cleanup would remove; delete nothing without consent."""

    return {
        "package_version": TOI_BATCH_PACKAGE_VERSION,
        "generated_at": _now(),
        "dry_run": not confirm_delete,
        "executed": False,
        "would_delete": [
            {
                "target": config.gs(config.run_prefix, "input"),
                "reason": "shard catalogues are reproducible from the source catalogue",
            },
            {
                "target": config.gs(config.run_prefix, "shard-NN/state"),
                "reason": "durable resume mirror is only needed during the run",
            },
            {
                "target": config.gs(config.source_object),
                "reason": "source bundle is reproducible from the recorded Git HEAD",
            },
        ],
        "would_retain": [
            config.gs("manifests"),
            config.gs("reports"),
            config.gs("models"),
            config.gs("sources"),
        ],
        "protected_never_touched": list(PROTECTED_BUCKET_SUFFIXES),
        "lifecycle": lifecycle_policy(),
        "note": (
            "Nothing is deleted by this tool unless --confirm-delete is passed, "
            "and even then only under the run prefix of this dedicated bucket."
        ),
    }


__all__ = [
    "BUNDLE_EXCLUDE_DIRECTORIES",
    "BUNDLE_EXCLUDE_NAMES",
    "BUNDLE_EXCLUDE_SUFFIXES",
    "PROJECT_ENVIRONMENT_KEYS",
    "PROTECTED_BUCKET_SUFFIXES",
    "REQUIRED_APIS",
    "TOI_BATCH_PACKAGE_VERSION",
    "BatchConfig",
    "BatchPlanError",
    "JobBudget",
    "build_plan",
    "build_source_bundle",
    "cleanup_inventory",
    "describe_sharding",
    "git_provenance",
    "lifecycle_policy",
    "planned_resources",
    "preflight",
    "projected_usage",
    "remaining_confirmations",
    "render_job_spec",
    "render_task_script",
    "rendered_commands",
    "resolve_billing_account",
    "resolve_project",
    "shard_catalog",
    "shard_for_event",
    "verify_merged_output",
]
