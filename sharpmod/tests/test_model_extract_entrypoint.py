"""Fresh-process coverage for the forecast-model extractor entry point."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("args", "exit_code", "message"),
    [
        (["--help"], 0, "Extract public forecast-model point soundings to .npz"),
        (["--list"], 0, "Supported forecast models:"),
        ([], 2, "model is required unless --list is used"),
        (["hrrr"], 2, "lat and lon are required for extraction"),
    ],
)
def test_module_entrypoint(args, exit_code, message):
    result = subprocess.run(
        [sys.executable, "-m", "sharpmod.tools.model_extract", *args],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == exit_code, result.stderr
    assert message in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_library_import_uses_canonical_facade():
    from sharpmod.tools import model_extract, model_extract_cli, model_extract_config

    assert model_extract.main is model_extract_cli.main
    assert isinstance(
        model_extract.get_config("hrrr"), model_extract_config.ModelConfig
    )
