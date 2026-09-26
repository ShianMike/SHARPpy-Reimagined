"""Atomic chosen-size, explicit-gap GIF animation exports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable


ANIMATION_VERSION = 1
ANIMATION_MODES = frozenset({"forecast-timeline", "run-to-run"})
GAP_POLICIES = frozenset({"cards", "stop", "error"})


class AnimationExportError(RuntimeError):
    """A requested animation could not be produced safely."""


class ExportCancelled(AnimationExportError):
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


@dataclass(frozen=True)
class AnimationFrame:
    """One displayed or explicitly missing frame in a GIF."""

    label: str
    png_bytes: bytes | None
    valid_time: datetime | None
    run_time: datetime | None = None
    forecast_hour: int | None = None
    missing_reason: str | None = None

    def __post_init__(self):
        if self.png_bytes is None and not str(self.missing_reason or "").strip():
            raise AnimationExportError("a missing animation frame needs a reason")
        if self.png_bytes is not None and not bytes(self.png_bytes).startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise AnimationExportError(
                f"animation frame {self.label!r} is not PNG"
            )


def _temporary(destination: Path, suffix: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=suffix, dir=destination.parent
    )
    os.close(handle)
    return Path(name)


def _frame_caption(frame: AnimationFrame, mode: str) -> str:
    if mode == "forecast-timeline":
        lead = (
            f" · F{int(frame.forecast_hour):03d}"
            if frame.forecast_hour is not None
            and str(frame.label).strip().upper() != f"F{int(frame.forecast_hour):03d}"
            else ""
        )
        return f"{frame.label} · valid {_time(frame.valid_time)}{lead}"
    return (
        f"{frame.label} · run {_time(frame.run_time)} · "
        f"valid {_time(frame.valid_time)}"
    )


def _continuity_time(frame: AnimationFrame, mode: str) -> datetime | None:
    return _utc(frame.valid_time if mode == "forecast-timeline" else frame.run_time)


def _gap_frame(
    previous: AnimationFrame,
    following: AnimationFrame,
    *,
    mode: str,
    expected_interval_seconds: float,
) -> AnimationFrame:
    before = _continuity_time(previous, mode)
    after = _continuity_time(following, mode)
    missing_time = before + timedelta(seconds=expected_interval_seconds)
    reason = (
        f"time discontinuity from {_time(before)} to {_time(after)}; "
        "the displayed step exceeds the smallest source interval; "
        "cadence may change or intermediate times may be unselected"
    )
    if mode == "forecast-timeline":
        lead = None
        if previous.forecast_hour is not None:
            lead = int(
                round(
                    previous.forecast_hour
                    + expected_interval_seconds / 3600.0
                )
            )
        return AnimationFrame(
            "Time gap",
            None,
            missing_time,
            previous.run_time,
            lead,
            reason,
        )
    return AnimationFrame(
        "Run gap",
        None,
        following.valid_time,
        missing_time,
        None,
        reason,
    )


def prepare_animation_frames(
    frames: Iterable[AnimationFrame],
    *,
    mode: str,
    gap_policy: str = "cards",
    expected_interval_seconds: float | None = None,
) -> tuple[AnimationFrame, ...]:
    """Apply an explicit missing/discontinuity policy before rendering.

    There is intentionally no plain "skip" policy: joining times on either side
    of a gap without a visible marker would imply continuity that is not in the
    source data.
    """
    if mode not in ANIMATION_MODES:
        raise AnimationExportError(f"unsupported animation mode {mode!r}")
    policy = str(gap_policy or "cards").strip().lower()
    if policy not in GAP_POLICIES:
        raise AnimationExportError(
            f"unsupported gap policy {gap_policy!r}; expected cards, stop, or error"
        )
    source = tuple(frames)
    if not source:
        raise AnimationExportError("animation needs at least one selected frame")
    expected = None
    if expected_interval_seconds is not None:
        try:
            expected = float(expected_interval_seconds)
        except (TypeError, ValueError) as exc:
            raise AnimationExportError("expected frame interval must be seconds") from exc
        if expected <= 0:
            raise AnimationExportError("expected frame interval must be positive")

    expanded: list[AnimationFrame] = []
    previous = None
    for frame in source:
        if previous is not None and expected is not None:
            before = _continuity_time(previous, mode)
            after = _continuity_time(frame, mode)
            if (
                before is not None
                and after is not None
                and (after - before).total_seconds() > expected * 1.5
            ):
                expanded.append(
                    _gap_frame(
                        previous,
                        frame,
                        mode=mode,
                        expected_interval_seconds=expected,
                    )
                )
        expanded.append(frame)
        previous = frame

    missing = [item for item in expanded if item.png_bytes is None]
    if policy == "error" and missing:
        first = missing[0]
        raise AnimationExportError(
            f"gap policy is Fail export and frame {first.label!r} is missing: "
            f"{first.missing_reason}"
        )
    if policy == "stop":
        result = []
        for item in expanded:
            if item.png_bytes is None:
                break
            result.append(item)
        if not result:
            first = expanded[0]
            raise AnimationExportError(
                "gap policy is Stop before first gap, but the first frame is "
                f"missing: {first.missing_reason}"
            )
        return tuple(result)
    return tuple(expanded)


def _palette(theme: str) -> tuple[str, str, str, str, str]:
    if str(theme).lower() == "light":
        return "#f4f1eb", "#ffffff", "#1a1815", "#fff2e8", "#9a3e13"
    return "#0d0d0c", "#1f1e1b", "#f1efeb", "#2b1810", "#ffb27d"


def _font(size, ImageFont):
    for path in ("C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(path, size=max(10, int(size)))
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text, font, max_width):
    words = str(text).split()
    if not words:
        return ""
    result = " ".join(words)
    while len(result) > 1 and draw.textlength(result, font=font) > max_width:
        result = result[:-2].rstrip() + "…"
    return result


def _source_dimensions(frames, Image):
    dimensions = None
    for frame in frames:
        if frame.png_bytes is None:
            continue
        try:
            with Image.open(io.BytesIO(frame.png_bytes)) as image:
                frame_dimensions = image.size
                image.verify()
        except Exception as exc:
            raise AnimationExportError(
                f"could not decode animation frame {frame.label!r}: {exc}"
            ) from exc
        if dimensions is None:
            dimensions = frame_dimensions
        elif frame_dimensions != dimensions:
            raise AnimationExportError(
                "animation frames have different dimensions; render them with "
                "one fixed visual scale"
            )
    return dimensions or (960, 720)


def _render_frame_image(
    frame: AnimationFrame,
    *,
    mode: str,
    dimensions: tuple[int, int],
    source_dimensions: tuple[int, int],
    theme: str,
    context: str,
    Image,
    ImageDraw,
    ImageFont,
):
    width, total_height = dimensions
    caption_height = max(38, min(64, int(round(total_height * 0.065))))
    content_height = max(1, total_height - caption_height)
    background, caption_background, foreground, gap_background, gap_foreground = _palette(theme)
    canvas = Image.new("RGB", (width, total_height), background)
    draw = ImageDraw.Draw(canvas)
    if frame.png_bytes is None:
        gap_font = _font(min(22, max(12, width / 75)), ImageFont)
        draw.rectangle((0, 0, width - 1, content_height - 1), fill=gap_background,
                       outline=gap_foreground, width=max(2, width // 600))
        message = f"Gap: {frame.missing_reason}"
        draw.text(
            (max(16, width // 50), max(20, content_height // 2)),
            _fit_text(draw, message, gap_font, max(1, width - max(32, width // 25))),
            fill=gap_foreground, font=gap_font,
        )
    else:
        try:
            with Image.open(io.BytesIO(frame.png_bytes)) as source:
                converted = source.convert("RGB")
                try:
                    ratio = min(width / converted.width, content_height / converted.height)
                    target = (
                        max(1, int(round(converted.width * ratio))),
                        max(1, int(round(converted.height * ratio))),
                    )
                    if target != converted.size:
                        resized = converted.resize(target, Image.Resampling.LANCZOS)
                    else:
                        resized = converted.copy()
                    try:
                        canvas.paste(
                            resized,
                            ((width - target[0]) // 2, (content_height - target[1]) // 2),
                        )
                    finally:
                        resized.close()
                finally:
                    converted.close()
        except Exception as exc:
            raise AnimationExportError(
                f"could not decode animation frame {frame.label!r}: {exc}"
            ) from exc
    draw.rectangle((0, content_height, width, total_height), fill=caption_background)
    caption_font = _font(max(12, min(22, caption_height * 0.34)), ImageFont)
    caption = _frame_caption(frame, mode)
    if context:
        caption = f"{context} · {caption}"
    draw.text((12, content_height + max(10, caption_height // 3)),
              _fit_text(draw, caption, caption_font, max(1, width - 24)),
              fill=foreground, font=caption_font)
    return canvas


def animation_frame_preview_png(
    frame: AnimationFrame,
    *,
    mode: str,
    output_size: tuple[int, int] | None = None,
    theme: str = "dark",
    context: str = "",
) -> bytes:
    """Render one frame through the same composition used by the GIF writer."""
    if mode not in ANIMATION_MODES:
        raise AnimationExportError(f"unsupported animation mode {mode!r}")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise AnimationExportError("GIF export requires Pillow") from exc
    source = _source_dimensions((frame,), Image)
    dimensions = output_size or (source[0], source[1] + 42)
    dimensions = (int(dimensions[0]), int(dimensions[1]))
    if dimensions[0] < 1 or dimensions[1] < 80:
        raise AnimationExportError("animation output dimensions are too small")
    image = _render_frame_image(
        frame,
        mode=mode,
        dimensions=dimensions,
        source_dimensions=source,
        theme=theme,
        context=str(context or ""),
        Image=Image,
        ImageDraw=ImageDraw,
        ImageFont=ImageFont,
    )
    stream = io.BytesIO()
    try:
        image.save(stream, format="PNG")
    finally:
        image.close()
    return stream.getvalue()


def write_animation_gif(
    path,
    frames: Iterable[AnimationFrame],
    *,
    mode: str,
    frame_duration_ms: int = 750,
    output_size: tuple[int, int] | None = None,
    gap_policy: str = "cards",
    expected_interval_seconds: float | None = None,
    theme: str = "dark",
    context: str = "",
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write an ordered GIF with exact dimensions and an explicit gap policy."""
    source_frames = tuple(frames)
    frames = prepare_animation_frames(
        source_frames,
        mode=mode,
        gap_policy=gap_policy,
        expected_interval_seconds=expected_interval_seconds,
    )
    duration = int(frame_duration_ms)
    if not 100 <= duration <= 10_000:
        raise AnimationExportError(
            "frame duration must be between 100 and 10000 ms"
        )
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise AnimationExportError("GIF export requires Pillow") from exc

    source_dimensions = _source_dimensions(source_frames, Image)
    if output_size is None:
        dimensions = (source_dimensions[0], source_dimensions[1] + 42)
    else:
        try:
            dimensions = (int(output_size[0]), int(output_size[1]))
        except (IndexError, TypeError, ValueError) as exc:
            raise AnimationExportError("animation output size must be width and height") from exc
        if dimensions[0] < 1 or dimensions[1] < 80:
            raise AnimationExportError("animation output dimensions are too small")
        if dimensions[0] * dimensions[1] > 12_000_000:
            raise AnimationExportError("animation dimensions exceed 12 million pixels")
    output_frames = []
    try:
        for index, frame in enumerate(frames, start=1):
            if cancelled is not None and cancelled():
                raise ExportCancelled(
                    f"animation cancelled after {len(output_frames)}/{len(frames)} frames"
                )
            canvas = _render_frame_image(
                frame,
                mode=mode,
                dimensions=dimensions,
                source_dimensions=source_dimensions,
                theme=theme,
                context=str(context or ""),
                Image=Image,
                ImageDraw=ImageDraw,
                ImageFont=ImageFont,
            )
            try:
                output_frames.append(canvas.convert("P", palette=Image.Palette.ADAPTIVE))
            finally:
                canvas.close()
            if progress is not None:
                progress(index, len(frames))
        if cancelled is not None and cancelled():
            raise ExportCancelled("animation cancelled before final encoding")

        destination = Path(path)
        temporary = _temporary(destination, ".gif")
        manifest = {
            "format": "sharpmod-animation",
            "version": ANIMATION_VERSION,
            "mode": mode,
            "dimensions": list(dimensions),
            "theme": theme,
            "context": str(context or ""),
            "gap_policy": gap_policy,
            "expected_interval_seconds": expected_interval_seconds,
            "source_frame_count": len(source_frames),
            "output_frame_count": len(frames),
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
                raise AnimationExportError("Pillow did not produce a valid GIF")
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
    "ANIMATION_VERSION",
    "GAP_POLICIES",
    "AnimationExportError",
    "AnimationFrame",
    "ExportCancelled",
    "animation_frame_preview_png",
    "prepare_animation_frames",
    "write_animation_gif",
]
