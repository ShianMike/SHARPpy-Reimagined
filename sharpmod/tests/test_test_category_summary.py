"""Inline categories preserve fast-lane coverage and report real test failures."""

from __future__ import annotations

from scripts import summarize_test_categories as summary


def test_modules_have_one_stable_group():
    examples = {
        "test_viz_streamwiseness": "charts",
        "test_skewt_label_bounds": "charts",
        "test_map_geography": "maps",
        "test_animation_exports": "analysis",
        "test_timeline_frames": "analysis",
        "test_mounted_profile_refresh": "interface",
        "test_render_label_plates": "exports",
        "test_packaging_render_smoke": "exports",
        "test_box_export": "exports",
        "test_backend_equivalence": "platform",
        "test_bridge_protocol": "platform",
        "test_grib_backend_equivalence": "platform",
        "test_model_fetch_packaging": "platform",
        "test_test_category_summary": "platform",
        "test_model_sources": "providers",
        "test_place_names": "maps",
        "test_derived_dcp_property": "science",
        "test_igra2_reader": "providers",
    }
    assert {module: summary.category_for_module(module) for module in examples} == examples


def test_report_marks_a_failed_category_and_escapes_names(tmp_path, monkeypatch):
    junit = tmp_path / "fast.xml"
    junit.write_text(
        '<testsuites><testsuite name="pytest" tests="3" time="3.0">'
        '<testcase classname="sharpmod.tests.test_viz_streamwiseness" name="ok" time="1" />'
        '<testcase classname="sharpmod.tests.test_viz_streamwiseness" name="bad|case" time="2"><failure/></testcase>'
        '<testcase classname="sharpmod.tests.test_map_geography" name="later" time="0"><skipped/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    path = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    assert summary.main([str(junit), "--group", "visualization"]) == 1
    text = path.read_text(encoding="utf-8")
    assert "Data visualization — FAIL" in text
    assert "3 tests** · 1 passed · 1 failed · 1 skipped" in text
    assert "bad&#124;case" in text
    assert "| Charts & soundings | 1 | 1 | 0 | 2 |" in text
    assert "| Maps & overlays | 0 | 0 | 1 | 1 |" in text
    assert "<details>" in text
    assert summary.main([str(junit), "--group", "interface"]) == 1
    assert "No tests matched" in path.read_text(encoding="utf-8")


def test_workflow_displays_every_test_group():
    import pytest

    yaml = pytest.importorskip("yaml")
    workflow = yaml.load(
        (summary.Path(__file__).resolve().parents[2] / ".github/workflows/tests.yml").read_text(
            encoding="utf-8"
        ),
        Loader=yaml.BaseLoader,
    )
    jobs = workflow["jobs"]
    fast = jobs["fast"]
    assert "outputs" not in fast
    reports = [
        (name, step)
        for name, job in jobs.items()
        for step in job["steps"]
        if "scripts/summarize_test_categories.py" in step.get("run", "")
    ]
    assert len(reports) == 1
    owner, report = reports[0]
    assert owner == "fast"
    assert report["if"] == (
        "${{ always() && !cancelled() && hashFiles('.test-results/fast.xml') != '' }}"
    )
    assert report["env"]["SOURCE_RESULT"] == "${{ steps.fast_tests.outcome }}"
    assert any(step.get("id") == "fast_tests" for step in fast["steps"])
    loop = next(line.strip() for line in report["run"].splitlines() if "for group in" in line)
    assert set(loop.removeprefix("for group in ").removesuffix("; do").split()) == set(summary.GROUPS)
    assert '--group "$group" --source-result "$SOURCE_RESULT" || status=1' in report["run"]
    assert 'exit "$status"' in report["run"]
    assert not any(name == "visualization" or name.endswith("-area") for name in jobs)
    uploads = [step for step in fast["steps"] if "upload-artifact@" in step.get("uses", "")]
    assert len(uploads) == 1
    assert ".test-results/fast*" in uploads[0]["with"]["path"]
