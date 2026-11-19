#!/usr/bin/env bash
# Assemble the Tauri launcher and the frozen backend into one Debian package.
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "Usage: $0 TAURI_DEB OUTPUT_DEB" >&2
  exit 2
fi

repo=$(cd "$(dirname "$0")/.." && pwd -P)
backend="$repo/dist/sharppy-backend"
test -x "$backend/sharppy-backend"
test -d "$backend/_internal"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT

dpkg-deb --raw-extract "$1" "$stage"
test -x "$stage/usr/bin/sharppy-reimagined"
resources="$stage/usr/lib/SHARPpy Reimagined"
mkdir -p "$resources"
test ! -e "$resources/backend"
# The Tauri .deb archiver dereferences PyInstaller's library symlinks.
cp -a -- "$backend" "$resources/backend"

size=$(du -sk "$stage/usr" | cut -f1)
sed -i "s/^Installed-Size:.*/Installed-Size: $size/" "$stage/DEBIAN/control"
(
  cd "$stage"
  find usr -type f -print0 | LC_ALL=C sort -z | xargs -0r md5sum > DEBIAN/md5sums
)
dpkg-deb --root-owner-group -Zxz -z3 --threads-max=2 --build "$stage" "$2"
