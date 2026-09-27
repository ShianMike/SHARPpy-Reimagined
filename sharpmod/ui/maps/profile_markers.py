"""Loaded-sounding markers on the station map."""

from __future__ import annotations

import math
from datetime import datetime

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QPen, QPolygonF

from sharpmod.ui.maps.presentation import _map


class ProfileMarkersMixin:
    """Represent open sounding viewers as map markers."""

    @staticmethod
    def _normalise_profile_marker_kind(value) -> str:
        key = str(value or "imported").strip().lower().replace("_", "-")
        if key in {"observed", "observation", "archive", "raob"}:
            return "observed"
        if key in {"model", "forecast", "analysis", "reanalysis"}:
            return "model"
        return "imported"

    def set_loaded_profile_points(self, points) -> None:
        """Show loaded sounding locations using source-specific marker shapes.

        ``points`` are plain mappings or objects with id/label/lat/lon/kind.
        No ProfileCollection leaks into the map, which keeps this rendering seam
        usable by the observed, model, reanalysis, file, and panel rails alike.
        """

        resolved = []
        seen = set()
        for index, item in enumerate(points or ()):
            try:
                if isinstance(item, dict):
                    get = item.get
                else:
                    get = lambda name, default=None, _item=item: getattr(
                        _item, name, default
                    )
                latitude = float(get("lat"))
                longitude = ((float(get("lon")) + 180.0) % 360.0) - 180.0
                marker_id = str(get("id") or f"profile:{index}")
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
            if not (
                math.isfinite(latitude)
                and math.isfinite(longitude)
                and -90.0 <= latitude <= 90.0
            ):
                continue
            if marker_id in seen:
                continue
            seen.add(marker_id)
            kind = self._normalise_profile_marker_kind(get("kind"))
            label = str(get("label") or get("name") or marker_id).strip()
            resolved.append(
                {
                    "id": marker_id,
                    "label": label or marker_id,
                    "lat": latitude,
                    "lon": longitude,
                    "kind": kind,
                    "active": bool(get("active", False)),
                    "valid_time": get("valid_time"),
                    "source": str(get("source") or "").strip(),
                }
            )
        markers = tuple(resolved)
        if markers == getattr(self, "_profile_markers", ()):
            return
        self._profile_markers = markers
        self._refresh_map_accessible_description()
        self.update()

    def loaded_profile_points(self) -> tuple[dict, ...]:
        return tuple(dict(item) for item in getattr(self, "_profile_markers", ()))

    def _profile_marker_style(self, kind: str) -> tuple[str, str, str]:
        palette = _map()
        if kind == "observed":
            return "triangle", palette.station, palette.station_edge
        if kind == "model":
            return "square", palette.saved, palette.saved_edge
        return "diamond", palette.domain_edge, palette.readout_shadow

    def profile_marker_key(self) -> tuple[tuple[str, str, str, str], ...]:
        """Return the shape-aware key for profile kinds present on this map."""

        present = {item["kind"] for item in getattr(self, "_profile_markers", ())}
        labels = {
            "observed": "Observed",
            "model": "Model/reanalysis",
            "imported": "Imported",
        }
        rows = []
        for kind in ("observed", "model", "imported"):
            if kind not in present:
                continue
            shape, fill, stroke = self._profile_marker_style(kind)
            rows.append((labels[kind], stroke, fill, shape))
        return tuple(rows)

    def _profile_marker_hits(self, x: float, y: float, max_px: float = 12.0):
        hits = []
        p = self._proj()
        maximum = float(max_px) ** 2
        for marker in getattr(self, "_profile_markers", ()):
            point = self._to_px(marker["lon"], marker["lat"], p)
            distance = (point.x() - float(x)) ** 2 + (point.y() - float(y)) ** 2
            if distance <= maximum:
                hits.append((not bool(marker.get("active")), distance, marker))
        hits.sort(key=lambda item: (item[0], item[1], item[2]["label"].casefold()))
        return tuple(item[2] for item in hits)

    @staticmethod
    def _profile_marker_description(marker: dict) -> str:
        kind_labels = {
            "observed": "Observed loaded profile",
            "model": "Model/reanalysis loaded profile",
            "imported": "Imported loaded profile",
        }
        lines = [
            f"{kind_labels.get(marker.get('kind'), 'Loaded profile')} — "
            f"{marker.get('label', 'profile')}",
            f"Location: {float(marker['lat']):.3f}, {float(marker['lon']):.3f}",
        ]
        valid = marker.get("valid_time")
        if isinstance(valid, datetime):
            lines.append(f"Valid time: {valid:%d %b %Y %H%MZ}")
        elif valid:
            lines.append(f"Valid time: {valid}")
        if marker.get("source"):
            lines.append(f"Source: {marker['source']}")
        if marker.get("active"):
            lines.append("Focused in an open sounding window")
        return "\n".join(lines)

    def _draw_loaded_profile_markers(self, qp, p) -> None:
        markers = getattr(self, "_profile_markers", ())
        if not markers:
            return
        qp.save()
        for marker in markers:
            point = self._to_px(marker["lon"], marker["lat"], p)
            if not (
                -12.0 <= point.x() <= self.width() + 12.0
                and -12.0 <= point.y() <= self.height() + 12.0
            ):
                continue
            shape, fill, stroke = self._profile_marker_style(marker["kind"])
            size = 6.0
            qp.setBrush(QBrush(QColor(fill)))
            qp.setPen(QPen(QColor(stroke), 1.6))
            if shape == "triangle":
                qp.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(point.x(), point.y() - size),
                            QPointF(point.x() + size, point.y() + size),
                            QPointF(point.x() - size, point.y() + size),
                        ]
                    )
                )
            elif shape == "square":
                qp.drawRect(
                    QRectF(
                        point.x() - size,
                        point.y() - size,
                        size * 2.0,
                        size * 2.0,
                    )
                )
            else:
                qp.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(point.x(), point.y() - size),
                            QPointF(point.x() + size, point.y()),
                            QPointF(point.x(), point.y() + size),
                            QPointF(point.x() - size, point.y()),
                        ]
                    )
                )
            if marker.get("active"):
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor(_map().selected_edge), 2.0))
                qp.drawEllipse(point, size + 3.5, size + 3.5)
        qp.restore()
