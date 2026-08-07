#!/usr/bin/env python
"""Download versioned NCEI Storm Events detail files for TOI catalogue builds.

The catalogue builder can fetch these itself, but retaining the raw versioned
CSVs locally is what makes the provenance auditable: NCEI encodes a creation
date in every file name and republishes corrected years, so
``StormEvents_details-ftp_v1.0_d2018_c20250401.csv.gz`` is a *different dataset*
from the ``c20240117`` build of the same year.  Keeping the exact bytes plus a
SHA-256 means a later run can prove which revision produced a label.

Downloads are skipped when a file of the recorded size already exists, so the
script is safe to re-run and resumable after an interruption.

Usage::

    python scripts/fetch_ncei_storm_events.py --out-dir archive/ncei \
        --first-year 2015 --last-year 2025
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sharpmod.guidance.toi_catalog import (  # noqa: E402
    NCEI_STORM_EVENTS_LICENSE,
    _http_get,
    ncei_detail_urls,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="archive/ncei")
    parser.add_argument("--first-year", type=int, default=2015)
    parser.add_argument("--last-year", type=int, default=2025)
    parser.add_argument("--retries", type=int, default=4)
    args = parser.parse_args(argv)

    years = list(range(args.first_year, args.last_year + 1))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"resolving NCEI detail URLs for {years[0]}-{years[-1]} ...")
    urls = ncei_detail_urls(years)

    records: list[dict[str, object]] = []
    for year, url in sorted(urls.items()):
        name = url.rsplit("/", 1)[-1]
        target = out_dir / name
        if target.exists() and target.stat().st_size > 0:
            print(f"  {year}  already present  {name}")
        else:
            payload = None
            for attempt in range(1, args.retries + 1):
                try:
                    payload = _http_get(url, timeout=300)
                    break
                except (urllib.error.URLError, OSError, TimeoutError) as exc:
                    if attempt == args.retries:
                        print(f"  {year}  FAILED after {attempt} attempts: {exc}")
                        return 1
                    delay = min(30.0, 2.0**attempt)
                    print(f"  {year}  attempt {attempt} failed ({exc}); "
                          f"retrying in {delay:.0f}s")
                    time.sleep(delay)
            assert payload is not None
            partial = target.with_name(target.name + ".partial")
            partial.write_bytes(payload)
            os.replace(partial, target)
            print(f"  {year}  downloaded  {name}  "
                  f"{target.stat().st_size / 1024**2:.2f} MiB")

        records.append(
            {
                "year": year,
                "file_name": name,
                "url": url,
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )

    manifest = {
        "source": "NOAA NCEI Storm Events Database",
        "license": NCEI_STORM_EVENTS_LICENSE,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "years": years,
        "files": records,
        "total_bytes": sum(int(item["bytes"]) for item in records),
    }
    manifest_path = out_dir / "ncei-source-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    total_mib = manifest["total_bytes"] / 1024**2  # type: ignore[operator]
    print(f"\n{len(records)} file(s), {total_mib:.1f} MiB total")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
