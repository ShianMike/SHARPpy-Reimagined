"""Self-contained briefing, PDF, and atomic GIF export contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json

from PIL import Image
import pytest

from sharpmod.briefing_exports import (
    AnimationFrame,
    BriefingDocument,
    BriefingExportError,
    BriefingImage,
    BriefingSounding,
    ExportCancelled,
    _pdf_document,
    build_briefing_html,
    write_animation_gif,
    write_briefing_html,
    write_briefing_pdf,
)


VALID = datetime(2026, 9, 13, 0, tzinfo=timezone.utc)


def _png(colour="navy", size=(80, 60)):
    image = Image.new("RGB", size, colour)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _document():
    sounding_image = BriefingImage(
        "HRRR sounding",
        _png(),
        "Selected sounding",
        "NOAA/NCEP HRRR",
    )
    return BriefingDocument(
        "KOUN Convective Briefing",
        VALID,
        (
            BriefingSounding(
                "HRRR — KOUN",
                "HRRR",
                VALID - timedelta(hours=6),
                VALID,
                "35.20, -97.44",
                "35.19, -97.45",
                scenario="Warm sector",
                ensemble_coverage="24/31 loaded; 22 usable for SRH",
                image=sounding_image,
            ),
        ),
        notes="Watch the cap.\nHypothetical scenario, not an observation.",
        map_images=(BriefingImage("Radar map", _png("green"), attribution="NWS"),),
        comparison_rows=({"Sounding": "HRRR", "MLCAPE": 1500},),
        trend_rows=({"Valid": VALID, "MLCAPE": 1500},),
        context_rows=({"GOES IR": "2026-09-13 00:00Z", "Age": "0 min"},),
        limitations=("One ensemble member lacks 0-1 km wind coverage.",),
    )


def test_html_is_self_contained_and_keeps_analysis_provenance(tmp_path):
    document = _document()
    html = build_briefing_html(document)
    path = write_briefing_html(tmp_path / "briefing.html", document)

    assert path.read_text(encoding="utf-8") == html
    assert "data:image/png;base64," in html
    assert "src=\"http" not in html
    assert "24/31 loaded; 22 usable for SRH" in html
    assert "Warm sector (hypothetical)" in html
    assert "NOAA/NCEP HRRR" in html
    assert "2026-09-13 00:00Z" in html
    assert not list(tmp_path.glob("*.tmp"))


def test_pdf_contains_real_pdf_bytes(qt_app, tmp_path):
    path = write_briefing_pdf(tmp_path / "briefing.pdf", _document())

    payload = path.read_bytes()
    assert payload.startswith(b"%PDF-")
    assert len(payload) > 1000
    assert not list(tmp_path.glob("*.pdf.*"))


def test_pdf_copy_bounds_desktop_images_without_changing_html_source():
    document = _document()
    source = BriefingImage("large", _png(size=(1600, 1000)))
    document = BriefingDocument(
        document.title,
        document.generated_at,
        (BriefingSounding("large", "fixture", VALID, VALID, image=source),),
    )

    bounded = _pdf_document(document)

    with Image.open(io.BytesIO(bounded.soundings[0].image.png_bytes)) as image:
        assert image.width <= 720
        assert image.height <= 650
        assert image.size == (720, 450)
    with Image.open(io.BytesIO(source.png_bytes)) as image:
        assert image.size == (1600, 1000)


def test_gif_order_mode_labels_and_missing_frame_are_embedded(tmp_path):
    frames = (
        AnimationFrame("F000", _png("red"), VALID, VALID, 0),
        AnimationFrame(
            "F003",
            None,
            VALID + timedelta(hours=3),
            VALID,
            3,
            "provider returned no sounding",
        ),
        AnimationFrame(
            "F006", _png("blue"), VALID + timedelta(hours=6), VALID, 6
        ),
    )
    progress = []
    path = write_animation_gif(
        tmp_path / "timeline.gif",
        frames,
        mode="forecast-timeline",
        frame_duration_ms=400,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert path.read_bytes().startswith(b"GIF89a")
    with Image.open(path) as image:
        assert image.n_frames == 3
        manifest = json.loads(image.info["comment"].decode("utf-8"))
    assert manifest["mode"] == "forecast-timeline"
    assert [label.split(" · ")[0] for label in manifest["labels"]] == [
        "F000",
        "F003",
        "F006",
    ]
    assert manifest["missing"][1] == "provider returned no sounding"
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_gif_stores_prepared_frames_as_palettes(monkeypatch, tmp_path):
    frames = (
        AnimationFrame("F000", _png("red", size=(320, 240)), VALID),
        AnimationFrame("F001", _png("blue", size=(320, 240)), VALID),
    )
    original = Image.Image.save
    captured_modes = []

    def capture_modes(image, fp, *args, **kwargs):
        if kwargs.get("format") == "GIF":
            appended = tuple(kwargs.get("append_images", ()))
            captured_modes.append((image.mode, *(item.mode for item in appended)))
        return original(image, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", capture_modes)

    write_animation_gif(
        tmp_path / "palette.gif",
        frames,
        mode="forecast-timeline",
    )

    assert captured_modes == [("P", "P")]


def test_run_to_run_labels_name_both_run_and_valid_time(tmp_path):
    frames = (
        AnimationFrame("Newest", _png(), VALID, VALID - timedelta(hours=3), 3),
        AnimationFrame("Older", _png(), VALID, VALID - timedelta(hours=6), 6),
    )
    path = write_animation_gif(
        tmp_path / "runs.gif", frames, mode="run-to-run"
    )
    with Image.open(path) as image:
        manifest = json.loads(image.info["comment"].decode("utf-8"))
    assert "run 2026-09-12 21:00Z" in manifest["labels"][0]
    assert all("valid 2026-09-13 00:00Z" in item for item in manifest["labels"])


def test_cancelled_gif_leaves_existing_destination_unchanged(tmp_path):
    destination = tmp_path / "timeline.gif"
    destination.write_bytes(b"prior complete artifact")
    calls = []

    with pytest.raises(ExportCancelled, match="1/2"):
        write_animation_gif(
            destination,
            (
                AnimationFrame("one", _png(), VALID),
                AnimationFrame("two", _png(), VALID + timedelta(hours=1)),
            ),
            mode="forecast-timeline",
            cancelled=lambda: len(calls) >= 1,
            progress=lambda *_args: calls.append(True),
        )

    assert destination.read_bytes() == b"prior complete artifact"
    assert not list(tmp_path.glob("*.gif.*"))


def test_fixed_scale_contract_rejects_mixed_frame_sizes(tmp_path):
    with pytest.raises(BriefingExportError, match="different dimensions"):
        write_animation_gif(
            tmp_path / "bad.gif",
            (
                AnimationFrame("small", _png(size=(40, 30)), VALID),
                AnimationFrame("large", _png(size=(80, 60)), VALID),
            ),
            mode="forecast-timeline",
        )
