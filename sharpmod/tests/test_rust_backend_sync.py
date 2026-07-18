"""Regression tests for the local Rust-backend synchronization command."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tomllib

import pytest

from sharpmod._version import __version__
from sharpmod.tools import rust_backend_sync as sync


def _verified_info():
    return {
        "requested_backend": "rust",
        "active_backend": "rust",
        "rust_installed": True,
        "rust_version": __version__,
        "fallback_reason": None,
    }


def test_pyproject_registers_the_sync_console_command():
    root = Path(__file__).resolve().parents[2]
    document = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8"))

    assert document["project"]["scripts"]["sharpmod-rust-sync"] == (
        "sharpmod.tools.rust_backend_sync:main"
    )


def test_current_extension_is_verified_without_rebuild(monkeypatch, capsys):
    monkeypatch.setattr(sync, "installed_rust_version", lambda: __version__)
    rebuilds = []
    monkeypatch.setattr(
        sync, "rebuild_rust_backend", lambda: rebuilds.append(True))
    monkeypatch.setattr(sync, "verify_rust_backend", _verified_info)

    assert sync.main([]) == 0
    assert rebuilds == []
    output = capsys.readouterr()
    assert f"sharpmod_rs {__version__} already matches" in output.out
    assert "active backend: rust" in output.out


@pytest.mark.parametrize("installed", [None, "0.3.1", "0.4.1"])
def test_missing_or_stale_extension_is_rebuilt(
    installed, monkeypatch, capsys,
):
    monkeypatch.setattr(sync, "installed_rust_version", lambda: installed)
    rebuilds = []
    monkeypatch.setattr(
        sync, "rebuild_rust_backend", lambda: rebuilds.append(True))
    monkeypatch.setattr(sync, "verify_rust_backend", _verified_info)

    assert sync.main([]) == 0
    assert rebuilds == [True]
    assert "rebuilding" in capsys.readouterr().out.lower()


def test_check_reports_stale_extension_without_rebuilding(monkeypatch, capsys):
    monkeypatch.setattr(sync, "installed_rust_version", lambda: "0.4.1")
    monkeypatch.setattr(
        sync,
        "rebuild_rust_backend",
        lambda: pytest.fail("--check must not rebuild"),
    )
    monkeypatch.setattr(
        sync,
        "verify_rust_backend",
        lambda: pytest.fail("a mismatched extension cannot be verified"),
    )

    assert sync.main(["--check"]) == 1
    error = capsys.readouterr().err
    assert "installed sharpmod_rs 0.4.1" in error
    assert f"expected {__version__}" in error


def test_check_reports_a_missing_extension(monkeypatch, capsys):
    monkeypatch.setattr(sync, "installed_rust_version", lambda: None)
    monkeypatch.setattr(
        sync,
        "rebuild_rust_backend",
        lambda: pytest.fail("--check must not rebuild"),
    )

    assert sync.main(["--check"]) == 1
    assert "sharpmod_rs is not installed" in capsys.readouterr().err


def test_force_rebuilds_a_current_extension(monkeypatch):
    monkeypatch.setattr(sync, "installed_rust_version", lambda: __version__)
    rebuilds = []
    monkeypatch.setattr(
        sync, "rebuild_rust_backend", lambda: rebuilds.append(True))
    monkeypatch.setattr(sync, "verify_rust_backend", _verified_info)

    assert sync.main(["--force"]) == 0
    assert rebuilds == [True]


def test_crate_root_requires_a_source_checkout(monkeypatch, tmp_path):
    fake_module = tmp_path / "sharpmod" / "tools" / "rust_backend_sync.py"
    monkeypatch.setattr(sync, "__file__", str(fake_module))

    with pytest.raises(sync.RustBackendSyncError, match="source checkout"):
        sync.rust_crate_root()


def test_crate_root_resolves_the_repository_crate():
    crate = sync.rust_crate_root()

    assert crate.name == "sharpmod-rs"
    assert (crate / "Cargo.toml").is_file()


def test_rebuild_requires_an_active_virtual_environment(monkeypatch):
    monkeypatch.setattr(sync.sys, "prefix", "C:/Python311")
    monkeypatch.setattr(sync.sys, "base_prefix", "C:/Python311")
    monkeypatch.delenv("CONDA_PREFIX", raising=False)

    with pytest.raises(
        sync.RustBackendSyncError,
        match="virtual or Conda environment",
    ):
        sync.rebuild_rust_backend()


def test_rebuild_accepts_an_active_conda_environment(monkeypatch):
    environment = Path("C:/Miniconda/envs/sharpmod").resolve()
    monkeypatch.setattr(sync.sys, "prefix", str(environment))
    monkeypatch.setattr(sync.sys, "base_prefix", str(environment))
    monkeypatch.setenv("CONDA_PREFIX", str(environment))

    assert sync._active_environment_root() == environment


def test_rebuild_uses_locked_release_maturin(monkeypatch, tmp_path):
    crate = tmp_path / "rust" / "sharpmod-rs"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    environment = tmp_path / ".venv"
    monkeypatch.setattr(sync, "rust_crate_root", lambda: crate)
    monkeypatch.setattr(sync.sys, "prefix", str(environment))
    monkeypatch.setattr(sync.sys, "base_prefix", str(tmp_path / "base"))
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(sync.subprocess, "run", fake_run)

    sync.rebuild_rust_backend()

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        sync.sys.executable,
        "-m",
        "maturin",
        "develop",
        "--release",
        "--locked",
    ]
    assert kwargs["cwd"] == crate
    assert kwargs["check"] is True
    assert kwargs["env"]["VIRTUAL_ENV"] == str(Path(environment).resolve())


def test_rebuild_translates_maturin_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "rust_crate_root", lambda: tmp_path)
    monkeypatch.setattr(sync.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(sync.sys, "base_prefix", str(tmp_path / "base"))

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(sync.subprocess, "run", fail)

    with pytest.raises(sync.RustBackendSyncError, match="exit code 7"):
        sync.rebuild_rust_backend()


def test_verifier_uses_a_fresh_forced_rust_process(monkeypatch):
    expected = _verified_info()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(expected) + "\n",
            stderr="",
        )

    monkeypatch.setattr(sync.subprocess, "run", fake_run)

    assert sync.verify_rust_backend() == expected
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:2] == [sync.sys.executable, "-c"]
    assert kwargs["env"]["SHARPMOD_BACKEND"] == "rust"
    assert kwargs["check"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_backend", "auto"),
        ("active_backend", "python"),
        ("rust_installed", False),
        ("rust_version", "0.4.1"),
        ("fallback_reason", "incompatible extension"),
    ],
)
def test_verifier_rejects_an_invalid_backend_report(
    field, value, monkeypatch,
):
    report = {**_verified_info(), field: value}

    monkeypatch.setattr(
        sync.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(report) + "\n",
            stderr="",
        ),
    )

    with pytest.raises(sync.RustBackendSyncError, match=field):
        sync.verify_rust_backend()


def test_verifier_translates_forced_rust_process_failure(monkeypatch):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            command,
            output="",
            stderr="extension could not load",
        )

    monkeypatch.setattr(sync.subprocess, "run", fail)

    with pytest.raises(
        sync.RustBackendSyncError, match="extension could not load",
    ):
        sync.verify_rust_backend()


def test_main_reports_rebuild_failure(monkeypatch, capsys):
    monkeypatch.setattr(sync, "installed_rust_version", lambda: None)

    def fail():
        raise sync.RustBackendSyncError("build failed")

    monkeypatch.setattr(sync, "rebuild_rust_backend", fail)

    assert sync.main([]) == 1
    assert "build failed" in capsys.readouterr().err
