"""Version-gated registry for monkeypatches against vendored SHARPpy widgets."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Callable, Iterable


SUPPORTED_SHARPPY_VERSIONS = ("1.4.0a5",)


class RenderPatchError(RuntimeError):
    """Raised when a render patch registry is invalid or cannot install."""


class UnsupportedSHARPpyVersion(RenderPatchError):
    """Raised before mutation when SHARPpy internals are not supported."""


@dataclass(frozen=True)
class PatchSpec:
    """One named, ordered monkeypatch installer."""

    name: str
    installer: Callable[[], object]


def detected_sharppy_version() -> str:
    """Return the active SHARPpy version without assuming package metadata."""
    try:
        import sharppy

        declared = str(getattr(sharppy, "__version__", "") or "").strip()
        if declared:
            return declared
    except Exception:
        pass
    try:
        return package_version("SHARPpy")
    except PackageNotFoundError as exc:
        raise UnsupportedSHARPpyVersion(
            "SHARPpy is not installed; render patches cannot be validated") \
            from exc


def validate_sharppy_version(version: str | None = None) -> str:
    """Validate the exact widget-internal version targeted by the patches."""
    detected = str(version or detected_sharppy_version()).strip()
    if detected not in SUPPORTED_SHARPPY_VERSIONS:
        supported = ", ".join(SUPPORTED_SHARPPY_VERSIONS)
        raise UnsupportedSHARPpyVersion(
            f"SHARPpy {detected or '<unknown>'} is not supported by the render "
            f"patch registry; expected {supported}")
    return detected


def apply_patch_registry(
        patches: Iterable[PatchSpec], *,
        sharppy_version: str | None = None) -> tuple[str, ...]:
    """Validate then install every patch in stable order."""
    specs = tuple(patches)
    seen = set()
    for spec in specs:
        name = str(getattr(spec, "name", "") or "").strip()
        if not name:
            raise RenderPatchError("render patch names cannot be empty")
        if name in seen:
            raise RenderPatchError(f"duplicate render patch name: {name}")
        if not callable(getattr(spec, "installer", None)):
            raise RenderPatchError(
                f"render patch installer is not callable: {name}")
        seen.add(name)

    validate_sharppy_version(sharppy_version)
    installed = []
    for spec in specs:
        try:
            spec.installer()
        except Exception as exc:
            raise RenderPatchError(
                f"render patch failed during {spec.name}: {exc}") from exc
        installed.append(spec.name)
    return tuple(installed)


__all__ = [
    "PatchSpec",
    "RenderPatchError",
    "SUPPORTED_SHARPPY_VERSIONS",
    "UnsupportedSHARPpyVersion",
    "apply_patch_registry",
    "detected_sharppy_version",
    "validate_sharppy_version",
]
