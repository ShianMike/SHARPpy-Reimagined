#!/usr/bin/env python3
"""Install the exact upstream SHARPpy wheel with compatible NumPy metadata.

SHARPpy 1.4.0a5 works with this project's modern NumPy runtime, but its PyPI
wheel declares the obsolete requirement ``numpy==1.15.*``.  Installing that
wheel with ``--no-deps`` leaves an intentionally broken environment according
to ``pip check``.

This helper makes the narrowest possible compatibility wheel:

* download (or accept locally) the exact PyPI wheel pinned below by size and
  SHA-256;
* validate its distribution name, version, stale NumPy requirement, and wheel
  RECORD;
* replace only that ``Requires-Dist`` value with the NumPy requirement from
  this repository's ``pyproject.toml``;
* preserve every upstream payload byte, add a machine-readable provenance
  record, and regenerate RECORD; and
* install the result and require ``pip check`` to pass.

Run without arguments for a one-command editable source setup.  CI/release
jobs that already installed the project can pass ``--sharppy-only``.
"""

from __future__ import annotations

import argparse
import base64
import csv
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]

UPSTREAM_NAME = "SHARPpy"
UPSTREAM_VERSION = "1.4.0a5"
UPSTREAM_FILENAME = "SHARPpy-1.4.0a5-py2.py3-none-any.whl"
UPSTREAM_SIZE = 13_580_446
UPSTREAM_SHA256 = (
    "13582f88ba1932b842cbf3ceb6f5f1ddadc17b0b2fd9172a3fc74ed0bcadb868"
)
UPSTREAM_URL = (
    "https://files.pythonhosted.org/packages/ed/83/"
    "58a5c4f6c68267664e26a4da5ca465f0f246befb198bb3b1d1c7abdc5abf/"
    + UPSTREAM_FILENAME
)
STALE_NUMPY_REQUIREMENT = "numpy (==1.15.*)"
PROVENANCE_FILENAME = "SHARPMOD-PROVENANCE.json"
PROVENANCE_SCHEMA = 1

_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")
_STALE_NUMPY_HEADER = re.compile(
    br"^Requires-Dist:[ \t]*numpy[ \t]*\(==1\.15\.\*\)[ \t]*(\r?)$",
    flags=re.MULTILINE | re.IGNORECASE,
)


class CompatibilityInstallError(RuntimeError):
    """The pinned compatibility artifact could not be safely produced."""


@dataclass(frozen=True)
class RepackedWheel:
    """Auditable result returned by :func:`repack_wheel`."""

    path: Path
    source_sha256: str
    patched_sha256: str
    numpy_requirement: str
    provenance_path: str


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return "sha256=" + encoded


