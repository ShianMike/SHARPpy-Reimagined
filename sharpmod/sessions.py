"""Portable analysis sessions and bounded undo/redo for SHARPpy viewers.

Session documents intentionally contain only decoded sounding/profile state.
They never retain a source GRIB file or a model download directory, so the GUI
can keep its existing delete-on-viewer-close lifecycle.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Callable

import numpy as np
import numpy.ma as ma
from sharppy.sharptab import prof_collection, profile


SESSION_FORMAT = "sharpmod-analysis-session"
SESSION_VERSION = 1
DEFAULT_HISTORY_LIMIT = 50

_ARRAY_FIELDS = (
    "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v", "omeg",
    "tmp_stdev", "dew_stdev",
)
_EXTRA_SCALAR_FIELDS = (
    "surface_relative_vorticity",
    "sfc_relative_vorticity",
    "surface_vorticity",
    "sfc_vorticity",
    "vorticity",
)


class SessionFormatError(ValueError):
    """Raised when a session document is malformed or unsupported."""


def _encode_array(value) -> dict | None:
    if value is None:
        return None
    arr = ma.asarray(value, dtype=float)
    data = np.asarray(arr.filled(np.nan), dtype=float)
    mask = ma.getmaskarray(arr) | ~np.isfinite(data)
    safe = np.where(mask, 0.0, data)
    return {
        "data": safe.tolist(),
        "mask": np.asarray(mask, dtype=bool).tolist(),
    }


def _decode_array(payload):
    if payload is None:
        return None
    if not isinstance(payload, dict) or "data" not in payload \
            or "mask" not in payload:
        raise SessionFormatError("profile array payload is malformed")
    data = np.asarray(payload["data"], dtype=float)
    mask = np.asarray(payload["mask"], dtype=bool)
    if data.ndim != 1 or mask.shape != data.shape:
        raise SessionFormatError("profile array data and mask do not match")
    return ma.array(data, mask=mask)


def _encode_value(value):
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, Path):
        return {"__type__": "path", "value": str(value)}
    if isinstance(value, np.generic):
        return _encode_value(value.item())
    if isinstance(value, (np.ndarray, ma.MaskedArray)):
        return {"__type__": "array", "value": _encode_array(value)}
    if isinstance(value, tuple):
        return {"__type__": "tuple",
                "items": [_encode_value(item) for item in value]}
    if isinstance(value, list):
        return [_encode_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not np.isfinite(value):
            return {"__type__": "nonfinite", "value": str(value)}
        return value
    return {"__type__": "text", "value": str(value)}


def _decode_value(value):
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("__type__")
    if tag == "datetime":
        try:
            return datetime.fromisoformat(str(value["value"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionFormatError("session datetime is invalid") from exc
    if tag == "path":
        return Path(str(value.get("value", "")))
    if tag == "array":
        return _decode_array(value.get("value"))
    if tag == "tuple":
        return tuple(_decode_value(item) for item in value.get("items", []))
    if tag == "nonfinite":
        raw = str(value.get("value", "nan")).lower()
        return float("-inf" if raw.startswith("-") and "inf" in raw
                     else "inf" if "inf" in raw else "nan")
    if tag == "text":
        return str(value.get("value", ""))
    return {str(key): _decode_value(item) for key, item in value.items()}


def _snapshot_profile(prof) -> dict:
    arrays = {
        field: _encode_array(getattr(prof, field, None))
        for field in _ARRAY_FIELDS
    }
    extras = {}
    for field in _EXTRA_SCALAR_FIELDS:
        if hasattr(prof, field):
            extras[field] = _encode_value(getattr(prof, field))
    storm_motion = getattr(prof, "srwind", None)
    return {
        "arrays": arrays,
        "location": _encode_value(getattr(prof, "location", None)),
        "date": _encode_value(getattr(prof, "date", None)),
        "latitude": _encode_value(getattr(prof, "latitude", None)),
        "missing": float(getattr(prof, "missing", -9999.0)),
        "strict_qc": bool(getattr(prof, "strictQC", False)),
        "extras": extras,
        "storm_motion": _encode_value(tuple(storm_motion))
        if storm_motion is not None else None,
    }


def _restore_profile(payload: dict):
    if not isinstance(payload, dict):
        raise SessionFormatError("profile payload is malformed")
    arrays = payload.get("arrays")
    if not isinstance(arrays, dict):
        raise SessionFormatError("profile arrays are missing")
    kwargs = {
        "profile": "raw",
        "pres": _decode_array(arrays.get("pres")),
        "hght": _decode_array(arrays.get("hght")),
        "tmpc": _decode_array(arrays.get("tmpc")),
        "dwpc": _decode_array(arrays.get("dwpc")),
        "omeg": _decode_array(arrays.get("omeg")),
        "location": _decode_value(payload.get("location")),
        "date": _decode_value(payload.get("date")),
        "latitude": _decode_value(payload.get("latitude")),
        "missing": float(payload.get("missing", -9999.0)),
        "strictQC": bool(payload.get("strict_qc", False)),
    }
    wdir = _decode_array(arrays.get("wdir"))
    wspd = _decode_array(arrays.get("wspd"))
    if wdir is not None and wspd is not None:
        kwargs.update(wdir=wdir, wspd=wspd)
    else:
        u = _decode_array(arrays.get("u"))
        v = _decode_array(arrays.get("v"))
        if u is None or v is None:
            raise SessionFormatError("profile wind arrays are missing")
        kwargs.update(u=u, v=v)
    tmp_stdev = _decode_array(arrays.get("tmp_stdev"))
    dew_stdev = _decode_array(arrays.get("dew_stdev"))
    if tmp_stdev is not None:
        kwargs.update(tmp_stdev=tmp_stdev, dew_stdev=dew_stdev)

    try:
        restored = profile.create_profile(**kwargs)
    except Exception as exc:
        raise SessionFormatError(f"profile could not be restored: {exc}") from exc
    for field, value in (payload.get("extras") or {}).items():
        setattr(restored, str(field), _decode_value(value))
    storm_motion = payload.get("storm_motion")
    if storm_motion is not None:
        restored.srwind = tuple(_decode_value(storm_motion))
    return restored


def snapshot_collection(collection) -> dict:
    """Return a JSON-compatible snapshot of one SHARPpy profile collection."""
    profiles = {
        str(member): [_snapshot_profile(prof) for prof in member_profiles]
        for member, member_profiles in collection._profs.items()
    }
    return {
        "profiles": profiles,
        "dates": [_encode_value(value) for value in collection._dates],
        "meta": _encode_value(collection._meta),
        "highlight": str(collection._highlight),
        "profile_index": int(collection._prof_idx),
        "analog_date": _encode_value(collection._analog_date),
        "modified_thermo": [bool(value) for value in collection._mod_therm],
        "modified_wind": [bool(value) for value in collection._mod_wind],
        "interpolated": [bool(value) for value in collection._interp],
        "original_profiles": {
            str(index): _snapshot_profile(prof)
            for index, prof in collection._orig_profs.items()
        },
        "interpolated_profiles": {
            str(index): _snapshot_profile(prof)
            for index, prof in collection._interp_profs.items()
        },
    }


def _flags(values, length: int) -> list[bool]:
    result = [bool(value) for value in (values or [])][:length]
    return result + [False] * (length - len(result))


def restore_collection(payload: dict):
    """Reconstruct a SHARPpy ``ProfCollection`` from a portable snapshot."""
    if not isinstance(payload, dict) or not isinstance(
            payload.get("profiles"), dict) or not payload["profiles"]:
        raise SessionFormatError("session collection has no profiles")
    profiles = {
        str(member): [_restore_profile(item) for item in items]
        for member, items in payload["profiles"].items()
    }
    dates = [_decode_value(item) for item in payload.get("dates", [])]
    if not dates:
        raise SessionFormatError("session collection has no dates")
    if any(len(items) != len(dates) for items in profiles.values()):
        raise SessionFormatError("profile member lengths do not match dates")
    meta = _decode_value(payload.get("meta") or {})
    if not isinstance(meta, dict):
        raise SessionFormatError("session collection metadata is malformed")
    restored = prof_collection.ProfCollection(profiles, dates, **meta)
    restored._meta = meta
    highlight = str(payload.get("highlight", ""))
    restored._highlight = highlight if highlight in profiles else next(iter(profiles))
    restored._prof_idx = max(
        0, min(int(payload.get("profile_index", 0)), len(dates) - 1))
    restored._analog_date = _decode_value(payload.get("analog_date"))
    restored._mod_therm = _flags(payload.get("modified_thermo"), len(dates))
    restored._mod_wind = _flags(payload.get("modified_wind"), len(dates))
    restored._interp = _flags(payload.get("interpolated"), len(dates))
    restored._orig_profs = {
        int(index): _restore_profile(item)
        for index, item in (payload.get("original_profiles") or {}).items()
    }
    restored._interp_profs = {
        int(index): _restore_profile(item)
        for index, item in (payload.get("interpolated_profiles") or {}).items()
    }
    return restored


def build_session(collections, *, active_collection=0, ui_state=None) -> dict:
    """Build a versioned analysis-session document from profile collections."""
    collections = list(collections)
    if not collections:
        raise SessionFormatError("an analysis session needs at least one sounding")
    active_collection = int(active_collection)
    if active_collection < 0 or active_collection >= len(collections):
        raise SessionFormatError("active collection index is out of range")
    return {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "created": datetime.now().astimezone().isoformat(),
        "active_collection": active_collection,
        "ui_state": _encode_value(dict(ui_state or {})),
        "collections": [snapshot_collection(item) for item in collections],
    }


def _validate_document(document: dict) -> None:
    if not isinstance(document, dict):
        raise SessionFormatError("analysis session root must be an object")
    if document.get("format") != SESSION_FORMAT:
        raise SessionFormatError("not a SHARPpy Reimagined analysis session")
    if document.get("version") != SESSION_VERSION:
        raise SessionFormatError(
            "unsupported analysis session version: %s"
            % document.get("version"))
    collections = document.get("collections")
    if not isinstance(collections, list) or not collections:
        raise SessionFormatError("analysis session has no soundings")
    active = document.get("active_collection", 0)
    if not isinstance(active, int) or active < 0 or active >= len(collections):
        raise SessionFormatError("analysis session active sounding is invalid")


def write_session(path, document: dict) -> str:
    """Atomically write a validated session document and return its path."""
    _validate_document(document)
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=2, sort_keys=True,
                      ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return str(path)


def read_session(path) -> dict:
    """Read and validate a session document without mutating a viewer."""
    try:
        with Path(path).expanduser().open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionFormatError(f"analysis session could not be read: {exc}") from exc
    _validate_document(document)
    document["ui_state"] = _decode_value(document.get("ui_state") or {})
    return document


@dataclass
class _HistoryEntry:
    label: str
    collection_index: int
    before: dict
    after: dict


class AnalysisHistory:
    """A bounded undo/redo stack attached to one vendored SPC widget."""

    def __init__(self, spc_widget, limit: int = DEFAULT_HISTORY_LIMIT):
        self._widget = spc_widget
        self._limit = max(1, int(limit))
        self._undo: list[_HistoryEntry] = []
        self._redo: list[_HistoryEntry] = []
        self._suspend = 0
        self._listeners: list[Callable[[], None]] = []

    @property
    def undo_depth(self) -> int:
        return len(self._undo)

    @property
    def redo_depth(self) -> int:
        return len(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)
        self._notify()

    def _notify(self) -> None:
        for callback in tuple(self._listeners):
            try:
                callback()
            except Exception:
                pass

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def capture(self):
        if self._suspend:
            return None
        try:
            index = int(self._widget.pc_idx)
            collection = self._widget.prof_collections[index]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
        return index, snapshot_collection(collection)

    def commit(self, label: str, captured) -> None:
        if captured is None or self._suspend:
            return
        index, before = captured
        try:
            after = snapshot_collection(self._widget.prof_collections[index])
        except (AttributeError, IndexError, TypeError):
            return
        self.record(label, index, before, after)

    def record(self, label: str, collection_index: int,
               before: dict, after: dict) -> None:
        if self._suspend or before == after:
            return
        self._undo.append(_HistoryEntry(
            str(label), int(collection_index), before, after))
        if len(self._undo) > self._limit:
            del self._undo[:-self._limit]
        self._redo.clear()
        self._notify()

    def _apply(self, entry: _HistoryEntry, *, after: bool) -> None:
        payload = entry.after if after else entry.before
        self._suspend += 1
        try:
            index = entry.collection_index
            self._widget.prof_collections[index] = restore_collection(payload)
            self._widget.pc_idx = index
            self._widget.updateProfs()
            self._widget.setFocus()
        finally:
            self._suspend -= 1

    def undo(self) -> str | None:
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._apply(entry, after=False)
        self._redo.append(entry)
        self._notify()
        return entry.label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._apply(entry, after=True)
        self._undo.append(entry)
        self._notify()
        return entry.label

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._notify()


def install_history_hooks(spc_widget_class) -> None:
    """Wrap vendored mutation methods so existing UI paths record history."""
    if getattr(spc_widget_class, "_sharpmod_history_hooks_installed", False):
        return
    labels = {
        "modifyProf": "Edit sounding level",
        "resetProfModifications": "Reset profile edits",
        "interpProf": "Interpolate profile",
        "resetProfInterpolation": "Reset interpolation",
        "modifyVector": "Edit storm motion",
        "resetVector": "Reset storm motion",
    }
    for name, label in labels.items():
        original = getattr(spc_widget_class, name, None)
        if not callable(original):
            continue

        @wraps(original)
        def wrapped(self, *args, __original=original, __label=label, **kwargs):
            history = getattr(self, "_sharpmod_history", None)
            captured = history.capture() if history is not None else None
            result = __original(self, *args, **kwargs)
            if history is not None:
                history.commit(__label, captured)
            return result

        setattr(spc_widget_class, name, wrapped)
    spc_widget_class._sharpmod_history_hooks_installed = True


__all__ = [
    "AnalysisHistory",
    "SESSION_FORMAT",
    "SESSION_VERSION",
    "SessionFormatError",
    "build_session",
    "install_history_hooks",
    "read_session",
    "restore_collection",
    "snapshot_collection",
    "write_session",
]
