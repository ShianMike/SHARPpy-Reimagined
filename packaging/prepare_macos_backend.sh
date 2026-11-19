#!/usr/bin/env bash
# Sign the generated PyInstaller tree before Tauri copies it into Resources.
# Set APPLE_SIGNING_IDENTITY for this step and the Tauri build. A Developer ID
# identity and its private key must already be available in the keychain.
set -euo pipefail
export LC_ALL=C

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

case "${1:-}" in
  aarch64-apple-darwin) architecture=arm64 ;;
  x86_64-apple-darwin) architecture=x86_64 ;;
  *) fail "Usage: bash packaging/prepare_macos_backend.sh <aarch64-apple-darwin|x86_64-apple-darwin>" ;;
esac
[[ $# -eq 1 && $(uname -s) == Darwin ]] || fail "Run this step on macOS with one Rust target."

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
backend_dir="$repo_root/dist/sharppy-backend"
backend_executable="$backend_dir/sharppy-backend"
[[ -d "$backend_dir/_internal" && -f "$backend_executable" && -x "$backend_executable" &&
   ! -L "$backend_executable" ]] || fail "Build the one-folder PyInstaller backend in $backend_dir first."
[[ $(cd "$backend_dir" && pwd -P) == "$backend_dir" ]] || fail "The backend directory must be inside the checkout."
lipo "$backend_executable" -verify_arch "$architecture" || fail "Wrong backend architecture for $1."

# The copied tree must resolve its own framework and @rpath links after relocation.
find "$backend_dir" -type l -print0 | while IFS= read -r -d '' link; do
  target=$(readlink "$link")
  [[ "$target" != /* && -e "$link" ]] || fail "Expected a relative, resolvable backend symlink: $link"
  [[ $(realpath "$link") == "$backend_dir/"* ]] || fail "Backend symlink escapes the bundle: $link"
done

signing_identity=${APPLE_SIGNING_IDENTITY:--}
sign_args=(--force --sign "$signing_identity")
if [[ "$signing_identity" == - ]]; then
  [[ -z ${APPLE_CERTIFICATE:-} ]] || fail "Import the certificate and set APPLE_SIGNING_IDENTITY before signing."
  sign_args+=(--timestamp=none)
else
  sign_args+=(--timestamp)
fi

sign_code() {
  if [[ "$signing_identity" != - && "$2" == *executable* ]]; then
    codesign "${sign_args[@]}" --options runtime "$1"
  else
    codesign "${sign_args[@]}" "$1"
  fi
}

# Sign real files and nested bundles from the inside out. Tauri signs the outer
# app after copying the signed payload.
find "$backend_dir" -depth \
  \( -type f -o \( -type d \( -name '*.framework' -o -name '*.app' -o -name '*.xpc' \) \) \) \
  ! -path "$backend_executable" -print0 |
  while IFS= read -r -d '' path; do
    if [[ -d "$path" ]]; then
      case "$path" in
        *.framework) sign_code "$path" framework ;;
        *) sign_code "$path" executable ;;
      esac
    else
      kind=$(file -b "$path")
      if [[ "$kind" == Mach-O* ]]; then
        lipo "$path" -verify_arch "$architecture" || fail "Missing $architecture slice: $path"
        sign_code "$path" "$kind"
      fi
    fi
  done
sign_code "$backend_executable" executable
