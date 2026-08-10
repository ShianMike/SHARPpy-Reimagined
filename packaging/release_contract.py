"""Build-time version and installed-metadata checks for Windows releases.

This module deliberately reads ``sharpmod/_version.py`` without importing the
package.  A release build must not let an older editable install or generated
``*.egg-info`` directory decide either the bundled metadata or the executable's
Windows version resource.
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata as importlib_metadata
import json
from pathlib import Path
import re
from typing import Any


class ReleaseContractError(RuntimeError):
    """The build environment cannot produce a version-consistent release."""


def read_source_version(repo_root: str | Path) -> str:
    """Return the authoritative version from ``sharpmod/_version.py``."""

    version_path = Path(repo_root) / "sharpmod" / "_version.py"
    tree = ast.parse(version_path.read_text(encoding="utf-8"), filename=version_path)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str) and value.strip() == value and value:
            return value
        break
    raise ReleaseContractError(
        f"{version_path} must define __version__ as a non-empty string literal"
    )


def pe_version_tuple(version: str) -> tuple[int, int, int, int]:
    """Map a Python release version to Windows' four unsigned 16-bit fields."""

    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", version)
    if match is None:
        raise ReleaseContractError(
            f"version {version!r} must begin with MAJOR.MINOR.PATCH"
        )
    components = tuple(int(value or 0) for value in match.groups())
    if any(component > 65535 for component in components):
        raise ReleaseContractError(
            f"version {version!r} exceeds the Windows 16-bit version fields"
        )
    return components


def is_sharpmod_metadata_destination(destination: str | Path) -> bool:
    """Identify a top-level sharpmod dist-info/egg-info bundle destination."""

    name = Path(destination).name.lower()
    return name == "sharpmod.egg-info" or bool(
        re.match(r"^sharpmod[-_]\d.*\.(?:dist|egg)-info$", name)
    )


def validate_installed_sharpmod(
    repo_root: str | Path,
    *,
    require_dist_info: bool = False,
    require_external_metadata: bool = False,
) -> dict[str, Any]:
    """Validate the installed distribution that PyInstaller will collect.

    Official release builds require wheel-style ``.dist-info`` outside the
    checkout.  Local developer builds may use matching editable metadata, but a
    version mismatch always fails closed.
    """

    root = Path(repo_root).resolve()
    expected = read_source_version(root)
    try:
        distribution = importlib_metadata.distribution("sharpmod")
    except importlib_metadata.PackageNotFoundError as exc:
        raise ReleaseContractError(
            "sharpmod is not installed; install this checkout before freezing it"
        ) from exc

    installed = str(distribution.version)
    metadata_name = distribution.metadata.get("Name")
    metadata_version = distribution.metadata.get("Version")
    metadata_path_value = getattr(distribution, "_path", None)
    if metadata_path_value is None:
        raise ReleaseContractError(
            "sharpmod metadata has no filesystem path and cannot be bundled safely"
        )
    metadata_path = Path(metadata_path_value).resolve()

    versions = {
        "sharpmod/_version.py": expected,
        "installed distribution": installed,
        "installed METADATA": metadata_version,
    }
    if metadata_name is None or metadata_name.lower().replace("_", "-") != "sharpmod":
        raise ReleaseContractError(
            f"installed distribution has unexpected name {metadata_name!r}"
        )
    if len(set(versions.values())) != 1:
        raise ReleaseContractError(f"sharpmod versions do not match: {versions}")
    if not metadata_path.exists():
        raise ReleaseContractError(
            f"sharpmod metadata path does not exist: {metadata_path}"
        )
    if require_dist_info and not metadata_path.name.lower().endswith(".dist-info"):
        raise ReleaseContractError(
            "official releases require wheel-style sharpmod .dist-info; "
            f"found {metadata_path}"
        )
    if require_external_metadata and (
        metadata_path == root or root in metadata_path.parents
    ):
        raise ReleaseContractError(
            "official releases must collect installed sharpmod metadata outside "
            f"the source checkout; found {metadata_path}"
        )

    return {
        "source_version": expected,
        "installed_version": installed,
        "metadata_version": metadata_version,
        "metadata_path": str(metadata_path),
        "metadata_format": (
            "dist-info"
            if metadata_path.name.lower().endswith(".dist-info")
            else "egg-info"
        ),
    }


def build_windows_version_info(repo_root: str | Path):
    """Build a PyInstaller ``VSVersionInfo`` from the authoritative version."""

    from PyInstaller.utils.win32.versioninfo import (  # imported only at build time
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    version = read_source_version(repo_root)
    version_tuple = pe_version_tuple(version)
    strings = [
        StringStruct("CompanyName", "SHARPpy Reimagined maintainers"),
        StringStruct("FileDescription", "SHARPpy Reimagined sounding analysis"),
        StringStruct("FileVersion", version),
        StringStruct("InternalName", "SHARPpy-Reimagined"),
        StringStruct("LegalCopyright", "Copyright (c) SHARPpy Reimagined maintainers"),
        StringStruct("OriginalFilename", "SHARPpy-Reimagined.exe"),
        StringStruct("ProductName", "SHARPpy Reimagined"),
        StringStruct("ProductVersion", version),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=version_tuple,
            prodvers=version_tuple,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([StringTable("040904B0", strings)]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the sharpmod metadata used by a frozen release."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--require-dist-info", action="store_true")
    parser.add_argument("--require-external-metadata", action="store_true")
    args = parser.parse_args(argv)
    report = validate_installed_sharpmod(
        args.repo_root,
        require_dist_info=args.require_dist_info,
        require_external_metadata=args.require_external_metadata,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseContractError as exc:
        raise SystemExit(f"release contract failed: {exc}") from None
