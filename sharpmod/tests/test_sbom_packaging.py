"""Runtime SBOM graphs retain transitive/extra edges and reject missing inputs."""
from email.message import Message
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

SBOM = runpy.run_path(str(Path(__file__).resolve().parents[2] / "packaging/generate_sbom.py"))
INVENTORY = runpy.run_path(str(Path(__file__).resolve().parents[2] / "packaging/runtime_inventory.py"))


class Distribution:
    def __init__(self, name, requires=()):
        self.metadata = Message()
        self.metadata["Name"] = name
        self.metadata["License-Expression"] = "MIT"
        self.version = "1.0"
        self.requires = requires

    def read_text(self, _name):
        return None


def test_python_sbom_follows_runtime_extras_without_including_test_tools():
    distributions = [Distribution("sharpmod", ["runtime[fast]>=1", "optional; extra == 'era5'", "test-tool; extra == 'dev'"]),
                     Distribution("SHARPpy"), Distribution("sharpmod-rs"),
                     Distribution("runtime", ["accelerator; extra == 'fast'"]), Distribution("optional"), Distribution("accelerator")]
    components, edges, roots = SBOM["python_graph"](distributions)
    assert {c["name"] for c in components} == {d.metadata["Name"] for d in distributions}
    assert len(roots) == 3
    assert next(e for e in edges if e["ref"] == "pkg:pypi/runtime@1.0")["dependsOn"] == ["pkg:pypi/accelerator@1.0"]
    with pytest.raises(ValueError, match="Unsatisfied"):
        SBOM["python_graph"](distributions[:-1])


def test_cargo_sbom_keeps_only_normal_windows_graph_edges_and_locked_hashes():
    packages = [{"id": name, "name": name, "version": "1.0", "license": "MIT", "source": "registry"}
                for name in ("app", "runtime", "dev", "build")]
    graph = {"packages": packages, "resolve": {"root": "app", "nodes": [
        {"id": "app", "features": [], "deps": [{"pkg": name, "dep_kinds": [{"kind": kind}]} for name, kind in
                                              (("runtime", None), ("dev", "dev"), ("build", "build"))]},
        *({"id": name, "features": [], "deps": []} for name in ("runtime", "dev", "build"))]}}
    lock = {"package": [{**p, "checksum": "a" * 64} for p in packages]}
    components, edges, roots = SBOM["cargo_graph"](graph, lock)
    assert {c["name"] for c in components} == {"app", "runtime"}
    assert roots == ["pkg:cargo/app@1.0"]
    assert edges[0]["dependsOn"] == ["pkg:cargo/runtime@1.0"]
    assert all(c["hashes"][0]["content"] == "a" * 64 for c in components)
    bom = {"metadata": {"component": {"bom-ref": "root"}}, "components": components, "dependencies": edges}
    SBOM["validate_graph"](bom)
    edges[0]["dependsOn"].append("absent")
    with pytest.raises(ValueError, match="Dangling"):
        SBOM["validate_graph"](bom)


def test_windows_vex_checks_build_edges_without_hiding_linux_warnings():
    graph = {"packages": [{"id": "app", "name": "app", "version": "1"},
                          {"id": "glib", "name": "glib", "version": "0.18.5"}],
             "resolve": {"root": "app", "nodes": [{"id": "app", "deps": []}]}}
    findings = SBOM["windows_advisories"]([graph], "root")
    assert {f["id"] for f in findings} == {"RUSTSEC-2024-0429", "RUSTSEC-2024-0370"}
    assert all(f["analysis"]["justification"] == "code_not_present" for f in findings)
    graph["resolve"]["nodes"][0]["deps"].append({"pkg": "glib", "dep_kinds": [{"kind": "build"}]})
    with pytest.raises(ValueError, match="RustSec-affected"):
        SBOM["windows_advisories"]([graph], "root")


def test_shipped_inventory_hashes_files_links_owners_and_keeps_unknown_imports(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    internal = runtime / "backend/_internal"
    internal.mkdir(parents=True)
    (runtime / "app.exe").write_bytes(b"application")
    (internal / "math.dll").write_bytes(b"library")
    (internal / "font.ttf").write_bytes(b"font")
    globals_ = INVENTORY["runtime_inventory"].__globals__
    monkeypatch.setitem(globals_, "pe_details", lambda path: ({"FileVersion": "1.2"},
        ["math.dll", "api-ms-win-core-synch-l1-2-0.dll", "missing-sharppy-test.dll"]) if path.name == "app.exe" else ({}, []))
    monkeypatch.setattr(globals_["metadata"], "distribution", lambda _name: SimpleNamespace(files=["math.dll"]))
    owner = {"name": "owner", "purl": "pkg:pypi/owner@1", "bom-ref": "pkg:pypi/owner@1", "type": "library"}
    components, edges, assembly = INVENTORY["runtime_inventory"](runtime, [owner])
    files = [c for c in components if c["bom-ref"].startswith("file:")]
    assert len(files) == 3
    assert all(len(c["hashes"][0]["content"]) == 64 for c in files)
    assert next(c for c in files if c["name"] == "app.exe")["version"] == "1.2"
    assert next(e for e in edges if e["ref"].endswith("math.dll"))["dependsOn"] == [owner["bom-ref"]]
    unknown = next(c for c in components if c["name"] == "missing-sharppy-test.dll")
    assert {p["name"]: p["value"] for p in unknown["properties"]}["sharppy:provider"] == "unresolved"
    assert unknown["bom-ref"] not in next(e for e in edges if e["ref"] == "external:windows")["dependsOn"]
    SBOM["validate_graph"]({"metadata": {"component": {"bom-ref": "root"}},
                            "components": [owner, *components], "dependencies": edges})
    assert assembly == "sharppy:windows-runtime-files"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == runtime)
    with pytest.raises(ValueError, match="links/reparse"):
        INVENTORY["runtime_inventory"](runtime, [owner])
