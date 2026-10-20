"""Inventory resolved Windows runtime graphs without installing new tooling.

Run in the packaging environment after npm ci and native wheel installation.
Cargo's Windows normal dependency edges, npm's production graph and installed
Python runtime requirements are retained. This is a resolved-graph SBOM, not
an assertion that every package's native sub-library has been identified.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import runpy
import shutil
import subprocess
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def python_graph(distributions=None):
    installed = {canonical(dist.metadata["Name"]): dist for dist in
                 (metadata.distributions() if distributions is None else distributions)}
    roots = {"sharpmod": {"era5", "wrf", "render"}, "sharppy": set(), "sharpmod-rs": set()}
    pending = list(roots.items())
    seen, edges = {}, {}
    while pending:
        name, extras = pending.pop()
        if name in seen and extras <= seen[name]:
            continue
        if name not in installed:
            raise ValueError(f"Runtime dependency is not installed: {name}")
        seen.setdefault(name, set()).update(extras)
        edges.setdefault(name, set())
        for text in installed[name].requires or ():
            requirement = Requirement(text)
            if requirement.marker and not any(requirement.marker.evaluate({"extra": extra})
                                              for extra in seen[name] | {""}):
                continue
            child = canonical(requirement.name)
            if child not in installed or installed[child].version not in requirement.specifier:
                raise ValueError(f"Unsatisfied runtime dependency of {name}: {requirement}")
            edges[name].add(child)
            pending.append((child, set(requirement.extras)))

    refs = {name: f"pkg:pypi/{quote(name)}@{quote(installed[name].version, safe='')}" for name in seen}
    components = []
    for name in sorted(seen):
        dist = installed[name]
        component = {"type": "library", "bom-ref": refs[name], "purl": refs[name],
                     "name": dist.metadata["Name"], "version": dist.version, "scope": "required",
                     "properties": [{"name": "sharppy:ecosystem", "value": "python-runtime"}]}
        expression = dist.metadata.get("License-Expression")
        license_name = dist.metadata.get("License")
        classifiers = dist.metadata.get_all("Classifier") or []
        if expression:
            component["licenses"] = [{"expression": expression}]
        elif license_name and license_name.strip().upper() not in ("UNKNOWN", ""):
            component["licenses"] = [{"license": {"name": license_name.strip().splitlines()[0][:200],
                "text": {"contentType": "text/plain", "content": license_name}}}]
        else:
            names = [value.rsplit(" :: ", 1)[-1] for value in classifiers if value.startswith("License ::")]
            if names:
                component["licenses"] = [{"license": {"name": value}} for value in names]
        text = dist.read_text("METADATA")
        if text:
            component["properties"].append({"name": "sharppy:installed-metadata-sha256",
                                             "value": hashlib.sha256(text.encode()).hexdigest()})
        references = []
        for value in dist.metadata.get_all("Project-URL") or []:
            label, _, url = value.partition(",")
            if url.strip().startswith("https://"):
                references.append({"type": "vcs" if label.lower() in ("source", "repository") else "website", "url": url.strip()})
        if references:
            component["externalReferences"] = references
        components.append(component)
    dependencies = [{"ref": refs[name], "dependsOn": sorted(refs[child] for child in edges[name])} for name in sorted(seen)]
    return components, dependencies, [refs[name] for name in roots]


def cargo_graph(graph, lock):
    packages = {package["id"]: package for package in graph["packages"]}
    nodes = {node["id"]: node for node in graph["resolve"]["nodes"]}
    root = graph["resolve"]["root"]
    pending, edges = [root], {}
    while pending:
        key = pending.pop()
        if key in edges:
            continue
        children = {dep["pkg"] for dep in nodes[key]["deps"]
                    if any(kind["kind"] is None for kind in dep["dep_kinds"])}
        edges[key] = children
        pending.extend(children)
    refs = {key: f"pkg:cargo/{quote(packages[key]['name'])}@{packages[key]['version']}" for key in edges}
    checksums = {(p["name"], p["version"], p.get("source")): p.get("checksum") for p in lock["package"]}
    components = []
    for key in sorted(edges):
        package = packages[key]
        component = {"type": "library", "bom-ref": refs[key], "purl": refs[key], "name": package["name"],
                     "version": package["version"], "scope": "required",
                     "properties": [{"name": "sharppy:ecosystem", "value": "cargo-windows-normal"},
                                    {"name": "sharppy:cargo-features", "value": ",".join(nodes[key]["features"])}]}
        license_expression = package.get("license") or ("BSD-3-Clause" if package.get("source") is None else None)
        if license_expression:
            component["licenses"] = [{"expression": license_expression}]
        checksum = checksums.get((package["name"], package["version"], package.get("source")))
        if checksum:
            component["hashes"] = [{"alg": "SHA-256", "content": checksum}]
        references = [{"type": kind, "url": package[field]} for field, kind in
                      (("repository", "vcs"), ("homepage", "website")) if (package.get(field) or "").startswith("https://")]
        if references:
            component["externalReferences"] = references
        components.append(component)
    return components, [{"ref": refs[key], "dependsOn": sorted(refs[child] for child in edges[key])} for key in sorted(edges)], [refs[root]]


def validate_graph(bom):
    refs = [bom["metadata"]["component"]["bom-ref"], *(c["bom-ref"] for c in bom["components"])]
    if len(refs) != len(set(refs)):
        raise ValueError("Duplicate component references")
    known = set(refs)
    for node in bom["dependencies"]:
        if node["ref"] not in known or not set(node["dependsOn"]) <= known:
            raise ValueError("Dangling dependency reference")


def windows_advisories(graphs, root):
    """Retain RustSec findings, proving absence through every Windows edge kind."""
    warnings = (("glib", "RUSTSEC-2024-0429"), ("proc-macro-error", "RUSTSEC-2024-0370"))
    for graph in graphs:
        packages = {package["id"]: package for package in graph["packages"]}
        nodes = {node["id"]: node for node in graph["resolve"]["nodes"]}
        pending, seen = [graph["resolve"]["root"]], set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            package = packages[key]
            if package["name"] == "proc-macro-error" or (package["name"] == "glib" and Version(package["version"]) < Version("0.20")):
                raise ValueError(f"RustSec-affected package reached the Windows graph: {package['name']} {package['version']}")
            pending.extend(dep["pkg"] for dep in nodes[key]["deps"])
    return [{"id": advisory, "source": {"name": "RustSec", "url": f"https://rustsec.org/advisories/{advisory}.html"},
             "affects": [{"ref": root}], "analysis": {"state": "not_affected", "justification": "code_not_present",
             "detail": f"{name} is absent from both filtered x86_64-pc-windows-msvc dependency graphs, including build/dev edges. The all-target Cargo.lock warning remains applicable to Linux GTK3 builds; it is not suppressed or claimed fixed."}}
            for name, advisory in warnings]


def generate(output: Path, artifacts: Path | None = None, runtime_root: Path | None = None):
    if shutil.which("cargo") is None or shutil.which("npm") is None:
        raise ValueError("Cargo and npm are required to inventory the built dependency graphs")
    version = runpy.run_path(str(ROOT / "sharpmod/_version.py"))["__version__"]
    if metadata.version("sharpmod") != version or metadata.version("sharpmod-rs") != version:
        raise ValueError("Installed Python/native package versions do not match the release source")
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, encoding="utf-8").strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    npm = json.loads(subprocess.check_output([shutil.which("npm"), "sbom", "--omit=dev", "--sbom-format=cyclonedx", "--sbom-type=application"], cwd=ROOT / "frontend", encoding="utf-8"))
    npm["metadata"]["component"]["licenses"] = [{"license": {"id": "BSD-3-Clause"}}]
    components = {c["bom-ref"]: c for c in [npm["metadata"]["component"], *npm["components"]]}
    dependencies = {node["ref"]: set(node["dependsOn"]) for node in npm["dependencies"]}
    roots = [npm["metadata"]["component"]["bom-ref"]]
    graphs = [python_graph()]
    cargo_graphs = []
    for manifest in (ROOT / "src-tauri/Cargo.toml", ROOT / "rust/sharpmod-rs/Cargo.toml"):
        graph = json.loads(subprocess.check_output(["cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", "x86_64-pc-windows-msvc", "--manifest-path", str(manifest)], cwd=ROOT, encoding="utf-8"))
        graphs.append(cargo_graph(graph, tomllib.loads(manifest.with_name("Cargo.lock").read_text())))
        cargo_graphs.append(graph)
    for entries, edges, graph_roots in graphs:
        for component in entries:
            ref = component["bom-ref"]
            if ref in components:
                component["properties"] = list({(p["name"], p["value"]): p for p in
                    [*components[ref].get("properties", []), *component.get("properties", [])]}.values())
            components[ref] = component
        for node in edges:
            dependencies.setdefault(node["ref"], set()).update(node["dependsOn"])
        roots.extend(graph_roots)
    if artifacts is not None:
        for path in sorted(artifacts.iterdir()):
            if path.is_file() and path.suffix in (".exe", ".zip", ".whl"):
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                ref = f"artifact:{path.name}"
                components[ref] = {"type": "file", "bom-ref": ref, "name": path.name, "version": version,
                                   "hashes": [{"alg": "SHA-256", "content": digest}]}
    root = f"sharppy-reimagined:{version}"
    compositions = [{"aggregate": "incomplete", "assemblies": [root]}]
    if runtime_root is not None:
        inventory = runpy.run_path(str(ROOT / "packaging/runtime_inventory.py"))["runtime_inventory"]
        entries, edges, assembly = inventory(runtime_root, list(components.values()))
        components.update((component["bom-ref"], component) for component in entries)
        dependencies.update((node["ref"], set(node["dependsOn"])) for node in edges)
        roots.append(assembly)
        compositions.append({"aggregate": "complete", "assemblies": [assembly]})
    dependencies[root] = set(roots)
    bom = {"$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json", "bomFormat": "CycloneDX", "specVersion": "1.6",
           "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
           "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(),
               "component": {"type": "application", "bom-ref": root, "name": "SHARPpy Reimagined", "version": version,
                             "licenses": [{"license": {"id": "BSD-3-Clause"}}]},
               "properties": [{"name": "sharppy:source-sha", "value": source},
                              {"name": "sharppy:source-dirty", "value": str(dirty).lower()},
                              {"name": "sharppy:coverage", "value": "Resolved production npm, installed Python [era5,wrf,render] plus SHARPpy/native wheel, and Windows Cargo normal dependency graphs. When supplied, the complete shipped file assembly includes SHA256, PE versions/imports, Python RECORD ownership and external Windows/WebView2 requirements. Static linked sub-libraries and deployment-system versions are not asserted complete."}]},
           "components": sorted(components.values(), key=lambda c: c["bom-ref"]),
           "dependencies": [{"ref": ref, "dependsOn": sorted(children)} for ref, children in sorted(dependencies.items())],
           "compositions": compositions, "vulnerabilities": windows_advisories(cargo_graphs, root)}
    validate_graph(bom)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bom, indent=2) + "\n", encoding="utf-8")
    return bom


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    args = parser.parse_args()
    result = generate(args.output, args.artifacts, args.runtime_root)
    print(f"SBOM: {len(result['components'])} components, {len(result['dependencies'])} dependency records -> {args.output}")
