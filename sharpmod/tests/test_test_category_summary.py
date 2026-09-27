"""Category jobs preserve fast-lane coverage and report real test failures."""

from __future__ import annotations

from scripts import summarize_test_categories as summary


def test_modules_have_one_stable_group():
    examples = {
        "test_viz_streamwiseness": "charts",
        "test_skewt_label_bounds": "charts",
        "test_gui_map_layers": "maps",
        "test_gui_box_picker": "maps",
        "test_gui_analysis_workspace": "analysis",
        "test_gui_timeline_playback": "analysis",
        "test_gui_chrome_theme": "interface",
        "test_gui_viewer_zoom": "interface",
        "test_render_label_plates": "exports",
        "test_packaging_render_smoke": "exports",
        "test_backend_equivalence": "platform",
        "test_model_sources": "providers",
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
        '<testcase classname="sharpmod.tests.test_gui_map_layers" name="later" time="0"><skipped/></testcase>'
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
    assert jobs["visualization"]["needs"] == "fast"
    categories = {
        "gui-area": ("fast", "interface"),
        "science-area": ("fast", "science"),
        "providers-area": ("fast", "providers"),
        "platform-area": ("fast", "platform"),
        "charts-area": ("visualization", "charts"),
        "maps-area": ("visualization", "maps"),
        "analysis-area": ("visualization", "analysis"),
        "exports-area": ("visualization", "exports"),
    }
    for name, (parent, group) in categories.items():
        job = jobs[name]
        assert job["needs"] == parent
        assert f"--group {group}" in job["steps"][-1]["run"]
    for job in (jobs["visualization"], *(jobs[name] for name in categories)):
        assert "junit_artifact_id" in job["if"]
        assert any(
            "fast-junit-python-3.13" == step.get("with", {}).get("name")
            for step in job["steps"]
        )
    assert jobs["visualization"]["outputs"]["junit_artifact_id"] == (
        "${{ steps.forward_report.outputs.junit_artifact_id }}"
    )
