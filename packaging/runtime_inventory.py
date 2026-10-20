"""Hash every shipped file and retain PE versions/imports and external requirements."""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote


def pe_details(path):
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            return {}, []
    # pefile is already installed by PyInstaller in the Windows packaging job.
    import pefile

    with pefile.PE(str(path), fast_load=True) as pe:
        pe.parse_data_directories(directories=[1, 2, 13])  # imports, resources, delayed imports
        strings = {}
        for group in getattr(pe, "FileInfo", []):
            for item in group:
                for table in getattr(item, "StringTable", []):
                    strings.update({key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
                                    for key, value in table.entries.items()})
        imports = sorted({entry.dll.decode("ascii").lower() for kind in
                          ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT")
                          for entry in getattr(pe, kind, [])})
        if any(Path(name).name != name or any(char in name for char in "/\\:") for name in imports):
            raise ValueError(f"Invalid PE import name in {path}")
        return strings, imports


def runtime_inventory(root, python_components):
    root = Path(root).absolute()
    files, pending = [], [root]
    while pending:
        path = pending.pop()
        attributes = path.lstat()
        if path.is_symlink() or getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError(f"Runtime inventory rejects links/reparse points: {path}")
        if path.is_dir():
            pending.extend(path.iterdir())
        elif path.is_file():
            files.append(path)
        else:
            raise ValueError(f"Unsupported runtime entry: {path}")
    if not files:
        raise ValueError("Runtime inventory is empty")

    owners = {}
    for component in python_components:
        if not component.get("purl", "").startswith("pkg:pypi/"):
            continue
        dist = metadata.distribution(component["name"])
        for file in dist.files or []:
            owners.setdefault(str(file).replace("\\", "/").lower(), set()).add(component["bom-ref"])

    assembly = "sharppy:windows-runtime-files"
    components = [{"type": "application", "bom-ref": assembly, "name": "Windows shipped file inventory",
                   "properties": [{"name": "sharppy:file-count", "value": str(len(files))}]}]
    references = {path: f"file:windows-runtime/{quote(path.relative_to(root).as_posix(), safe='/')}" for path in files}
    names = {}
    for path, ref in references.items():
        names.setdefault(path.name.lower(), []).append((path, ref))
        alias = re.sub(r"-[a-f0-9]{32}(?=\.dll$)", "", path.name.lower())
        if alias != path.name.lower():
            names.setdefault(alias, []).append((path, ref))
    edges = {assembly: set(references.values())}
    external = {}
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        info, imports = pe_details(path)
        properties = [{"name": "sharppy:delivery", "value": "bundled"},
                      {"name": "sharppy:file-size", "value": str(path.stat().st_size)}]
        if imports:
            properties.append({"name": "sharppy:pe-imports", "value": ",".join(imports)})
        properties.extend({"name": f"sharppy:pe:{key}", "value": value} for key, value in sorted(info.items())
                          if key in ("CompanyName", "FileDescription", "FileVersion", "ProductName", "ProductVersion", "LegalCopyright"))
        component = {"type": "file", "bom-ref": references[path], "name": relative,
                     "hashes": [{"alg": "SHA-256", "content": digest}], "properties": properties}
        if info.get("FileVersion"):
            component["version"] = info["FileVersion"]
        components.append(component)
        bundled = relative.removeprefix("backend/_internal/").lower()
        edges[references[path]] = set(owners.get(bundled, ()))
        for name in imports:
            candidates = names.get(name, [])
            local = [ref for candidate, ref in candidates if candidate.parent == path.parent]
            if candidates:
                # Include delvewheel-mangled aliases; these are possible links, not a loader trace.
                edges[references[path]].update(local or [ref for _, ref in candidates])
                continue
            ref = f"external:windows-import/{quote(name)}"
            edges[references[path]].add(ref)
            if ref not in external:
                system_file = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / name
                supplied_by_windows = name.startswith(("api-ms-win-", "ext-ms-win-")) or system_file.is_file()
                external[ref] = {"type": "file", "bom-ref": ref, "name": name,
                    "description": "Resolved by the deployment Windows installation; not redistributed." if supplied_by_windows else
                                   "External PE import; not found in the shipped tree or packaging host System32.",
                    "properties": [{"name": "sharppy:delivery", "value": "external"},
                                   {"name": "sharppy:provider", "value": "Windows" if supplied_by_windows else "unresolved"}]}
    components.extend(external.values())
    for ref, name, url in (
        ("external:windows", "Microsoft Windows", "https://learn.microsoft.com/windows/"),
        ("external:webview2", "Microsoft Edge WebView2 Evergreen Runtime", "https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution"),
    ):
        components.append({"type": "operating-system" if ref == "external:windows" else "framework",
            "bom-ref": ref, "name": name, "scope": "required",
            "description": "Version is provided by the deployment system; measured in native acceptance, not fixed by this package.",
            "properties": [{"name": "sharppy:delivery", "value": "external"}],
            "externalReferences": [{"type": "documentation", "url": url}]})
        edges[assembly].add(ref)
    edges["external:windows"] = {ref for ref, component in external.items()
                                 if {"name": "sharppy:provider", "value": "Windows"} in component["properties"]}
    return components, [{"ref": ref, "dependsOn": sorted(children)} for ref, children in edges.items()], assembly
