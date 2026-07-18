"""Synchronize the local Rust extension with this source checkout."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import os
from pathlib import Path
import subprocess
import sys

from sharpmod._version import __version__


RUST_DISTRIBUTION = "sharpmod-rs"
_VERIFY_CODE = """
import json
from sharpmod.backends import backend_info

print(json.dumps(backend_info(), sort_keys=True))
""".strip()


class RustBackendSyncError(RuntimeError):
    """Raised when the extension cannot be rebuilt or verified safely."""


def installed_rust_version() -> str | None:
    """Return the installed native distribution version, if present."""
    try:
        return package_version(RUST_DISTRIBUTION)
    except PackageNotFoundError:
        return None


def rust_crate_root() -> Path:
    """Return the repository Rust crate required for a local rebuild."""
    root = Path(__file__).resolve().parents[2] / "rust" / "sharpmod-rs"
    if not (root / "Cargo.toml").is_file():
        raise RustBackendSyncError(
            "the Rust crate is unavailable; run this command from an editable "
            "SHARPpy Reimagined source checkout"
        )
    return root


def _active_environment_root() -> Path:
    """Return the active virtual environment or reject a global install."""
    environment_root = Path(sys.prefix).resolve()
    base_prefix = Path(getattr(sys, "base_prefix", sys.prefix)).resolve()
    if environment_root != base_prefix:
        return environment_root

    # Conda environments can intentionally report the same sys.prefix and
    # sys.base_prefix. Accept the environment only when its explicit Conda
    # marker resolves to the interpreter prefix, avoiding a stale marker from
    # redirecting the native install into another environment.
    conda_prefix = os.environ.get("CONDA_PREFIX", "").strip()
    if conda_prefix and Path(conda_prefix).resolve() == environment_root:
        return environment_root

    raise RustBackendSyncError(
        "a virtual or Conda environment must be active before rebuilding "
        "sharpmod_rs"
    )


def rebuild_rust_backend() -> None:
    """Build and install the locked release extension into this environment."""
    environment_root = _active_environment_root()
    crate_root = rust_crate_root()
    command = [
        sys.executable,
        "-m",
        "maturin",
        "develop",
        "--release",
        "--locked",
    ]
    environment = os.environ.copy()
    # Calling the environment's Python executable directly does not make
    # maturin recognize that environment. Identify it explicitly so the wheel
    # cannot be installed into a different interpreter by accident.
    environment["VIRTUAL_ENV"] = str(environment_root)
    try:
        subprocess.run(
            command,
            cwd=crate_root,
            env=environment,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RustBackendSyncError(
            f"could not start the Rust rebuild: {exc}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RustBackendSyncError(
            "maturin rebuild failed with exit code "
            f"{exc.returncode}; install the [rust-build] extra and Rust 1.86+"
        ) from exc


def verify_rust_backend() -> dict:
    """Require matching Rust selection in a fresh Python process."""
    environment = os.environ.copy()
    environment["SHARPMOD_BACKEND"] = "rust"
    command = [sys.executable, "-c", _VERIFY_CODE]
    try:
        result = subprocess.run(
            command,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RustBackendSyncError(
            f"forced-Rust verification process failed{suffix}"
        ) from exc

    output_lines = [
        line.strip() for line in result.stdout.splitlines() if line.strip()
    ]
    if not output_lines:
        raise RustBackendSyncError(
            "forced-Rust verification produced no backend report"
        )
    try:
        info = json.loads(output_lines[-1])
    except json.JSONDecodeError as exc:
        raise RustBackendSyncError(
            "forced-Rust verification produced an invalid backend report: "
            f"{output_lines[-1]!r}"
        ) from exc

    expected = {
        "requested_backend": "rust",
        "active_backend": "rust",
        "rust_installed": True,
        "rust_version": __version__,
        "fallback_reason": None,
    }
    for field, expected_value in expected.items():
        actual_value = info.get(field)
        if actual_value != expected_value:
            raise RustBackendSyncError(
                "forced-Rust verification failed: "
                f"{field}={actual_value!r}, expected {expected_value!r}"
            )
    return info


def _version_problem(installed: str | None) -> str | None:
    if installed is None:
        return "sharpmod_rs is not installed"
    if installed != __version__:
        return (
            f"installed sharpmod_rs {installed} does not match "
            f"expected {__version__}"
        )
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sharpmod-rust-sync",
        description=(
            "Check, rebuild when needed, and verify the local sharpmod_rs "
            "extension."
        ),
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check",
        action="store_true",
        help="verify the installed version and backend without rebuilding",
    )
    modes.add_argument(
        "--force",
        action="store_true",
        help="rebuild even when the installed extension version matches",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Synchronize the extension and return a process-style status code."""
    args = _build_parser().parse_args(argv)
    installed = installed_rust_version()
    version_problem = _version_problem(installed)

    if args.check and version_problem is not None:
        print(
            f"[sharpmod-rust-sync] ERROR: {version_problem}",
            file=sys.stderr,
        )
        return 1

    try:
        if args.force:
            print(
                "[sharpmod-rust-sync] forcing a locked release rebuild of "
                f"sharpmod_rs {__version__}",
                flush=True,
            )
            rebuild_rust_backend()
        elif version_problem is not None:
            print(
                f"[sharpmod-rust-sync] {version_problem}; rebuilding",
                flush=True,
            )
            rebuild_rust_backend()
        else:
            print(
                f"[sharpmod-rust-sync] sharpmod_rs {__version__} already "
                "matches; verifying"
            )

        info = verify_rust_backend()
    except RustBackendSyncError as exc:
        print(f"[sharpmod-rust-sync] ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        "[sharpmod-rust-sync] active backend: "
        f"{info['active_backend']} {info['rust_version']} (forced verification)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
