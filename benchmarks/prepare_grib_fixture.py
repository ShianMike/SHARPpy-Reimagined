"""Write a canonical pressure-field GRIB fixture for decoder benchmarks.

Provider byte-range downloads may include unrelated messages between requested
ranges.  The production direct decoder ignores those messages, while a legacy
cfgrib/xarray outer merge can add their pressure coordinates.  This helper
copies the complete *physical* GRIB messages selected by the direct inventory,
so old and optimized benchmarks receive the same scientific field set without
destroying U/V multi-field messages.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from typing import Sequence


def _activate_checkout(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise SystemExit(f"--checkout is not a directory: {resolved}")
    text = str(resolved)
    if text not in sys.path:
        sys.path.insert(0, text)


def _message_length(source, offset: int) -> int:
    source.seek(offset)
    header = source.read(16)
    if len(header) < 8 or header[:4] != b"GRIB":
        raise ValueError(f"invalid GRIB header at byte {offset}")
    edition = header[7]
    if edition == 1:
        length = int.from_bytes(header[4:7], "big")
    elif edition == 2:
        if len(header) < 16:
            raise ValueError(f"truncated GRIB2 header at byte {offset}")
        length = int.from_bytes(header[8:16], "big")
    else:
        raise ValueError(f"unsupported GRIB edition {edition} at byte {offset}")
    if length < 12:
        raise ValueError(f"invalid GRIB message length {length} at byte {offset}")
    return length


def _copy_selected_messages(source_path: Path, output_path: Path):
    from sharpmod.backends import grib

    eccodes = grib.load_eccodes()
    identity = grib._file_identity(source_path)
    inventory = grib._scan_inventory(identity, eccodes)
    offsets = sorted({
        reference.offset
        for role in inventory.roles
        for reference in role.messages
    })
    if not offsets:
        raise ValueError("direct inventory selected no physical GRIB messages")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".part",
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    bytes_written = 0
    try:
        with source_path.open("rb") as source, temporary_path.open(
            "wb"
        ) as output:
            for offset in offsets:
                length = _message_length(source, offset)
                source.seek(offset)
                message = source.read(length)
                if len(message) != length or message[-4:] != b"7777":
                    raise ValueError(
                        f"truncated GRIB message at byte {offset}: "
                        f"expected {length}, read {len(message)}"
                    )
                output.write(message)
                digest.update(message)
                bytes_written += length
        os.replace(temporary_path, output_path)
    except BaseException:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise
    return inventory, len(offsets), bytes_written, digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _activate_checkout(args.checkout)
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"source GRIB does not exist: {source}")
    if os.path.normcase(source) == os.path.normcase(output):
        raise SystemExit("source and output must be different files")
    if output.exists():
        try:
            if source.samefile(output):
                raise SystemExit("source and output must be different files")
        except OSError:
            pass
    inventory, messages, size, digest = _copy_selected_messages(source, output)
    print(f"source: {source}")
    print(f"output: {output}")
    print(f"physical messages: {messages}")
    print(f"pressure levels: {len(inventory.levels)}")
    print(f"bytes: {size}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