def _normalise_requirement(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def project_numpy_requirement(project_root: Path = ROOT) -> str:
    """Return the single core NumPy requirement declared by this project."""
    pyproject_path = Path(project_root) / "pyproject.toml"
    try:
        with pyproject_path.open("rb") as stream:
            dependencies = tomllib.load(stream)["project"]["dependencies"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise CompatibilityInstallError(
            f"could not read project dependencies from {pyproject_path}: {exc}"
        ) from exc

    matches = []
    for requirement in dependencies:
        match = _REQUIREMENT_NAME.match(str(requirement).strip())
        if match and _canonical_name(match.group(1)) == "numpy":
            matches.append(str(requirement).strip())
    if len(matches) != 1:
        raise CompatibilityInstallError(
            "pyproject.toml must declare exactly one core NumPy requirement; "
            f"found {matches!r}"
        )
    requirement = matches[0]
    if ";" in requirement or "[" in requirement:
        raise CompatibilityInstallError(
            f"unsupported conditional NumPy requirement: {requirement!r}"
        )
    if not re.fullmatch(r"numpy[<>=!~.,0-9*]+", requirement, re.IGNORECASE):
        raise CompatibilityInstallError(
            f"unsupported NumPy requirement syntax: {requirement!r}"
        )
    return requirement


def _validate_source_file(
    path: Path,
    *,
    expected_sha256: str = UPSTREAM_SHA256,
    expected_size: int = UPSTREAM_SIZE,
) -> str:
    if not path.is_file():
        raise CompatibilityInstallError(f"upstream wheel does not exist: {path}")
    size = path.stat().st_size
    if size != expected_size:
        raise CompatibilityInstallError(
            f"upstream wheel size mismatch: expected {expected_size}, got {size}"
        )
    digest = _sha256_file(path)
    if digest.lower() != expected_sha256.lower():
        raise CompatibilityInstallError(
            "upstream wheel SHA-256 mismatch: "
            f"expected {expected_sha256}, got {digest}"
        )
    return digest.lower()


def download_upstream_wheel(
    destination: Path,
    *,
    timeout: float = 60.0,
) -> Path:
    """Download the hash-pinned official PyPI wheel to ``destination``."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise CompatibilityInstallError(
            f"refusing to overwrite existing download: {destination}"
        )

    request = Request(
        UPSTREAM_URL,
        headers={"User-Agent": "sharpmod-sharppy-compat/1"},
    )
    temporary = destination.with_name(destination.name + ".part")
    if temporary.exists():
        raise CompatibilityInstallError(
            f"refusing to overwrite partial download: {temporary}"
        )

    digest = hashlib.sha256()
    received = 0
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = urlparse(response.geturl())
            if (
                final_url.scheme != "https"
                or final_url.hostname != "files.pythonhosted.org"
            ):
                raise CompatibilityInstallError(
                    f"unexpected upstream download URL: {response.geturl()}"
                )
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) != UPSTREAM_SIZE:
                raise CompatibilityInstallError(
                    "upstream Content-Length mismatch: expected "
                    f"{UPSTREAM_SIZE}, got {length_header}"
                )
            with temporary.open("xb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > UPSTREAM_SIZE:
                        raise CompatibilityInstallError(
                            "upstream download exceeded its pinned size"
                        )
                    digest.update(block)
                    output.write(block)
        if received != UPSTREAM_SIZE:
            raise CompatibilityInstallError(
                "upstream download size mismatch: "
                f"expected {UPSTREAM_SIZE}, got {received}"
            )
        if digest.hexdigest() != UPSTREAM_SHA256:
            raise CompatibilityInstallError(
                "upstream download SHA-256 mismatch: expected "
                f"{UPSTREAM_SHA256}, got {digest.hexdigest()}"
            )
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return destination


def _validate_archive_names(archive: zipfile.ZipFile) -> None:
    names = [info.filename for info in archive.infolist()]
    if len(names) != len(set(names)):
        raise CompatibilityInstallError("upstream wheel contains duplicate paths")
    for name in names:
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or ".." in path.parts
        ):
            raise CompatibilityInstallError(
                f"upstream wheel contains an unsafe path: {name!r}"
            )


def _single_dist_info_path(
    archive: zipfile.ZipFile,
    filename: str,
) -> str:
    matches = [
        info.filename
        for info in archive.infolist()
        if info.filename.endswith(".dist-info/" + filename)
    ]
    if len(matches) != 1:
        raise CompatibilityInstallError(
            f"upstream wheel must contain one {filename}; found {matches!r}"
        )
    return matches[0]


def _verify_record(archive: zipfile.ZipFile, record_path: str) -> None:
    """Validate every file hash/size in a wheel's PEP 376 RECORD."""
    try:
        rows = list(
            csv.reader(io.StringIO(archive.read(record_path).decode("utf-8")))
        )
    except (KeyError, UnicodeDecodeError, csv.Error) as exc:
        raise CompatibilityInstallError(
            f"could not parse upstream wheel RECORD: {exc}"
        ) from exc

    by_path: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or row[0] in by_path:
            raise CompatibilityInstallError(
                f"invalid or duplicate wheel RECORD row: {row!r}"
            )
        by_path[row[0]] = (row[1], row[2])

    file_names = {
        info.filename for info in archive.infolist() if not info.is_dir()
    }
    if set(by_path) != file_names:
        missing = sorted(file_names - set(by_path))
        extra = sorted(set(by_path) - file_names)
        raise CompatibilityInstallError(
            f"wheel RECORD coverage mismatch; missing={missing}, extra={extra}"
        )

    for name in sorted(file_names):
        hash_value, size_value = by_path[name]
        if name == record_path:
            if hash_value or size_value:
                raise CompatibilityInstallError(
                    "wheel RECORD must leave its own hash and size empty"
                )
            continue
        data = archive.read(name)
        if hash_value != _record_hash(data):
            raise CompatibilityInstallError(
                f"wheel RECORD hash mismatch for {name}"
            )
        if size_value != str(len(data)):
            raise CompatibilityInstallError(
                f"wheel RECORD size mismatch for {name}"
            )


def _metadata_requirements(metadata_bytes: bytes) -> tuple[str, str, list[str]]:
    try:
        metadata = BytesParser(policy=default).parsebytes(metadata_bytes)
    except Exception as exc:
        raise CompatibilityInstallError(
            f"could not parse upstream wheel metadata: {exc}"
        ) from exc
    name = str(metadata.get("Name", ""))
    version = str(metadata.get("Version", ""))
    requirements = list(metadata.get_all("Requires-Dist", []))
    return name, version, requirements


def _numpy_requirements(requirements: list[str]) -> list[str]:
    matches = []
    for requirement in requirements:
        match = _REQUIREMENT_NAME.match(requirement.strip())
        if match and _canonical_name(match.group(1)) == "numpy":
            matches.append(requirement)
    return matches


def _patched_metadata(
    original: bytes,
    numpy_requirement: str,
) -> bytes:
    matches = list(_STALE_NUMPY_HEADER.finditer(original))
    if len(matches) != 1:
        raise CompatibilityInstallError(
            "expected exactly one stale NumPy Requires-Dist header in the "
            f"pinned upstream wheel; found {len(matches)}"
        )
    replacement = (
        b"Requires-Dist: "
        + numpy_requirement.encode("ascii")
        + matches[0].group(1)
    )
    patched = _STALE_NUMPY_HEADER.sub(replacement, original, count=1)

    name, version, requirements = _metadata_requirements(patched)
    if _canonical_name(name) != _canonical_name(UPSTREAM_NAME):
        raise CompatibilityInstallError(
            f"patched wheel has unexpected name {name!r}"
        )
    if version != UPSTREAM_VERSION:
        raise CompatibilityInstallError(
            f"patched wheel has unexpected version {version!r}"
        )
    numpy_requirements = _numpy_requirements(requirements)
    if (
        len(numpy_requirements) != 1
        or _normalise_requirement(numpy_requirements[0])
        != _normalise_requirement(numpy_requirement)
    ):
        raise CompatibilityInstallError(
            "patched wheel NumPy metadata did not validate: "
            f"{numpy_requirements!r}"
        )
    return patched


def _record_bytes(
    rows: list[tuple[str, str, str]],
    record_path: str,
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows([*rows, (record_path, "", "")])
    return stream.getvalue().encode("utf-8")


def _provenance_bytes(
    numpy_requirement: str,
    source_sha256: str,
) -> bytes:
    provenance = {
        "schema_version": PROVENANCE_SCHEMA,
        "action": "metadata-compatibility-repack",
        "tool": "scripts/install_sharppy_compat.py",
        "upstream": {
            "name": UPSTREAM_NAME,
            "version": UPSTREAM_VERSION,
            "filename": UPSTREAM_FILENAME,
            "size": UPSTREAM_SIZE,
            "sha256": source_sha256,
            "url": UPSTREAM_URL,
        },
        "metadata_change": {
            "field": "Requires-Dist",
            "original": STALE_NUMPY_REQUIREMENT,
            "replacement": numpy_requirement,
        },
        "upstream_payload_files_unchanged": True,
        "record_regenerated": True,
    }
    return (
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _new_dist_info(
    filename: str,
    template: zipfile.ZipInfo,
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename, date_time=template.date_time)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = template.create_system
    info.external_attr = template.external_attr or (0o100644 << 16)
    return info


def _verify_upstream_metadata(metadata_bytes: bytes) -> None:
    name, version, requirements = _metadata_requirements(metadata_bytes)
    if _canonical_name(name) != _canonical_name(UPSTREAM_NAME):
        raise CompatibilityInstallError(
            f"upstream wheel name mismatch: expected {UPSTREAM_NAME}, got {name}"
        )
    if version != UPSTREAM_VERSION:
        raise CompatibilityInstallError(
            "upstream wheel version mismatch: "
            f"expected {UPSTREAM_VERSION}, got {version}"
        )
    numpy_requirements = _numpy_requirements(requirements)
    if (
        len(numpy_requirements) != 1
        or _normalise_requirement(numpy_requirements[0])
        != _normalise_requirement(STALE_NUMPY_REQUIREMENT)
    ):
        raise CompatibilityInstallError(
            "upstream wheel's stale NumPy requirement changed unexpectedly: "
            f"{numpy_requirements!r}"
        )


def _verify_payload_unchanged(
    source: zipfile.ZipFile,
    patched: zipfile.ZipFile,
    *,
    metadata_path: str,
    record_path: str,
    provenance_path: str,
) -> None:
    excluded = {metadata_path, record_path, provenance_path}
    source_names = {
        info.filename for info in source.infolist()
        if not info.is_dir() and info.filename not in excluded
    }
    patched_names = {
        info.filename for info in patched.infolist()
        if not info.is_dir() and info.filename not in excluded
    }
    if source_names != patched_names:
        raise CompatibilityInstallError(
            "compatibility repack changed the upstream payload path set"
        )
    for name in sorted(source_names):
        if source.read(name) != patched.read(name):
            raise CompatibilityInstallError(
                f"compatibility repack changed upstream payload bytes: {name}"
            )


def repack_wheel(
    source_path: Path,
    output_path: Path,
    numpy_requirement: str,
    *,
    expected_sha256: str = UPSTREAM_SHA256,
    expected_size: int = UPSTREAM_SIZE,
) -> RepackedWheel:
    """Create and fully validate the corrected compatibility wheel."""
    source_path = Path(source_path)
    output_path = Path(output_path)
    if source_path.resolve() == output_path.resolve():
        raise CompatibilityInstallError(
            "source and compatibility wheel paths must be different"
        )
    source_sha256 = _validate_source_file(
        source_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source_path) as source:
        _validate_archive_names(source)
        metadata_path = _single_dist_info_path(source, "METADATA")
        wheel_path = _single_dist_info_path(source, "WHEEL")
        record_path = _single_dist_info_path(source, "RECORD")
        dist_info = metadata_path.rsplit("/", 1)[0]
        if wheel_path.rsplit("/", 1)[0] != dist_info:
            raise CompatibilityInstallError(
                "upstream WHEEL and METADATA use different dist-info paths"
            )
        if record_path.rsplit("/", 1)[0] != dist_info:
            raise CompatibilityInstallError(
                "upstream RECORD and METADATA use different dist-info paths"
            )
        provenance_path = f"{dist_info}/{PROVENANCE_FILENAME}"
        if provenance_path in source.namelist():
            raise CompatibilityInstallError(
                "upstream wheel unexpectedly contains sharpmod provenance"
            )
        _verify_record(source, record_path)
        metadata_original = source.read(metadata_path)
        _verify_upstream_metadata(metadata_original)
        metadata_patched = _patched_metadata(
            metadata_original,
            numpy_requirement,
        )
        provenance = _provenance_bytes(numpy_requirement, source_sha256)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=output_path.name + ".",
            suffix=".tmp",
            dir=output_path.parent,
        )
        os.close(file_descriptor)
        temporary_path = Path(temporary_name)
        try:
            rows: list[tuple[str, str, str]] = []
            with zipfile.ZipFile(temporary_path, "w") as target:
                metadata_info = source.getinfo(metadata_path)
                record_info = source.getinfo(record_path)
                for info in source.infolist():
                    if info.filename == record_path:
                        continue
                    data = (
                        metadata_patched
                        if info.filename == metadata_path
                        else source.read(info.filename)
                    )
                    target.writestr(info, data)
                    if not info.is_dir():
                        rows.append(
                            (
                                info.filename,
                                _record_hash(data),
                                str(len(data)),
                            )
                        )
                provenance_info = _new_dist_info(
                    provenance_path,
                    metadata_info,
                )
                target.writestr(provenance_info, provenance)
                rows.append(
                    (
                        provenance_path,
                        _record_hash(provenance),
                        str(len(provenance)),
                    )
                )
                record = _record_bytes(rows, record_path)
                target.writestr(record_info, record)

            with (
                zipfile.ZipFile(source_path) as source_check,
                zipfile.ZipFile(temporary_path) as patched_check,
            ):
                _validate_archive_names(patched_check)
                _verify_record(patched_check, record_path)
                _verify_payload_unchanged(
                    source_check,
                    patched_check,
                    metadata_path=metadata_path,
                    record_path=record_path,
                    provenance_path=provenance_path,
                )
                if patched_check.read(metadata_path) != metadata_patched:
                    raise CompatibilityInstallError(
                        "patched wheel metadata changed during repack"
                    )
                loaded_provenance = json.loads(
                    patched_check.read(provenance_path).decode("utf-8")
                )
                if loaded_provenance["upstream"]["sha256"] != source_sha256:
                    raise CompatibilityInstallError(
                        "patched wheel provenance did not validate"
                    )

            if output_path.exists():
                if _sha256_file(output_path) != _sha256_file(temporary_path):
                    raise CompatibilityInstallError(
                        "refusing to overwrite a different compatibility wheel: "
                        f"{output_path}"
                    )
                temporary_path.unlink()
            else:
                os.replace(temporary_path, output_path)
        except BaseException:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise

    return RepackedWheel(
        path=output_path,
        source_sha256=source_sha256,
        patched_sha256=_sha256_file(output_path),
        numpy_requirement=numpy_requirement,
        provenance_path=provenance_path,
    )


def _run(command: list[str]) -> None:
    print("+ " + subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise CompatibilityInstallError(
            f"command failed with exit code {completed.returncode}: "
            + subprocess.list2cmdline(command)
        )


def _install_project(extras: str) -> None:
    names = [name.strip() for name in extras.split(",") if name.strip()]
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", name) for name in names):
        raise CompatibilityInstallError(
            f"invalid project extras list: {extras!r}"
        )
    target = str(ROOT)
    if names:
        target += "[" + ",".join(names) + "]"
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "--editable",
            target,
        ]
    )


def _install_compatibility_wheel(path: Path) -> None:
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "--force-reinstall",
            "--no-deps",
            str(path),
        ]
    )


