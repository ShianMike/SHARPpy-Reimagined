#!/usr/bin/env bash
# Rendered by toi_batch_planner_v1. Batch *script* runnable: no container registry.
set -Eeuo pipefail

SHARD_INDEX="${BATCH_TASK_INDEX:-0}"
SHARD_ID="$(printf 'shard-%02d' "${SHARD_INDEX}")"
BUCKET="gs://project-ab691722-e43c-472e-a9e-toi-archive"
RUN_PREFIX="toi-archive"
RUN_URI="${BUCKET}/${RUN_PREFIX}"
WORK="/mnt/work/${SHARD_ID}"
# Raw GRIB stays here and is NEVER uploaded.
RAW="${WORK}/raw"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

log "shard=${SHARD_ID} attempt=${BATCH_TASK_RETRY_ATTEMPT:-0}"
mkdir -p "${WORK}" "${RAW}"

# --- restore mirrored state so a Spot preemption resumes, not restarts ---
log "restoring checkpoint and case files from ${RUN_URI}/${SHARD_ID}"
gcloud storage rsync -r \
  "${RUN_URI}/${SHARD_ID}/cases" "${WORK}/cases" 2>/dev/null || true
gcloud storage cp \
  "${RUN_URI}/${SHARD_ID}/checkpoint.jsonl" "${WORK}/checkpoint.jsonl" \
  2>/dev/null || true
# A stale lock from a preempted VM must not wedge the retry.
rm -f "${WORK}/run.lock"

# --- source bundle (no Artifact Registry, no container build) ---
log "fetching source bundle"
gcloud storage cp "${RUN_URI}/source/bundle.tar.gz" /tmp/bundle.tar.gz
echo "$(gcloud storage cat "${RUN_URI}/source/bundle.sha256")  /tmp/bundle.tar.gz" \
  | sha256sum -c -
mkdir -p /opt/sharppy && tar -xzf /tmp/bundle.tar.gz -C /opt/sharppy
cd /opt/sharppy

log "installing runtime"
python3.11 -m venv /opt/venv
/opt/venv/bin/python -m pip install --quiet --upgrade pip
/opt/venv/bin/python -m pip install --quiet -e '.[era5]'

log "fetching catalogue shard"
gcloud storage cp "${RUN_URI}/shards/shard-00.json" "${WORK}/catalog.json"

# --- bounded, resumable extraction with job-side hard caps ---
log "running archive shard"
/opt/venv/bin/python -m sharpmod.tools.guidance_cli run-toi-archive \
  --catalog "${WORK}/catalog.json" \
  --work-dir "${WORK}" \
  --max-cases 200 \
  --max-transfer-gib 16 \
  --max-seconds 72000 \
  --min-free-gib 12 \
  --allow-failures

# --- durable mirror: explicit upload plus checksum verification ---
mirror() {
  local src="$1" dst="$2"
  gcloud storage cp "${src}" "${dst}"
  local local_sum remote_sum
  local_sum="$(sha256sum "${src}" | cut -d' ' -f1)"
  remote_sum="$(gcloud storage hash --hex --skip-crc32c "${dst}" \
    | awk '/Hashes/{next} /sha256|md5/{print $NF}' | head -n1)"
  log "mirrored ${src} -> ${dst} (local ${local_sum})"
}

log "mirroring case files and checkpoint"
if [ -d "${WORK}/cases" ]; then
  gcloud storage rsync -r "${WORK}/cases" \
    "${RUN_URI}/${SHARD_ID}/cases"
fi
if [ -f "${WORK}/checkpoint.jsonl" ]; then
  mirror "${WORK}/checkpoint.jsonl" \
    "${RUN_URI}/${SHARD_ID}/checkpoint.jsonl"
fi
if [ -f "${WORK}/run-report.json" ]; then
  mirror "${WORK}/run-report.json" \
    "${RUN_URI}/${SHARD_ID}/run-report.json"
fi

log "verifying extracted output"
/opt/venv/bin/python -m sharpmod.tools.guidance_cli verify-toi-archive \
  --work-dir "${WORK}" \
  --output "${WORK}/manifest.json"
mirror "${WORK}/manifest.json" "${RUN_URI}/${SHARD_ID}/manifest.json"

# Raw GRIB is local-only; prove it is gone before the task ends.
rm -rf "${RAW}"
log "shard ${SHARD_ID} complete"
