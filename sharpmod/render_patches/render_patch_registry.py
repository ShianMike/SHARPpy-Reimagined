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
    import sharpmod_rs

    detected = str(version if version else detected_sharppy_version())
    return sharpmod_rs.patch_registry_validate_version(detected)


def apply_patch_registry(
        patches: Iterable[PatchSpec], *,
        sharppy_version: str | None = None) -> tuple[str, ...]:
    """Validate then install every patch in stable order."""
    import sharpmod_rs

    specs = tuple(patches)
    sharpmod_rs.patch_registry_validate_specs(list(specs))

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
