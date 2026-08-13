"""Windows release packaging must be version-consistent and unambiguous."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

from sharpmod._version import __version__ as package_version


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "packaging" / "release_contract.py"


def _load_contract():
    spec = importlib.util.spec_from_file_location(
        "_sharpmod_release_contract_tests", CONTRACT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()


class _FakeDistribution:
    def __init__(self, version: str, metadata_path: Path):
        self.version = version
        self.metadata = {"Name": "sharpmod", "Version": version}
        self._path = metadata_path


def _version_root(tmp_path: Path, version: str = "0.8.1") -> Path:
    root = tmp_path / "repo"
    package = root / "sharpmod"
    package.mkdir(parents=True)
    (package / "_version.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    return root


def _workflow() -> dict:
    yaml = pytest.importorskip(
        "yaml", reason="PyYAML is required only for workflow structure checks"
    )
    return yaml.load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_release_contract_reads_source_and_maps_pe_version():
    version = CONTRACT.read_source_version(ROOT)

    assert version == package_version
    assert CONTRACT.pe_version_tuple("0.8.1") == (0, 8, 1, 0)
    assert CONTRACT.pe_version_tuple("12.34.56.7rc1") == (12, 34, 56, 7)
    with pytest.raises(CONTRACT.ReleaseContractError, match="MAJOR.MINOR.PATCH"):
        CONTRACT.pe_version_tuple("1.2")
    assert CONTRACT.is_sharpmod_metadata_destination("sharpmod-0.8.1.dist-info")
    assert CONTRACT.is_sharpmod_metadata_destination("sharpmod.egg-info")
    assert not CONTRACT.is_sharpmod_metadata_destination("sharpmod_rs-0.8.1.dist-info")


def test_release_contract_rejects_mismatched_installed_metadata(tmp_path, monkeypatch):
    root = _version_root(tmp_path)
    metadata_path = tmp_path / "site" / "sharpmod-0.7.9.dist-info"
    metadata_path.mkdir(parents=True)
    distribution = _FakeDistribution("0.7.9", metadata_path)
    monkeypatch.setattr(
        CONTRACT.importlib_metadata,
        "distribution",
        lambda _name: distribution,
    )

    with pytest.raises(CONTRACT.ReleaseContractError, match="versions do not match"):
        CONTRACT.validate_installed_sharpmod(root)


def test_official_contract_requires_external_wheel_metadata(tmp_path, monkeypatch):
    root = _version_root(tmp_path)
    in_tree = root / "sharpmod-0.8.1.dist-info"
    in_tree.mkdir()
    distribution = _FakeDistribution("0.8.1", in_tree)
    monkeypatch.setattr(
        CONTRACT.importlib_metadata,
        "distribution",
        lambda _name: distribution,
    )
    with pytest.raises(CONTRACT.ReleaseContractError, match="outside"):
        CONTRACT.validate_installed_sharpmod(
            root,
            require_dist_info=True,
            require_external_metadata=True,
        )

    external_egg = tmp_path / "site" / "sharpmod.egg-info"
    external_egg.mkdir(parents=True)
    distribution._path = external_egg
    with pytest.raises(CONTRACT.ReleaseContractError, match="wheel-style"):
        CONTRACT.validate_installed_sharpmod(root, require_dist_info=True)

    external_dist = tmp_path / "site" / "sharpmod-0.8.1.dist-info"
    external_dist.mkdir()
    distribution._path = external_dist
    report = CONTRACT.validate_installed_sharpmod(
        root,
        require_dist_info=True,
        require_external_metadata=True,
    )
    assert report["source_version"] == "0.8.1"
    assert report["metadata_format"] == "dist-info"
    assert report["metadata_path"] == str(external_dist.resolve())


def test_pyinstaller_uses_validated_metadata_and_source_pe_version():
    spec = (ROOT / "packaging" / "sharpmod_gui.spec").read_text(encoding="utf-8")

    assert 'os.environ.get("SHARPMOD_RELEASE_BUILD", "0") == "1"' in spec
    assert 'run_path(os.path.join(SPECPATH, "release_contract.py"))' in spec
    assert "validate_installed_sharpmod" in spec
    assert "require_dist_info=RELEASE_BUILD" in spec
    assert "require_external_metadata=RELEASE_BUILD" in spec
    assert 'copy_metadata("sharpmod")' in spec
    assert "is_sharpmod_metadata_destination" in spec
    assert "datas += _SHARPMOD_METADATA" in spec
    assert spec.count("version=_WINDOWS_VERSION_INFO") == 2
    assert "noarchive=False" in spec
    assert "append_pkg=False" in spec
    assert spec.count("upx=False") == 3
    assert "upx=True" not in spec


def test_frozen_launcher_reports_every_version_and_uses_lazy_picker_entrypoint():
    launcher = (ROOT / "packaging" / "sharpmod_gui_launcher.py").read_text(
        encoding="utf-8"
    )

    assert launcher.count("from sharpmod.gui_picker import main") == 2
    assert "from sharpmod.gui import main" not in launcher
    for key in (
        '"sharpmod"',
        '"sharpmod_metadata"',
        '"sharpmod_rs"',
        '"sharpmod_rs_metadata"',
        '"backend_rust"',
    ):
        assert key in launcher
    assert "version_consistent=True" in launcher
    assert "len(set(runtime_versions.values())) != 1" in launcher


def test_release_install_is_clean_and_artifacts_are_clearly_prioritized():
    workflow = _workflow()
    job = workflow["jobs"]["build-windows-exe"]
    steps = job["steps"]
    by_name = {step.get("name"): step for step in steps}

    assert job["env"]["SHARPMOD_RELEASE_BUILD"] == "1"
    cleanup = by_name["Clean generated package metadata and build outputs"]["run"]
    assert "egg|dist" in cleanup
    assert "Remove-Item" in cleanup
    install = by_name["Install constrained package and build dependencies"]["run"]
    assert '".[render,era5,wrf]"' in install
    assert '-e ".[render,era5,wrf]"' not in install
    metadata_check = by_name["Verify canonical installed package metadata"]["run"]
    assert "--require-dist-info" in metadata_check
    assert "--require-external-metadata" in metadata_check

    assert job["permissions"]["actions"] == "read"
    assert "attestations" not in job["permissions"]
    assert "id-token" not in job["permissions"]

    provider = by_name["Select Windows signing provider"]
    assert provider["id"] == "signing-provider"
    provider_script = provider["run"]
    assert "WINDOWS_SIGNING_CERTIFICATE_BASE64" in str(provider)
    assert "WINDOWS_SIGNING_CERTIFICATE_PASSWORD" in str(provider)
    assert "SIGNPATH_API_TOKEN" in str(provider)
    assert "SIGNPATH_ARTIFACT_CONFIGURATION_SLUG" in str(provider)
    assert "exactly one Windows signing provider" in provider_script

    pfx_signing = by_name["Sign Windows executables with PFX"]
    assert "signtool.exe" in pfx_signing["run"]
    assert "steps.signing-provider.outputs.provider == 'PFX'" in pfx_signing["if"]

    signpath = by_name["Submit SignPath signing request"]
    assert signpath["uses"].startswith(
        "signpath/github-action-submit-signing-request@"
    )
    assert signpath["with"]["github-artifact-id"].endswith(
        ".outputs.artifact-id }}"
    )
    assert signpath["with"]["wait-for-completion"] == "true"
    assert signpath["with"]["parameters"].strip().startswith("version:")

    signing = by_name["Record Windows signing result"]
    assert signing["id"] == "signing"
    assert "mode=$mode" in signing["run"]
    assert "provider=$provider" in signing["run"]

    verification_names = {
        "Verify recommended one-folder artifact",
        "Verify portable single-file artifact",
    }
    assert verification_names <= set(by_name)
    for name in verification_names:
        script = by_name[name]["run"]
        assert "verify_windows_artifact.ps1" in script
        assert "version_consistent" in script
        assert "sharpmod_metadata" in script
        assert "sharpmod_rs_metadata" in script

    stage = by_name["Stage clearly labeled release artifacts"]["run"]
    assert "windows-x64.zip" in stage
    assert "windows-x64-portable.exe" in stage
    assert "windows-x64-RECOMMENDED.zip" not in stage
    assert "portable-slower-startup.exe" not in stage
    assert "recommended_artifact" in stage
    assert "authenticode" in stage
    assert "signing_provider" in stage
    assert "Authenticode-$env:SIGNING_MODE.txt" in stage
    assert "SHA256SUMS" in stage

    attestation_job = workflow["jobs"]["attest-windows-release"]
    assert attestation_job["permissions"]["attestations"] == "write"
    assert attestation_job["permissions"]["id-token"] == "write"
    attestation_by_name = {
        step.get("name"): step for step in attestation_job["steps"]
    }
    attestation = attestation_by_name["Attest tested release artifacts"]
    assert attestation["uses"].startswith("actions/attest@")
    assert attestation["with"]["subject-path"] == "release-artifacts/*"

    publish_steps = workflow["jobs"]["publish"]["steps"]
    publish_by_name = {step.get("name"): step for step in publish_steps}
    publish = publish_by_name["Publish GitHub Release"]["with"]
    assert "Recommended Windows download" in publish["body"]
    assert "portable.exe" in publish["body"]
    assert "windows-x64-RECOMMENDED.zip" not in publish["body"]
    assert "portable-slower-startup.exe" not in publish["body"]
    assert "Code signing policy" in publish["body"]
    assert "gh attestation verify" in publish["body"]
    assert publish["overwrite_files"] == "true"
    stale_cleanup = publish_by_name["Remove stale release assets"]["run"]
    assert "windows-x64-RECOMMENDED.zip" in stale_cleanup
    assert "portable-slower-startup.exe" in stale_cleanup
    assert "Authenticode-Unsigned.txt" in stale_cleanup
    assert "Authenticode-Signed.txt" in stale_cleanup
    assert "gh release view" in stale_cleanup
    assert "grep -Fxq" in stale_cleanup
    assert "|| true" not in stale_cleanup


def test_signpath_policy_and_artifact_configuration_are_constrained():
    policy = (ROOT / "CODE_SIGNING_POLICY.md").read_text(encoding="utf-8")
    assert "# Code signing policy" in policy
    assert "Free code signing provided by" in policy
    assert "certificate by" in policy
    assert "Authors and committers" in policy
    assert "Signing approver" in policy
    assert "no advertising, analytics, or telemetry" in policy

    config_path = ROOT / "packaging" / "signpath-artifact-configuration.xml"
    tree = ET.parse(config_path)
    namespace = {"sp": "http://signpath.io/artifact-configuration/v1"}
    parameter = tree.find("sp:parameters/sp:parameter", namespace)
    assert parameter is not None
    assert parameter.attrib == {"name": "version", "required": "true"}

    pe_set = tree.find("sp:zip-file/sp:pe-file-set", namespace)
    assert pe_set is not None
    assert pe_set.attrib["product-name"] == "SHARPpy Reimagined"
    assert pe_set.attrib["product-version"] == "${version}"
    assert pe_set.attrib["file-version"] == "${version}"
    includes = pe_set.findall("sp:include", namespace)
    assert [include.attrib["path"] for include in includes] == [
        "recommended/SHARPpy-Reimagined.exe",
        "portable/SHARPpy-Reimagined.exe",
    ]
    assert pe_set.find("sp:for-each/sp:authenticode-sign", namespace) is not None


def test_windows_artifact_check_requires_pe_versions_and_explicit_signing_state():
    verifier = (ROOT / "packaging" / "verify_windows_artifact.ps1").read_text(
        encoding="utf-8"
    )

    assert "$versionInfo.FileVersion -ne $ExpectedVersion" in verifier
    assert "$versionInfo.ProductVersion -ne $ExpectedVersion" in verifier
    assert 'ValidateSet("Signed", "Unsigned")' in verifier
    assert "Get-AuthenticodeSignature" in verifier
    assert '$signature.Status -ne "Valid"' in verifier
    assert '$signature.Status -ne "NotSigned"' in verifier
