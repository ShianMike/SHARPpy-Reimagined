"""Fetch observed soundings through an explicit provider/fallback chain."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

import numpy as np

from sharpmod.observations import (
    DEFAULT_PROVIDER_ORDER,
    ObservedProviderError,
    available_observed_providers,
    fetch_observed,
    write_observed_npz,
)


def _parse_when(value: str) -> datetime:
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"could not parse observation time {value!r}: {exc}"
        ) from exc


def _cmd_providers(_args) -> int:
    for info in available_observed_providers():
        print(f"{info.key:8s} {info.name}\n         {info.homepage}")
    return 0


def _provider_order(value: str) -> tuple[str, ...]:
    if value == "auto":
        return DEFAULT_PROVIDER_ORDER
    return (value,)


def _safe_token(value: str) -> str:
    token = "".join(
        character if character.isalnum() else "_" for character in value
    ).strip("_")
    return token or "station"


def _cmd_fetch(args) -> int:
    providers = _provider_order(args.provider)
    print(
        f"Fetching {args.station} at {args.time:%Y-%m-%d %H:%M} UTC "
        f"via {', '.join(providers)} ..."
    )
    try:
        result = fetch_observed(args.station, args.time, providers=providers)
    except ObservedProviderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    attempts = result.metadata.get("fallback_attempts", ())
    for attempt in attempts:
        if isinstance(attempt, dict):
            print(
                f"{attempt.get('provider', 'provider')} failed: "
                f"{attempt.get('error', 'unknown failure')}"
            )
    print(
        f"Selected {result.provider} ({result.provider_name}); "
        f"source station {result.station_id}"
    )
    out = args.out
    if out is None:
        out = (
            f"observed_{_safe_token(result.provider)}_"
            f"{_safe_token(result.station_id)}_{result.valid:%Y%m%d%H}.npz"
        )
    try:
        write_observed_npz(result, out, loc=args.loc)
    except (OSError, ValueError, TypeError) as exc:
        print(f"ERROR: could not write observed sounding: {exc}", file=sys.stderr)
        return 2
    levels = int(np.ma.asarray(result.profile.pres).size)
    print(f"wrote {out} ({levels} levels, source={result.provider})")

    if args.render is not None:
        from sharpmod.tools import render_npz

        png_path = args.render or None
        rendered = render_npz(out, png_path)
        print(f"rendered {rendered}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="observed-sounding",
        description=(
            "Fetch one observed sounding from UWyo or the independent IEM "
            "RAOB archive. The default auto mode tries UWyo, then IEM, and "
            "records the selected source without merging providers."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    providers = commands.add_parser(
        "providers", help="list available observed-sounding providers"
    )
    providers.set_defaults(func=_cmd_providers)

    fetch = commands.add_parser(
        "fetch", help="fetch one station/time report to a portable .npz"
    )
    fetch.add_argument("station", help="WMO, provider station id, or station name")
    fetch.add_argument("time", type=_parse_when, help="UTC time")
    fetch.add_argument(
        "--provider",
        choices=("auto",) + DEFAULT_PROVIDER_ORDER,
        default="auto",
        help="provider or explicit UWyo -> IEM fallback (default: auto)",
    )
    fetch.add_argument("--out", type=Path, default=None, help="output .npz path")
    fetch.add_argument("--loc", default=None, help="location label")
    fetch.add_argument(
        "--render",
        nargs="?",
        const="",
        default=None,
        metavar="PNG",
        help="also render to PNG (optional path)",
    )
    fetch.set_defaults(func=_cmd_fetch)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if isinstance(getattr(args, "out", None), Path):
        args.out = str(args.out)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
