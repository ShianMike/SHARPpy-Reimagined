#!/usr/bin/env python3
"""Record measured release downloads and render their date-based SVG history."""

import argparse
import json
import math
import os
from datetime import UTC, datetime
from html import escape
from http.client import HTTPSConnection
from itertools import count
from pathlib import Path

REPOSITORY = "ShianMike/SHARPpy-Reimagined"
DEFAULT_HISTORY = Path(__file__).resolve().parents[1] / "docs/images/download-history.json"


def api_items(resource, token):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "SHARPpy-download-history"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for page in count(1):
        # Fixed HTTPS host; http.client never follows redirects with credentials.
        connection = HTTPSConnection("api.github.com", timeout=30)
        try:
            connection.request("GET", f"/repos/{REPOSITORY}/{resource}?per_page=100&page={page}", headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"GitHub API returned HTTP {response.status} for {resource}")
            items = json.load(response)
        finally:
            connection.close()
        if not isinstance(items, list):
            raise ValueError("Expected a GitHub API list")
        yield from items
        if len(items) < 100:
            break


def count_assets(assets):
    totals = {"exe": 0, "zip": 0}
    for asset in assets:
        name = asset["name"]
        if name.startswith("SHARPpy-Reimagined-") and name.endswith((".exe", ".zip")):
            downloads = asset["download_count"]
            if type(downloads) is not int or downloads < 0:
                raise ValueError(f"Invalid download count for {name}")
            totals[name.rsplit(".", 1)[1]] += downloads
    return totals


def collect(token):
    totals = {"exe": 0, "zip": 0}
    for release in api_items("releases", token):
        if release["draft"]:
            continue
        assets = api_items(f"releases/{int(release['id'])}/assets", token)
        for kind, downloads in count_assets(assets).items():
            totals[kind] += downloads
    return {"collected_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), **totals, "total": sum(totals.values())}


def sample_time(sample):
    if any(type(sample[key]) is not int or sample[key] < 0 for key in ("exe", "zip", "total")):
        raise ValueError("Download counts must be nonnegative integers")
    if sample["total"] != sample["exe"] + sample["zip"]:
        raise ValueError("Sample total does not match its executable and ZIP counts")
    return datetime.strptime(sample["collected_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def validate_history(history):
    if history["repository"] != REPOSITORY or not history["samples"]:
        raise ValueError("Expected this repository's measured download history")
    times = [sample_time(sample) for sample in history["samples"]]
    if any(a.date() >= b.date() for a, b in zip(times, times[1:])):
        raise ValueError("History must have one chronological sample per UTC date")
    return times


def add_sample(history, sample):
    previous = validate_history(history)[-1]
    current = sample_time(sample)
    if current < previous:
        raise ValueError("Cannot insert a measurement before the latest sample")
    if current.date() > previous.date():
        history["samples"].append(sample)


def render_svg(history):
    times = validate_history(history)
    samples = history["samples"]
    values = [sample["total"] for sample in samples]
    top = max(1000, math.ceil(max(values) / 1000) * 1000)
    span = (times[-1] - times[0]).total_seconds()
    xs = [85 + (t - times[0]).total_seconds() / span * 635 if span else 402.5 for t in times]
    ys = [345 - value / top * 240 for value in values]
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="760" height="420" viewBox="0 0 760 420" role="img" aria-labelledby="title description">',
        '<title id="title">SHARPpy Reimagined download history</title>',
        f'<desc id="description">History starts {times[0]:%d %b %Y}. Measured cumulative executable and ZIP release downloads by UTC collection date. No history before the first measurement is inferred. Source: GitHub Releases API. Excludes latest.json and support files; downloads are not unique users or installs.</desc>',
        f'<metadata>{escape(json.dumps(samples))}</metadata>',
        '<rect width="760" height="420" fill="#ffffff"/>',
        '<g font-family="Arial, sans-serif" fill="#202938">',
        '<text x="60" y="37" font-size="24" font-weight="bold">Download History</text>',
        '<line x1="60" y1="66" x2="80" y2="66" stroke="#d97706" stroke-width="3"/>',
        f'<text x="90" y="71" font-size="13">{REPOSITORY}</text>',
    ]
    for tick in range(5):
        y = 345 - tick / 4 * 240
        svg.extend([
            f'<line x1="85" y1="{y:g}" x2="720" y2="{y:g}" stroke="#edf0f3"/>',
            f'<text x="74" y="{y + 4:g}" text-anchor="end" font-size="11" fill="#596579">{top * tick / 4:,.0f}</text>',
        ])
    svg.append('<path d="M85 105 V345 H720" fill="none" stroke="#9ca3af"/>')
    if len(samples) > 1:
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys))
        svg.append(f'<polyline points="{points}" fill="none" stroke="#d97706" stroke-width="3"/>')
    for x, y, sample in zip(xs, ys, samples):
        svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#d97706"><title>{sample["collected_at"]}: {sample["total"]:,} downloads</title></circle>')
    svg.append(f'<text x="{xs[-1]:.2f}" y="{ys[-1] - 12:.2f}" text-anchor="middle" font-size="12">{values[-1]:,}</text>')
    for index in sorted({round(i * (len(samples) - 1) / 4) for i in range(5)}):
        svg.append(f'<text x="{xs[index]:.2f}" y="367" text-anchor="middle" font-size="11" fill="#596579">{times[index]:%d %b %Y}</text>')
    svg.append('<text x="402.5" y="402" text-anchor="middle" font-size="14">Date</text>')
    svg.append('<text transform="translate(23 225) rotate(-90)" text-anchor="middle" font-size="14">Downloads</text>')
    return "\n".join([*svg, "</g></svg>", ""])


def write_atomic(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--render-only", action="store_true", help="Render saved measurements without contacting GitHub")
    args = parser.parse_args()
    history = json.loads(args.history.read_text(encoding="utf-8"))
    validate_history(history)
    if not args.render_only:
        add_sample(history, collect(os.environ.get("GH_TOKEN", "")))
    svg = render_svg(history)
    if not args.render_only:
        write_atomic(args.history, json.dumps(history, indent=2) + "\n")
    write_atomic(args.history.with_suffix(".svg"), svg)
    print(f"{len(history['samples'])} measured daily sample(s); latest total: {history['samples'][-1]['total']:,}")


if __name__ == "__main__":
    main()