def _verify_installed(numpy_requirement: str) -> None:
    try:
        distribution = importlib.metadata.distribution(UPSTREAM_NAME)
    except importlib.metadata.PackageNotFoundError as exc:
        raise CompatibilityInstallError(
            "SHARPpy was not installed after compatibility-wheel installation"
        ) from exc
    if distribution.version != UPSTREAM_VERSION:
        raise CompatibilityInstallError(
            "installed SHARPpy version mismatch: "
            f"expected {UPSTREAM_VERSION}, got {distribution.version}"
        )
    requirements = list(distribution.requires or ())
    numpy_requirements = _numpy_requirements(requirements)
    if (
        len(numpy_requirements) != 1
        or _normalise_requirement(numpy_requirements[0])
        != _normalise_requirement(numpy_requirement)
    ):
        raise CompatibilityInstallError(
            "installed SHARPpy NumPy requirement did not validate: "
            f"{numpy_requirements!r}"
        )
    provenance_text = distribution.read_text(PROVENANCE_FILENAME)
    if provenance_text is None:
        raise CompatibilityInstallError(
            "installed SHARPpy provenance record is missing"
        )
    provenance = json.loads(provenance_text)
    if provenance.get("upstream", {}).get("sha256") != UPSTREAM_SHA256:
        raise CompatibilityInstallError(
            "installed SHARPpy provenance hash did not validate"
        )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install an auditable SHARPpy 1.4.0a5 compatibility wheel whose "
            "NumPy metadata matches this project."
        )
    )
    parser.add_argument(
        "--sharppy-only",
        action="store_true",
        help="skip editable project installation (for CI/release jobs)",
    )
    parser.add_argument(
        "--extras",
        default="render",
        help=(
            "comma-separated project extras for source setup "
            "(default: render; ignored with --sharppy-only)"
        ),
    )
    parser.add_argument(
        "--source-wheel",
        type=Path,
        help=(
            "use a local copy of the exact pinned upstream wheel instead of "
            "downloading it"
        ),
    )
    parser.add_argument(
        "--wheel-dir",
        type=Path,
        help="retain the corrected wheel in this directory",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="build and verify the corrected wheel without installing it",
    )
    parser.add_argument(
        "--skip-pip-check",
        action="store_true",
        help="skip the final whole-environment pip check",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="download timeout in seconds (default: 60)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    if args.build_only and args.wheel_dir is None:
        parser.error("--build-only requires --wheel-dir")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    try:
        numpy_requirement = project_numpy_requirement()
        with tempfile.TemporaryDirectory(
            prefix="sharpmod-sharppy-compat-"
        ) as temporary:
            work = Path(temporary)
            if args.source_wheel is None:
                source = download_upstream_wheel(
                    work / UPSTREAM_FILENAME,
                    timeout=args.timeout,
                )
            else:
                source = args.source_wheel.resolve()

            wheel_dir = (
                args.wheel_dir.resolve()
                if args.wheel_dir is not None
                else work / "patched"
            )
            patched = repack_wheel(
                source,
                wheel_dir / UPSTREAM_FILENAME,
                numpy_requirement,
            )
            print(f"Verified upstream SHA-256: {patched.source_sha256}")
            print(f"Built compatibility wheel: {patched.path}")
            print(f"Compatibility SHA-256: {patched.patched_sha256}")
            print(f"NumPy requirement: {patched.numpy_requirement}")
            print(f"Provenance: {patched.provenance_path}")

            if args.build_only:
                return 0
            if not args.sharppy_only:
                _install_project(args.extras)
            _install_compatibility_wheel(patched.path)
            _verify_installed(numpy_requirement)
            if not args.skip_pip_check:
                _run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "--disable-pip-version-check",
                        "check",
                    ]
                )
            print(
                f"Installed {UPSTREAM_NAME} {UPSTREAM_VERSION} with "
                f"{numpy_requirement}; environment verification passed."
            )
            return 0
    except CompatibilityInstallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"ERROR: compatibility install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
