"""Tests for the Google Cloud Batch deployment planner.

Nothing here contacts Google Cloud. ``gcloud`` is replaced by a fake, and every
mutating path is exercised only in its dry-run form, so the suite can never
enable an API, create a bucket, submit a job, or incur cost.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "infra" / "gcp" / "toi-batch" / "toi_batch.py"
)


def _load_planner():
    spec = importlib.util.spec_from_file_location("toi_batch_planner", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = _load_planner()


@pytest.fixture(autouse=True)
def _no_real_gcloud(monkeypatch):
    """Fail loudly if any test tries to shell out to a real gcloud."""

    def guard(*args, **kwargs):  # pragma: no cover - only on a regression
        raise AssertionError(f"test attempted a real subprocess: {args!r}")

    monkeypatch.setattr(planner.subprocess, "run", guard)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project-123")


def _catalog(events: int = 8) -> dict:
    return {
        "target_definition": "high_risk_worthy_proxy_v1",
        "dataset_kind": "historical",
        "cases": [
            {
                "event_id": f"event-{index:02d}",
                "case_class": "outbreak" if index % 3 == 0 else "severe",
                "run_time": f"2019-05-{index + 1:02d}T06:00:00+00:00",
                "forecast_hour": 6,
                "latitude": 39.0,
                "longitude": -98.0,
                "anchor_source": "model_forecast_maximum_stp",
                "observed": {"tornado_count": index},
            }
            for index in range(events)
        ],
    }


# --------------------------------------------------------------------------
# Configuration never hardcodes the project
# --------------------------------------------------------------------------


def test_project_is_resolved_from_environment_not_source(monkeypatch):
    assert planner.resolve_project(None) == "test-project-123"
    assert planner.resolve_project("explicit-project") == "explicit-project"

    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(
        planner.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": "", "stderr": "", "returncode": 0})(),
    )
    with pytest.raises(planner.PlannerError, match="no project resolved"):
        planner.resolve_project(None)


def test_no_project_or_billing_account_is_embedded_in_source():
    text = _MODULE_PATH.read_text(encoding="utf-8")

    # The live project id and any billing-account form must not appear.
    assert "project-ab691722" not in text
    assert "billingAccounts/" not in text


def test_config_rejects_reuse_of_protected_existing_buckets():
    for suffix in planner.PROTECTED_BUCKET_SUFFIXES:
        with pytest.raises(planner.PlannerError, match="protected existing bucket"):
            planner.BatchConfig(project="p", bucket=f"someproject{suffix}")


def test_config_defaults_are_the_documented_cost_bounded_choices():
    config = planner.BatchConfig(project="p")

    assert config.region == "us-east1"
    assert config.machine_type == "e2-standard-4"
    assert config.provisioning_model == "SPOT"
    assert config.boot_disk_gib == 50
    assert config.boot_disk_type == "pd-balanced"
    assert config.task_count == 4
    assert config.parallelism == 1
    assert config.labels["app"] == "sharppy"
    assert config.labels["workload"] == "toi-archive"
    assert config.default_bucket == "p-toi-archive"


def test_config_refuses_unvalidated_parallelism():
    assert planner.BatchConfig(project="p", parallelism=2).parallelism == 2

    with pytest.raises(planner.PlannerError, match="parallelism above 2"):
        planner.BatchConfig(project="p", parallelism=3)
    with pytest.raises(planner.PlannerError, match="cannot exceed task_count"):
        planner.BatchConfig(project="p", task_count=1, parallelism=2)


def test_config_round_trips_through_json(tmp_path):
    config = planner.BatchConfig(project="p", region="us-central1", task_count=2)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.to_mapping()), encoding="utf-8")

    assert planner.BatchConfig.load(path) == config


# --------------------------------------------------------------------------
# Source bundle
# --------------------------------------------------------------------------


def test_bundle_uses_an_allow_list_and_excludes_history_and_secrets():
    members = planner.bundle_members(planner.REPO_ROOT)
    relative = {
        path.relative_to(planner.REPO_ROOT).as_posix() for path in members
    }

    assert relative, "bundle must not be empty"
    # Everything is inside a declared include root.
    assert all(planner._included_root(item) for item in relative)
    # None of the large or sensitive trees leak in.
    for forbidden in (".git/", ".gribenv/", ".griblenv/", "rust/", "archive/", "dist/"):
        assert not any(item.startswith(forbidden) for item in relative), forbidden
    assert not any(item.endswith(".pyc") for item in relative)
    assert not any("credential" in item.casefold() for item in relative)
    assert not any(item.endswith(".env") for item in relative)
    # The land-domain resource the job needs is present.
    assert "sharpmod/resources/conus-counties.zip" in relative
    assert "pyproject.toml" in relative
    total = sum(path.stat().st_size for path in members)
    assert total < 64 * 1024**2, "source bundle should stay small"


def test_bundle_excludes_cache_directories_at_any_depth():
    """Regression: ``.hypothesis/*`` is root-anchored, so a nested cache such as
    ``sharpmod/.hypothesis`` shipped 43 local example-database files until the
    exclusion was also applied component-wise."""
    for leaked in (
        "sharpmod/.hypothesis/constants/0dfc3528cfd096ab",
        "sharpmod/.hypothesis/unicode_data/14.0.0/charmap.json.gz",
        "sharpmod/tests/__pycache__/test_x.cpython-311.pyc",
        "scripts/.ruff_cache/content",
        "sharpmod/.pytest_cache/v/cache/lastfailed",
        "sharpmod/nested/.mypy_cache/3.11/builtins.data.json",
    ):
        assert planner._excluded(leaked), leaked

    # A real source file that merely lives deep in the tree is still included.
    assert not planner._excluded("sharpmod/guidance/toi_archive.py")
    assert not planner._excluded("sharpmod/resources/conus-counties.zip")

    members = {
        path.relative_to(planner.REPO_ROOT).as_posix()
        for path in planner.bundle_members(planner.REPO_ROOT)
    }
    assert not any(".hypothesis" in item for item in members)
    assert not any("__pycache__" in item for item in members)


def test_bundle_dry_run_records_git_provenance_without_building(tmp_path, monkeypatch):
    monkeypatch.setattr(
        planner,
        "git_provenance",
        lambda root: {
            "head": "a" * 40,
            "branch": "main",
            "dirty": True,
            "dirty_file_count": 3,
            "dirty_files_sample": ["a.py"],
        },
    )
    record = planner.build_bundle(
        planner.REPO_ROOT, tmp_path / "bundle.tar.gz", dry_run=True
    )

    assert record["dry_run"] is True
    assert record["sha256"] is None
    assert not (tmp_path / "bundle.tar.gz").exists()
    assert record["git"]["head"] == "a" * 40
    assert record["git"]["dirty"] is True
    assert "dirty_files_sample" in record["git"]
    assert "include_roots" in record


def test_bundle_build_records_a_real_sha256(tmp_path, monkeypatch):
    monkeypatch.setattr(
        planner,
        "git_provenance",
        lambda root: {
            "head": "",
            "branch": "",
            "dirty": False,
            "dirty_file_count": 0,
            "dirty_files_sample": [],
        },
    )
    root = tmp_path / "repo"
    (root / "sharpmod").mkdir(parents=True)
    (root / "sharpmod" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    destination = tmp_path / "out" / "bundle.tar.gz"
    record = planner.build_bundle(root, destination, dry_run=False)

    assert destination.exists()
    assert len(record["sha256"]) == 64
    assert record["bundle_bytes"] > 0


def test_bundle_refuses_credential_like_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        planner,
        "git_provenance",
        lambda root: {
            "head": "",
            "branch": "",
            "dirty": False,
            "dirty_file_count": 0,
            "dirty_files_sample": [],
        },
    )
    root = tmp_path / "repo"
    (root / "sharpmod").mkdir(parents=True)
    (root / "sharpmod" / "service-account-key.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(planner, "BUNDLE_EXCLUDE_GLOBS", ())

    with pytest.raises(planner.PlannerError, match="credential-like"):
        planner.build_bundle(root, tmp_path / "b.tar.gz", dry_run=True)


# --------------------------------------------------------------------------
# Event-indivisible sharding
# --------------------------------------------------------------------------


def test_sharding_keeps_every_event_in_exactly_one_shard():
    catalog = _catalog(24)
    # Two cycles per event: they must never be separated.
    catalog["cases"] += [dict(case, forecast_hour=12) for case in catalog["cases"]]

    shards = planner.shard_catalog(catalog, shards=4)

    assert len(shards) == 4
    assert sum(item["case_count"] for item in shards) == len(catalog["cases"])
    placement: dict[str, str] = {}
    for shard in shards:
        for case in shard["cases"]:
            event_id = case["event_id"]
            placement.setdefault(event_id, shard["shard_id"])
            assert placement[event_id] == shard["shard_id"]
    assert len(placement) == 24


def test_sharding_is_deterministic_and_order_independent():
    catalog = _catalog(16)
    reversed_catalog = dict(catalog, cases=list(reversed(catalog["cases"])))

    first = planner.shard_catalog(catalog, shards=4)
    second = planner.shard_catalog(reversed_catalog, shards=4)

    assert [item["event_ids"] for item in first] == [
        item["event_ids"] for item in second
    ]


def test_sharding_gives_each_shard_an_isolated_workdir_and_prefix():
    shards = planner.shard_catalog(_catalog(12), shards=4)

    work_dirs = {item["work_dir"] for item in shards}
    prefixes = {item["gcs_prefix"] for item in shards}
    assert len(work_dirs) == 4
    assert len(prefixes) == 4
    assert all(item["gcs_prefix"].startswith("shards/") for item in shards)


def test_sharding_rejects_empty_shards_and_missing_event_ids():
    with pytest.raises(planner.PlannerError, match="empty shard"):
        planner.shard_catalog(_catalog(2), shards=8)
    with pytest.raises(planner.PlannerError, match="needs an event_id"):
        planner.shard_catalog({"cases": [{"forecast_hour": 6}]}, shards=1)
    with pytest.raises(planner.PlannerError, match="no cases"):
        planner.shard_catalog({"cases": []}, shards=1)


# --------------------------------------------------------------------------
# Merge verification
# --------------------------------------------------------------------------


def _shard_report(shard_id: str, cases: list[tuple[str, str]], **overrides) -> dict:
    payload = {
        "shard_id": shard_id,
        "plan_hash": "planhash",
        "target_definition": "high_risk_worthy_proxy_v1",
        "feature_method_version": "sharpmod_hrrr_toi_experimental_v3",
        "cases": [
            {
                "cache_key": key,
                "event_id": event,
                "scientific_content_sha256": "s" * 64,
            }
            for key, event in cases
        ],
    }
    payload.update(overrides)
    return payload


def test_merge_verify_accepts_disjoint_complete_shards():
    reports = [
        _shard_report("shard-00", [("k1", "e1"), ("k2", "e2")]),
        _shard_report("shard-01", [("k3", "e3")]),
    ]

    result = planner.verify_merge(reports, expected_events=["e1", "e2", "e3"])

    assert result["verified"] is True
    assert result["unique_cases"] == 3
    assert result["unique_events"] == 3
    assert result["failures"] == []


def test_merge_verify_rejects_duplicates_overlaps_and_missing_events():
    duplicate = planner.verify_merge(
        [
            _shard_report("shard-00", [("k1", "e1")]),
            _shard_report("shard-01", [("k1", "e9")]),
        ]
    )
    split = planner.verify_merge(
        [
            _shard_report("shard-00", [("k1", "e1")]),
            _shard_report("shard-01", [("k2", "e1")]),
        ]
    )
    missing = planner.verify_merge(
        [_shard_report("shard-00", [("k1", "e1")])],
        expected_events=["e1", "e2"],
    )
    unexpected = planner.verify_merge(
        [_shard_report("shard-00", [("k1", "e1"), ("k2", "rogue")])],
        expected_events=["e1"],
    )

    assert any(f["check"] == "duplicate_case" for f in duplicate["failures"])
    assert any(
        f["check"] == "event_split_across_shards" for f in split["failures"]
    )
    assert any(f["check"] == "missing_expected_event" for f in missing["failures"])
    assert any(f["check"] == "unexpected_event" for f in unexpected["failures"])
    for result in (duplicate, split, missing, unexpected):
        assert result["verified"] is False


def test_merge_verify_rejects_inconsistent_plans_and_missing_hashes():
    inconsistent = planner.verify_merge(
        [
            _shard_report("shard-00", [("k1", "e1")]),
            _shard_report("shard-01", [("k2", "e2")], plan_hash="different"),
        ]
    )
    unhashed = planner.verify_merge(
        [
            {
                "shard_id": "shard-00",
                "plan_hash": "p",
                "cases": [{"cache_key": "k1", "event_id": "e1"}],
            }
        ]
    )

    assert any(
        f["check"] == "inconsistent_plan_hash" for f in inconsistent["failures"]
    )
    assert any(
        f["check"] == "missing_scientific_hash" for f in unhashed["failures"]
    )


# --------------------------------------------------------------------------
# Budgets and rendering
# --------------------------------------------------------------------------


def test_projection_matches_the_measured_pilot_rates():
    config = planner.BatchConfig(project="p", task_count=4, parallelism=1)

    usage = planner.project_usage(
        config, total_cases=600, mib_per_case=73.4, seconds_per_case=89.3
    )

    assert usage["inbound_transfer_gib"] == pytest.approx(43.0, abs=0.2)
    assert usage["wall_hours_total_serial"] == pytest.approx(14.9, abs=0.2)
    assert usage["cases_per_task"] == 150
    assert usage["vcpu_hours"] > 0
    # Raw GRIB is discarded, so retained output stays tiny.
    assert usage["retained_output_mib"] < 5
    assert usage["peak_raw_disk_mib"] < config.boot_disk_gib * 1024


def test_budget_guard_blocks_a_run_that_exceeds_job_side_caps():
    config = planner.BatchConfig(
        project="p",
        task_count=1,
        parallelism=1,
        max_cases_per_task=10,
        max_input_gib_per_task=1,
        max_task_wall_seconds=600,
    )
    usage = planner.project_usage(
        config, total_cases=600, mib_per_case=73.4, seconds_per_case=89.3
    )

    problems = planner.budget_guard(config, usage)

    assert any("cases per task" in item for item in problems)
    assert any("inbound transfer per task" in item for item in problems)
    assert any("wall time" in item for item in problems)


def test_rendered_job_is_a_script_runnable_with_spot_retry():
    config = planner.BatchConfig(project="p")

    job = planner.render_job(config, script_object="out/task-script.sh")
    group = job["taskGroups"][0]
    spec = group["taskSpec"]

    # Script runnable, never a container image: no registry is required.
    assert "script" in spec["runnables"][0]
    assert "container" not in spec["runnables"][0]
    assert json.dumps(job).find("gcr.io") == -1
    assert json.dumps(job).find("pkg.dev") == -1
    assert group["taskCount"] == 4
    assert group["parallelism"] == 1
    assert spec["maxRetryCount"] == 3
    assert spec["maxRunDuration"] == "86400s"
    assert spec["computeResource"]["bootDiskMib"] == 50 * 1024
    policy = job["allocationPolicy"]["instances"][0]["policy"]
    assert policy["provisioningModel"] == "SPOT"
    assert policy["machineType"] == "e2-standard-4"
    assert policy["bootDisk"]["type"] == "pd-balanced"
    assert job["logsPolicy"]["destination"] == "CLOUD_LOGGING"
    assert job["labels"] == {"app": "sharppy", "workload": "toi-archive"}
    # Spot preemption retries the task rather than failing the job.
    assert spec["lifecyclePolicies"][0]["action"] == "RETRY_TASK"


def test_task_script_restores_state_and_never_uploads_raw_grib():
    config = planner.BatchConfig(project="p")

    script = planner.render_task_script(config)

    assert script.startswith("#!/usr/bin/env bash")
    assert "set -Eeuo pipefail" in script
    # Resume before continuing.
    assert "restoring checkpoint" in script
    assert "checkpoint.jsonl" in script
    # Each Batch task fetches its own catalogue instead of repeating shard 00.
    assert 'shards/${SHARD_ID}.json' in script
    assert "shards/shard-00.json" not in script
    # A preempted VM's stale lock must not wedge the retry.
    assert "rm -f" in script and "run.lock" in script
    # Explicit upload plus checksum verification, not FUSE rename semantics.
    assert "sha256sum" in script
    assert "gcsfuse" not in script
    # Raw GRIB is local-only and removed.
    assert 'rm -rf "${RAW}"' in script
    assert "NEVER uploaded" in script
    # Job-side hard caps are passed to the runner.
    assert "--max-cases" in script
    assert "--max-transfer-gib" in script
    assert "--max-seconds" in script
    assert "--min-free-gib" in script
    # Completed cases are mirrored during the archive process, not only after
    # a potentially day-long subprocess exits.
    assert "MIRROR_INTERVAL_SECONDS=300" in script
    assert 'while kill -0 "${ARCHIVE_PID}"' in script
    assert "periodic_mirror &" in script
    mirror_start = script.index("periodic_mirror &")
    assert mirror_start < script.index('wait "${ARCHIVE_PID}"', mirror_start)
    assert "archive_interrupted" in script
    # No container build anywhere.
    assert "docker" not in script.lower()
    assert "artifactregistry" not in script.lower()


def test_rendered_commands_gate_every_mutation_behind_an_explicit_flag():
    config = planner.BatchConfig(project="p")

    commands = planner.render_commands(
        config, job_name="job", job_path="job.json", script_path="s.sh"
    )
    by_step = {item["step"]: item for item in commands}

    assert by_step["enable-apis"]["requires"] == "--confirm-enable-apis"
    assert by_step["create-dedicated-bucket"]["requires"] == "--confirm-create-bucket"
    assert by_step["submit-job"]["requires"] == "--confirm-submit"
    assert by_step["cleanup-temp-prefixes"]["requires"] == "--confirm-delete"
    assert by_step["watch-status"]["requires"].startswith("none")
    # The dedicated bucket, never an existing one.
    assert "p-toi-archive" in by_step["create-dedicated-bucket"]["command"]
    for item in commands:
        for suffix in planner.PROTECTED_BUCKET_SUFFIXES:
            assert suffix not in item["command"]


def test_lifecycle_deletes_temp_prefixes_and_retains_final_artifacts():
    lifecycle = planner.render_lifecycle()

    ages = {rule["condition"]["age"] for rule in lifecycle["rule"]}
    assert 14 in ages
    prefixes = [
        prefix
        for rule in lifecycle["rule"]
        for prefix in rule["condition"]["matchesPrefix"]
    ]
    assert any("shards/" in prefix for prefix in prefixes)
    assert all("final/" not in prefix for prefix in prefixes)
    assert "final/" in lifecycle["retained_forever_note"]


# --------------------------------------------------------------------------
# Commands are dry-run by default
# --------------------------------------------------------------------------


def test_plan_command_writes_artifacts_and_executes_nothing(tmp_path, capsys):
    exit_code = planner.main(
        [
            "--project",
            "test-project-123",
            "plan",
            "--out-dir",
            str(tmp_path),
            "--total-cases",
            "600",
            "--shards",
            "4",
            "--parallelism",
            "1",
        ]
    )
    output = capsys.readouterr().out
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "DRY RUN ONLY" in output
    assert plan["dry_run"] is True
    assert plan["executed"] is False
    assert plan["apis_enabled"] is False
    assert plan["job_submitted"] is False
    assert plan["budget_violations"] == []
    created = {item["type"] for item in plan["resources_that_would_be_created"]}
    assert {"storage_bucket", "batch_job", "google_project_service"} <= created
    assert plan["retry_and_preemption"]["raw_grib_uploaded"] is False
    assert plan["remaining_user_confirmations"]
    assert (tmp_path / "job.json").exists()
    assert (tmp_path / "task-script.sh").exists()
    assert (tmp_path / "lifecycle.json").exists()
    assert (tmp_path / "commands.json").exists()


def test_plan_command_fails_when_budgets_are_exceeded(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            planner.BatchConfig(
                project="p", task_count=1, max_cases_per_task=5
            ).to_mapping()
        ),
        encoding="utf-8",
    )

    exit_code = planner.main(
        ["plan", "--config", str(config_path), "--out-dir", str(tmp_path)]
    )
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))

    assert exit_code == 1
    assert plan["budget_violations"]


def test_submit_requires_explicit_confirmation(capsys):
    assert planner.main(["submit"]) == 0
    assert "requires --confirm-submit" in capsys.readouterr().out

    # Even with the flag this pass refuses; permission is never inferred.
    assert planner.main(["submit", "--confirm-submit"]) == 2


def test_cleanup_is_dry_run_and_refuses_protected_buckets(tmp_path, capsys):
    exit_code = planner.main(
        [
            "--project",
            "test-project-123",
            "cleanup",
            "--output",
            str(tmp_path / "cleanup.json"),
        ]
    )
    inventory = json.loads((tmp_path / "cleanup.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "DRY-RUN" in capsys.readouterr().out
    assert inventory["dry_run"] is True
    assert inventory["deleted"] == []
    assert any("shards/" in item for item in inventory["would_delete_prefixes"])
    assert any("final/" in item for item in inventory["would_retain"])

    with pytest.raises(planner.PlannerError, match="protected bucket"):
        planner.cmd_cleanup(
            type(
                "A",
                (),
                {
                    "project": "p",
                    "bucket": "p_cloudbuild",
                    "run_prefix": "toi-archive",
                    "output": str(tmp_path / "x.json"),
                    "confirm_delete": False,
                },
            )()
        )


def test_preflight_is_read_only_and_reports_blockers(tmp_path, capsys):
    exit_code = planner.main(
        [
            "--project",
            "test-project-123",
            "preflight",
            "--offline",
            "--output",
            str(tmp_path / "preflight.json"),
        ]
    )
    report = json.loads((tmp_path / "preflight.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["read_only"] is True
    assert report["mutations_performed"] == []
    assert "no mutations were performed" in capsys.readouterr().out


def test_offline_preflight_never_reports_ready_to_submit(tmp_path):
    """Regression: readiness was computed from missing APIs alone, so an offline
    run that made no gcloud calls reported ``ready_to_submit: true`` while also
    listing a blocker. Unobserved state must never imply permission."""
    planner.main(
        [
            "--project",
            "test-project-123",
            "preflight",
            "--offline",
            "--output",
            str(tmp_path / "offline.json"),
        ]
    )
    report = json.loads((tmp_path / "offline.json").read_text(encoding="utf-8"))

    assert report["offline"] is True
    assert report["ready_to_submit"] is False
    assert report["blockers"], "offline preflight must name its blockers"
    assert any("--offline" in blocker for blocker in report["blockers"])
    # Readiness is exactly the absence of blockers, never a subset of them.
    assert report["ready_to_submit"] == (not report["blockers"])


def test_preflight_ready_only_when_no_blocker_remains(tmp_path, monkeypatch):
    def fake_gcloud(args, timeout=60):
        if args[0] == "services":
            return [
                {"config": {"name": name}}
                for name in (*planner.REQUIRED_APIS, planner.STORAGE_API)
            ]
        return [{"name": "test-project-123-toi-archive"}]

    monkeypatch.setattr(planner, "_gcloud_json", fake_gcloud)
    planner.main(
        [
            "--project",
            "test-project-123",
            "preflight",
            "--output",
            str(tmp_path / "p.json"),
        ]
    )
    report = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))

    assert report["services"]["required_missing"] == []
    assert report["buckets"]["dedicated_bucket_exists"] is True
    assert report["blockers"] == []
    assert report["ready_to_submit"] is True


def test_preflight_flags_missing_batch_and_compute_apis(tmp_path, monkeypatch):
    def fake_gcloud(args, timeout=60):
        if args[0] == "services":
            return [{"config": {"name": planner.STORAGE_API}}]
        return [{"name": "test-project-123-modelforecastpy-cache"}]

    monkeypatch.setattr(planner, "_gcloud_json", fake_gcloud)
    planner.main(
        [
            "--project",
            "test-project-123",
            "preflight",
            "--output",
            str(tmp_path / "p.json"),
        ]
    )
    report = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))

    assert report["services"]["storage_enabled"] is True
    assert set(report["services"]["required_missing"]) == set(planner.REQUIRED_APIS)
    assert report["ready_to_submit"] is False
    # The unrelated existing bucket is listed as protected, never reused.
    assert report["buckets"]["protected_untouched"] == [
        "test-project-123-modelforecastpy-cache"
    ]
    assert report["buckets"]["dedicated_bucket_exists"] is False


def test_shard_command_writes_per_shard_catalogues(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(_catalog(12)), encoding="utf-8")

    exit_code = planner.main(
        [
            "shard",
            "--catalog",
            str(catalog_path),
            "--shards",
            "4",
            "--out-dir",
            str(tmp_path / "shards"),
        ]
    )

    assert exit_code == 0
    summary = json.loads(
        (tmp_path / "shards" / "shard-summary.json").read_text(encoding="utf-8")
    )
    assert len(summary["shards"]) == 4
    for entry in summary["shards"]:
        payload = json.loads(
            (tmp_path / "shards" / f"{entry['shard_id']}.json").read_text("utf-8")
        )
        assert payload["target_definition"] == "high_risk_worthy_proxy_v1"
        assert len(payload["cases"]) == entry["case_count"]


def test_verify_command_exits_nonzero_on_merge_failure(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "a.json").write_text(
        json.dumps(_shard_report("shard-00", [("k1", "e1")])), encoding="utf-8"
    )
    (reports / "b.json").write_text(
        json.dumps(_shard_report("shard-01", [("k1", "e2")])), encoding="utf-8"
    )

    exit_code = planner.main(
        [
            "verify",
            "--reports-dir",
            str(reports),
            "--output",
            str(tmp_path / "merge.json"),
        ]
    )

    assert exit_code == 1
    result = json.loads((tmp_path / "merge.json").read_text(encoding="utf-8"))
    assert result["verified"] is False
