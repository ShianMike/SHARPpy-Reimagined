"""Runtime SBOM graphs retain transitive/extra edges and reject missing inputs."""
from email.message import Message
from pathlib import Path
import runpy

import pytest

SBOM = runpy.run_path(str(Path(__file__).resolve().parents[2] / "packaging/generate_sbom.py"))


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
