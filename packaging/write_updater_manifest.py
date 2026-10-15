"""Write the Tauri feed beside the renamed, signed Windows release installer."""

import argparse
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


def write_manifest(installer: Path, tag: str) -> Path:
    if not installer.is_file():
        raise FileNotFoundError(installer)
    signature = installer.with_suffix(installer.suffix + ".sig").read_text(encoding="utf-8").strip()
    if not signature:
        raise ValueError("Updater signature is empty")
    base64.b64decode(signature, validate=True)
    manifest = {
        "version": tag.removeprefix("v"),
        "pub_date": datetime.now(timezone.utc).isoformat(),
        "platforms": {
            "windows-x86_64": {
                "url": f"https://github.com/ShianMike/SHARPpy-Reimagined/releases/download/{quote(tag, safe='')}/{quote(installer.name)}",
                "signature": signature,
            }
        },
    }
    destination = installer.parent / "latest.json"
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("tag")
    args = parser.parse_args()
    write_manifest(args.installer, args.tag)
