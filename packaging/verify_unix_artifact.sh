#!/usr/bin/env bash
# Exercise installed Tauri discovery and both Qt render paths without a source venv.
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: $0 APP BACKEND VERSION SOUNDING REPORT_DIRECTORY" >&2
  exit 2
fi

app=$1
backend=$2
version=$3
sample=$4
report_directory=$5
test -x "$app"
test -x "$backend"
test -r "$sample"
mkdir -p "$report_directory"

# Run from an empty application data directory, away from source and toolchain.
unset SHARPPY_PYTHON SHARPMOD_CACHE_DIR PYTHONPATH PYTHONHOME
export SHARPMOD_BACKEND=rust
export XDG_CONFIG_HOME="$report_directory/config"
export XDG_DATA_HOME="$report_directory/data"
export XDG_CACHE_HOME="$report_directory/cache"
cd "$report_directory"

"$backend" --model-fetch-runtime-check "$report_directory/model-fetch-runtime.json" ||
  { cat "$report_directory/model-fetch-runtime.json" >&2; exit 1; }
jq -e --arg version "$version" '
  .ok and .frozen and .version_consistent and .backend_kernel_ok and .wrf_runtime_ok
  and .backend_entrypoint and .logging_handlers and (.configured_models > 0)
  and (.backend.active_backend == "rust")
  and (.versions.sharpmod == $version and .versions.sharpmod_rs == $version)
  and (.eccodes | length > 0)
' model-fetch-runtime.json

if [[ $(uname -s) == Linux ]]; then
  xvfb-run -a dbus-run-session -- "$app" --desktop-runtime-check "$report_directory/desktop-runtime.json" "$sample"
else
  "$app" --desktop-runtime-check "$report_directory/desktop-runtime.json" "$sample"
fi || { cat "$report_directory/desktop-runtime.json" >&2; exit 1; }

jq -e --arg version "$version" '
  .ok and (.station_count > 0) and (.sounding.levelCount >= 2)
  and (.health.app.version == $version and .health.python.sharpmodVersion == $version)
  and (.health.scientificBackend.activeBackend == "rust")
  and (.health.scientificBackend.rustVersion == $version)
  and ([.views.severe_hd, .views.winter_hd, .views.winter_uhd]
    | all(.image_bytes > 24 and .width > 0 and .height > 0))
  and (.views.winter_hd.width == .views.severe_hd.width)
  and (.views.winter_hd.height == .views.severe_hd.height)
  and (.views.winter_uhd.width > .views.winter_hd.width)
  and (.views.winter_uhd.height > .views.winter_hd.height)
' desktop-runtime.json
