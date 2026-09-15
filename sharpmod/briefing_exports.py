"""Self-contained briefing and fixed-frame animation exports.

Writers use a sibling temporary file and replace the destination only after a
complete, validated artifact exists.  A cancellation or renderer error cannot
leave a corrupt final HTML, PDF, or GIF with the requested name.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html import escape
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable, Mapping


BRIEFING_VERSION = 1
ANIMATION_MODES = frozenset({"forecast-timeline", "run-to-run"})


class BriefingExportError(RuntimeError):
    """A requested briefing artifact could not be produced safely."""


class ExportCancelled(BriefingExportError):
    """An export stopped cooperatively before the destination was replaced."""


def _utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time(value) -> str:
    value = _utc(value)
    return value.strftime("%Y-%m-%d %H:%MZ") if value is not None else "Unknown"


def _value(value) -> str:
    if value is None or value == "":
        return "Unknown"
    if isinstance(value, datetime):
        return _time(value)
    return str(value)


@dataclass(frozen=True)
class BriefingImage:
    label: str
    png_bytes: bytes
    caption: str = ""
    attribution: str = ""

    def __post_init__(self):
        if not bytes(self.png_bytes).startswith(b"\x89PNG\r\n\x1a\n"):
            raise BriefingExportError(f"{self.label or 'briefing image'} is not PNG")


@dataclass(frozen=True)
class BriefingSounding:
    label: str
    source: str
    run_time: datetime | None
    valid_time: datetime | None
    requested_point: str = "Unknown"
    selected_point: str = "Unknown"
    scenario: str | None = None
    ensemble_coverage: str | None = None
    image: BriefingImage | None = None


@dataclass(frozen=True)
class BriefingDocument:
    title: str
    generated_at: datetime
    soundings: tuple[BriefingSounding, ...]
    notes: str = ""
    map_images: tuple[BriefingImage, ...] = ()
    comparison_rows: tuple[Mapping[str, object], ...] = ()
    trend_rows: tuple[Mapping[str, object], ...] = ()
    context_rows: tuple[Mapping[str, object], ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        if not str(self.title).strip():
            raise BriefingExportError("briefing title must not be empty")
        object.__setattr__(self, "generated_at", _utc(self.generated_at))
        if not self.soundings:
            raise BriefingExportError("briefing needs at least one selected sounding")


@dataclass(frozen=True)
class AnimationFrame:
    """One displayed or explicitly missing frame in a fixed-scale GIF."""

    label: str
    png_bytes: bytes | None
    valid_time: datetime | None
    run_time: datetime | None = None
    forecast_hour: int | None = None
    missing_reason: str | None = None

    def __post_init__(self):
        if self.png_bytes is None and not str(self.missing_reason or "").strip():
            raise BriefingExportError("a missing animation frame needs a reason")
        if self.png_bytes is not None and not bytes(self.png_bytes).startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise BriefingExportError(f"animation frame {self.label!r} is not PNG")


def _image_html(image: BriefingImage) -> str:
    encoded = base64.b64encode(image.png_bytes).decode("ascii")
    caption = " · ".join(
        escape(value) for value in (image.caption, image.attribution) if value
    )
    return (
        '<figure><img src="data:image/png;base64,'
        + encoded
        + f'" alt="{escape(image.label)}">'
        + (f"<figcaption>{caption}</figcaption>" if caption else "")
        + "</figure>"
    )


def _mapping_table(title: str, rows: Iterable[Mapping[str, object]]) -> str:
    rows = tuple(dict(row) for row in rows)
    if not rows:
        return ""
    columns = tuple(dict.fromkeys(key for row in rows for key in row))
    header = "".join(f"<th>{escape(str(column))}</th>" for column in columns)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{escape(_value(row.get(column)))}</td>" for column in columns)
        + "</tr>"
        for row in rows
    )
    return f"<section><h2>{escape(title)}</h2><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></section>"


def build_briefing_html(document: BriefingDocument) -> str:
    """Return one offline HTML document with every image embedded."""
    sounding_cards = []
    for sounding in document.soundings:
        details = [
            ("Source", sounding.source),
            ("Run", _time(sounding.run_time)),
            ("Valid", _time(sounding.valid_time)),
            ("Requested point", sounding.requested_point),
            ("Selected point", sounding.selected_point),
        ]
        if sounding.scenario:
            details.append(("Scenario", f"{sounding.scenario} (hypothetical)"))
        if sounding.ensemble_coverage:
            details.append(("Ensemble coverage", sounding.ensemble_coverage))
        facts = "".join(
            f"<dt>{escape(label)}</dt><dd>{escape(_value(value))}</dd>"
            for label, value in details
        )
        image = _image_html(sounding.image) if sounding.image else (
            '<p class="missing">No sounding image was selected.</p>'
        )
        sounding_cards.append(
            f"<article><h2>{escape(sounding.label)}</h2><dl>{facts}</dl>{image}</article>"
        )
    maps = "".join(_image_html(image) for image in document.map_images)
    limitations = "".join(
        f"<li>{escape(str(item))}</li>" for item in document.limitations
    )
    notes = escape(document.notes).replace("\n", "<br>")
    return "".join(
        (
            "<!doctype html><html><head><meta charset=\"utf-8\">",
            f"<meta name=\"sharpmod-briefing-version\" content=\"{BRIEFING_VERSION}\">",
            "<style>",
            "body{font-family:'Space Grotesk','Segoe UI','DejaVu Sans',sans-serif;margin:2rem;color:#17202a;background:#fff}",
            "header{border-bottom:3px solid #2b6f96;margin-bottom:1.2rem}",
            "article,section{break-inside:avoid;margin:1.2rem 0;padding:1rem;border:1px solid #ccd6dd;border-radius:8px}",
            "img{max-width:100%;height:auto;display:block;margin:auto}",
            "dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1rem}",
            "dt{font-weight:700}dd{margin:0}table{border-collapse:collapse;width:100%}",
            "th,td{border:1px solid #ccd6dd;padding:.35rem;text-align:left}",
            "th{background:#eaf2f7}.missing{color:#8a3b12;font-weight:600}",
            "figcaption{font-size:.85rem;color:#52616b;margin-top:.4rem}",
            "</style></head><body>",
            f"<header><h1>{escape(document.title)}</h1><p>Generated {_time(document.generated_at)}</p></header>",
            "".join(sounding_cards),
            f"<section><h2>Map context</h2>{maps}</section>" if maps else "",
            _mapping_table("Comparison", document.comparison_rows),
            _mapping_table("Trends", document.trend_rows),
            _mapping_table("Environmental context", document.context_rows),
            f"<section><h2>Notes</h2><p>{notes}</p></section>" if notes else "",
            f"<section><h2>Limitations and missing data</h2><ul>{limitations}</ul></section>" if limitations else "",
            "<footer><p>Created by SHARPpy Reimagined. Times are UTC. Data provenance and attribution are retained above.</p></footer>",
            "</body></html>",
        )
    )


def _temporary(destination: Path, suffix: str):
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=suffix, dir=destination.parent
    )
    os.close(handle)
    return Path(name)


def _atomic_text(destination: Path, content: str) -> Path:
    temporary = _temporary(destination, ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def write_briefing_html(path, document: BriefingDocument) -> Path:
    return _atomic_text(Path(path), build_briefing_html(document))


def _pdf_image(image: BriefingImage) -> BriefingImage:
    """Return a page-sized copy of one image for Qt's HTML print engine.

    ``QTextDocument`` does not consistently apply CSS ``max-width`` to an
    image's intrinsic height while paginating.  A desktop-sized widget grab can
    therefore be narrowed horizontally but retain its original vertical size,
    splitting one sounding across pages.  Resize only the PDF copy of the
    already-rendered pixels; the self-contained HTML keeps the source PNG.
    """
    from PIL import Image

    source = Image.open(io.BytesIO(image.png_bytes))
    source.load()
    if source.width <= 720 and source.height <= 650:
        return image
    source.thumbnail((720, 650), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    source.save(output, format="PNG", optimize=True)
    return replace(image, png_bytes=output.getvalue())


def _pdf_document(document: BriefingDocument) -> BriefingDocument:
    return replace(
        document,
        soundings=tuple(
            replace(
                sounding,
                image=_pdf_image(sounding.image) if sounding.image else None,
            )
            for sounding in document.soundings
        ),
        map_images=tuple(_pdf_image(image) for image in document.map_images),
    )


def write_briefing_pdf(path, document: BriefingDocument) -> Path:
    """Render the same self-contained briefing HTML into a real PDF."""
    from qtpy.QtCore import QMarginsF
    from qtpy.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    from sharpmod.gui_theme import install_chrome_fonts, ui_font

    destination = Path(path)
    temporary = _temporary(destination, ".pdf")
    try:
        writer = QPdfWriter(str(temporary))
        writer.setTitle(document.title)
        writer.setCreator("SHARPpy Reimagined")
        writer.setPageLayout(
            QPageLayout(
                QPageSize(QPageSize.Letter),
                QPageLayout.Portrait,
                QMarginsF(12, 12, 12, 12),
                QPageLayout.Millimeter,
            )
        )
        install_chrome_fonts()
        text = QTextDocument()
        text.setDefaultFont(ui_font("body"))
        text.setDocumentMargin(20.0)
        text.setHtml(build_briefing_html(_pdf_document(document)))
        text.print_(writer)
        del writer
        with temporary.open("rb") as stream:
            signature = stream.read(5)
        if signature != b"%PDF-":
            raise BriefingExportError("Qt did not produce a valid PDF")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _frame_caption(frame: AnimationFrame, mode: str) -> str:
    if mode == "forecast-timeline":
        lead = (
            f" · F{int(frame.forecast_hour):03d}"
            if frame.forecast_hour is not None
            else ""
        )
        return f"{frame.label} · valid {_time(frame.valid_time)}{lead}"
    return (
        f"{frame.label} · run {_time(frame.run_time)} · "
        f"valid {_time(frame.valid_time)}"
    )


def write_animation_gif(
    path,
    frames: Iterable[AnimationFrame],
    *,
    mode: str,
    frame_duration_ms: int = 750,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write an ordered GIF, retaining explicit missing-frame cards."""
    if mode not in ANIMATION_MODES:
        raise BriefingExportError(f"unsupported animation mode {mode!r}")
    frames = tuple(frames)
    if not frames:
        raise BriefingExportError("animation needs at least one selected frame")
    duration = int(frame_duration_ms)
    if not 100 <= duration <= 10_000:
        raise BriefingExportError("frame duration must be between 100 and 10000 ms")
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise BriefingExportError("GIF export requires Pillow") from exc

    dimensions = None
    for frame in frames:
        if frame.png_bytes is None:
            continue
        try:
            with Image.open(io.BytesIO(frame.png_bytes)) as image:
                frame_dimensions = image.size
                image.verify()
        except Exception as exc:
            raise BriefingExportError(
                f"could not decode animation frame {frame.label!r}: {exc}"
            ) from exc
        if dimensions is None:
            dimensions = frame_dimensions
        elif frame_dimensions != dimensions:
            raise BriefingExportError(
                "animation frames have different dimensions; render them with "
                "one fixed visual scale"
            )
    if dimensions is None:
        dimensions = (960, 720)
    width, height = dimensions
    caption_height = 42
    output_frames = []
    for index, frame in enumerate(frames, start=1):
        if cancelled is not None and cancelled():
            raise ExportCancelled(
                f"animation cancelled after {len(output_frames)}/{len(frames)} frames"
            )
        canvas = Image.new("RGB", (width, height + caption_height), "white")
        if frame.png_bytes is None:
            draw = ImageDraw.Draw(canvas)
            message = f"Missing frame: {frame.missing_reason}"
            draw.rectangle((0, 0, width - 1, height - 1), outline="#a5491a", width=3)
            draw.text((20, max(20, height // 2)), message, fill="#7a2d0b")
        else:
            try:
                with Image.open(io.BytesIO(frame.png_bytes)) as source:
                    converted = source.convert("RGB")
                    try:
                        canvas.paste(converted, (0, 0))
                    finally:
                        converted.close()
            except Exception as exc:
                raise BriefingExportError(
                    f"could not decode animation frame {frame.label!r}: {exc}"
                ) from exc
            draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, height, width, height + caption_height), fill="#102a3a")
        draw.text((12, height + 13), _frame_caption(frame, mode), fill="white")
        # Pillow converts every RGB frame to an adaptive palette before GIF
        # encoding.  Doing that as each canvas is prepared keeps only one byte
        # per stored pixel instead of retaining a second full RGB/RGBA copy of
        # every selected frame until the final save.
        output_frames.append(canvas.convert("P", palette=Image.Palette.ADAPTIVE))
        canvas.close()
        if progress is not None:
            progress(index, len(frames))
    if cancelled is not None and cancelled():
        raise ExportCancelled("animation cancelled before final encoding")

    destination = Path(path)
    temporary = _temporary(destination, ".gif")
    manifest = {
        "format": "sharpmod-animation",
        "version": BRIEFING_VERSION,
        "mode": mode,
        "labels": [_frame_caption(frame, mode) for frame in frames],
        "missing": [frame.missing_reason for frame in frames],
    }
    try:
        first, *rest = output_frames
        first.save(
            temporary,
            format="GIF",
            save_all=True,
            append_images=rest,
            duration=duration,
            loop=0,
            optimize=False,
            comment=json.dumps(manifest, separators=(",", ":")).encode("utf-8"),
        )
        with temporary.open("rb") as stream:
            signature = stream.read(6)
        if signature not in {b"GIF87a", b"GIF89a"}:
            raise BriefingExportError("Pillow did not produce a valid GIF")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        for image in output_frames:
            image.close()
    return destination


__all__ = [
    "ANIMATION_MODES",
    "AnimationFrame",
    "BRIEFING_VERSION",
    "BriefingDocument",
    "BriefingExportError",
    "BriefingImage",
    "BriefingSounding",
    "ExportCancelled",
    "build_briefing_html",
    "write_animation_gif",
    "write_briefing_html",
    "write_briefing_pdf",
]
