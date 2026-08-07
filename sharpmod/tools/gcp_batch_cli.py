#!/usr/bin/env python3
"""``sharpmod-toi-batch``: inert-by-default Google Cloud Batch packaging CLI.

Every mutating command defaults to a dry run.  Submission, API enabling, bucket
creation, and cleanup each require their own explicit confirmation flag, and this
tool never calls a Google API or infers permission from configuration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sharpmod.tools.gcp_batch import (
    TOI_BATCH_PACKAGE_VERSION,
    BatchConfig,
    BatchPlanError,
    JobBudget,
    build_plan,
    build_source_bundle,
    cleanup_inventory,
    describe_sharding,
    lifecycle_policy,
    preflight,
    rendered_commands,
    resolve_project,
    shard_catalog,
    verify_merged_output,
)

DISCLAIMER = (
    "EXPERIMENTAL SHARPpy tooling. This command performs no Google Cloud "
    "mutation and enables no API."
)
DEFAULT_OUT = Path("infra/gcp/toi-batch/out")


def _write(path: Path, payload: Any) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str)
    )
    path.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
    return path


def _config(args: argparse.Namespace) -> BatchConfig:
    if getattr(args, "config", None):
        payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
        config = BatchConfig.from_mapping(payload)
        if getattr(args, "project", None):
            config = BatchConfig.from_mapping({**payload, "project": args.project})
        return config
    project = resolve_project(getattr(args, "project", None))
    bucket = getattr(args, "bucket", None) or f"{project}-toi-archive"
    budget = JobBudget(
        maximum_cases_per_task=args.max_cases_per_task,
        maximum_input_gib=args.max_input_gib,
        maximum_task_seconds=args.max_task_seconds,
        maximum_tasks=max(args.shards, args.max_tasks),
        maximum_retries=args.max_retries,
        boot_disk_gib=args.boot_disk_gib,
    )
    return BatchConfig(
        project=project,
        bucket=bucket,
        region=args.region,
        machine_type=args.machine_type,
        provisioning_model=args.provisioning_model,
        shard_count=args.shards,
        parallelism=args.parallelism,
        run_id=getattr(args, "run_id", "") or "",
        budget=budget,
    )


def render_config_command(args: argparse.Namespace) -> int:
    config = _config(args)
    path = _write(Path(args.output), config.to_mapping())
    print(DISCLAIMER, flush=True)
    print(f"Wrote config to {path}", flush=True)
    print(
        f"project={config.project} region={config.region} bucket={config.bucket} "
        f"shards={config.shard_count} parallelism={config.parallelism}",
        flush=True,
    )
    print(f"job name would be: {config.job_name}", flush=True)
    return 0


def preflight_command(args: argparse.Namespace) -> int:
    config = _config(args)
    report = preflight(config, check_gcloud=args.check_gcloud)
    print(DISCLAIMER, flush=True)
    for check in report["checks"]:
        state = {True: "PASS", False: "FAIL", None: "USER"}[check["ok"]]
        print(f"  [{state}] {check['check']}: {check['detail']}", flush=True)
    print(f"ready_to_plan={report['ready_to_plan']}", flush=True)
    if args.report:
        print(f"Wrote preflight report to {_write(Path(args.report), report)}")
    return 0 if report["ready_to_plan"] else 1


def bundle_command(args: argparse.Namespace) -> int:
    report = build_source_bundle(
        args.root, args.output, dry_run=not args.confirm_write
    )
    print(DISCLAIMER, flush=True)
    print(
        f"{'DRY RUN: would bundle' if report['dry_run'] else 'Bundled'} "
        f"{report['included_files']} file(s), {report['included_mib']} MiB",
        flush=True,
    )
    for reason, count in report["excluded_reasons"].items():
        print(f"  excluded {count:>5} by {reason}", flush=True)
    provenance = report["provenance"]
    print(
        f"git HEAD {provenance['git_head'][:12]} branch {provenance['git_branch']} "
        f"dirty={provenance['worktree_dirty']} "
        f"({provenance['dirty_path_count']} path(s))",
        flush=True,
    )
    if report.get("bundle_sha256"):
        print(f"bundle sha256 {report['bundle_sha256']}", flush=True)
    if args.report:
        print(f"Wrote bundle report to {_write(Path(args.report), report)}")
    return 0


def shard_command(args: argparse.Namespace) -> int:
    config = _config(args)
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    cases = catalog.get("cases") or []
    summary = describe_sharding(cases, config.shard_count)
    print(DISCLAIMER, flush=True)
    print(
        f"{summary['cases']} case(s), {summary['events']} event(s) -> "
        f"{summary['shard_count']} shard(s); events split across shards: "
        f"{summary['events_split_across_shards']}",
        flush=True,
    )
    for name, counts in summary["per_shard"].items():
        print(f"  {name}: {counts['cases']} cases, {counts['events']} events")
    if args.output_dir:
        shards = shard_catalog(cases, config.shard_count)
        for index, members in sorted(shards.items()):
            payload = {**catalog, "cases": members}
            payload["shard_index"] = index
            payload["shard_count"] = config.shard_count
            _write(Path(args.output_dir) / f"shard-{index:02d}.json", payload)
        print(f"Wrote {len(shards)} shard catalogue(s) to {args.output_dir}")
    return 0


def plan_command(args: argparse.Namespace) -> int:
    config = _config(args)
    cases = args.cases
    sharding: dict[str, Any] = {}
    if args.catalog:
        catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        entries = catalog.get("cases") or []
        cases = cases or len(entries)
        sharding = describe_sharding(entries, config.shard_count)
    bundle = None
    if args.bundle_report:
        bundle = json.loads(Path(args.bundle_report).read_text(encoding="utf-8"))
    plan = build_plan(config, cases=cases, bundle=bundle, sharding=sharding)

    print(DISCLAIMER, flush=True)
    print(f"=== DRY-RUN PLAN ({TOI_BATCH_PACKAGE_VERSION}) ===", flush=True)
    print(f"project {config.project} | region {config.region}", flush=True)
    print("\nResources that WOULD be created:", flush=True)
    for resource in plan["planned_resources"]:
        print(f"  - {resource['kind']}: {resource['name']}", flush=True)
        print(f"      {resource['action']} - {resource['detail']}", flush=True)
    usage = plan["projected_usage"]
    print("\nProjected usage:", flush=True)
    print(
        f"  cases {usage['cases']} | inbound transfer "
        f"{usage['inbound_transfer_gib']} GiB | single-worker "
        f"{usage['single_worker_wall_hours']} h | vCPU-hours "
        f"{usage['vcpu_hours']}",
        flush=True,
    )
    print(
        f"  peak raw disk {usage['peak_raw_disk_mib']} MiB | retained output "
        f"{usage['retained_output_mib']} MiB | boot disk "
        f"{usage['boot_disk_gib_per_task']} GiB/task",
        flush=True,
    )
    print("\nRetry / preemption:", flush=True)
    print(f"  {plan['retry_and_preemption']['behaviour']}", flush=True)
    print("\nCleanup actions:", flush=True)
    for action in plan["cleanup_actions"]:
        print(f"  - {action}", flush=True)
    print("\nRemaining user confirmations:", flush=True)
    for item in plan["remaining_confirmations"]:
        print(f"  [ ] {item}", flush=True)

    out = Path(args.output_dir or DEFAULT_OUT)
    _write(out / "plan.json", plan)
    _write(out / "job.json", plan["job_spec"])
    _write(out / "task-script.sh", plan["job_spec"]["taskGroups"][0]["taskSpec"][
        "runnables"
    ][0]["script"]["text"])
    _write(out / "lifecycle.json", lifecycle_policy())
    _write(out / "commands.json", rendered_commands(config))
    print(f"\nWrote rendered dry-run artifacts to {out}", flush=True)
    print("NOTHING WAS EXECUTED. No API enabled, no bucket, no VM, no job.", flush=True)
    return 0


def submit_command(args: argparse.Namespace) -> int:
    config = _config(args)
    commands = rendered_commands(config)
    print(DISCLAIMER, flush=True)
    if not args.confirm_submit:
        print("DRY RUN: submission requires --confirm-submit.", flush=True)
        print(f"Would run: {commands['submit']}", flush=True)
        print("Prerequisites not verified by this tool:", flush=True)
        for item in ("enable_apis", "create_bucket", "upload_source"):
            print(f"  {commands[item]}", flush=True)
        return 0
    print(
        "Refusing to submit: this build intentionally contains no submission "
        "path. Run the printed gcloud command manually after completing every "
        "confirmation in plan.json.",
        file=sys.stderr,
    )
    print(commands["submit"], flush=True)
    return 2


def status_command(args: argparse.Namespace) -> int:
    config = _config(args)
    commands = rendered_commands(config)
    print(DISCLAIMER, flush=True)
    print(f"Status command:  {commands['status']}", flush=True)
    print(f"Logs command:    {commands['logs']}", flush=True)
    print(f"Fetch output:    {commands['fetch_output']}", flush=True)
    print(
        "Resume: re-running the same Batch job or the local run-toi-archive "
        "command against the mirrored state prefix continues from the "
        "checkpoint; completed cache keys are skipped.",
        flush=True,
    )
    return 0


def verify_merge_command(args: argparse.Namespace) -> int:
    report = verify_merged_output(
        args.shard_dir, expected_cases=args.expect_keys or None
    )
    print(DISCLAIMER, flush=True)
    for shard in report["shards"]:
        print(
            f"  {shard['shard']}: verified={shard['verified']} "
            f"cases={shard['cases']} failures={shard['failures']}",
            flush=True,
        )
    print(
        f"merged cases {report['merged_cases']} | unique events "
        f"{report['unique_events']} | failures {report['failure_count']}",
        flush=True,
    )
    for failure in report["failures"][:20]:
        print(f"  FAIL {failure.get('check')}: {failure.get('detail')}", flush=True)
    if args.output:
        print(f"Wrote merge report to {_write(Path(args.output), report)}")
    return 0 if report["verified"] else 1


def cleanup_command(args: argparse.Namespace) -> int:
    config = _config(args)
    inventory = cleanup_inventory(config, confirm_delete=args.confirm_delete)
    print(DISCLAIMER, flush=True)
    print("Would delete:", flush=True)
    for item in inventory["would_delete"]:
        print(f"  - {item['target']}  ({item['reason']})", flush=True)
    print("Would retain:", flush=True)
    for target in inventory["would_retain"]:
        print(f"  - {target}", flush=True)
    print(
        f"dry_run={inventory['dry_run']} executed={inventory['executed']} "
        "(this tool never issues a delete)",
        flush=True,
    )
    if args.output:
        print(f"Wrote cleanup inventory to {_write(Path(args.output), inventory)}")
    return 0


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", help="explicit project; else environment")
    parser.add_argument("--bucket", help="dedicated bucket (never an existing one)")
    parser.add_argument("--region", default="us-east1")
    parser.add_argument("--machine-type", default="e2-standard-4")
    parser.add_argument(
        "--provisioning-model", choices=("SPOT", "STANDARD"), default="SPOT"
    )
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--parallelism", type=int, default=1)
    parser.add_argument("--max-cases-per-task", type=int, default=200)
    parser.add_argument("--max-input-gib", type=float, default=16.0)
    parser.add_argument("--max-task-seconds", type=int, default=86_400)
    parser.add_argument("--max-tasks", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--boot-disk-gib", type=int, default=50)
    parser.add_argument("--run-id", help="stable run identifier")
    parser.add_argument("--config", help="load a rendered config JSON instead")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sharpmod-toi-batch",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render-config", help="generate a config file")
    _add_config_arguments(render)
    render.add_argument("--output", default=str(DEFAULT_OUT / "config.json"))
    render.set_defaults(handler=render_config_command)

    pre = subparsers.add_parser("preflight", help="read-only audit; enables nothing")
    _add_config_arguments(pre)
    pre.add_argument(
        "--check-gcloud",
        action="store_true",
        help="also run read-only gcloud service lookups",
    )
    pre.add_argument("--report")
    pre.set_defaults(handler=preflight_command)

    bundle = subparsers.add_parser("bundle", help="build the source bundle")
    bundle.add_argument("--root", default=".")
    bundle.add_argument("--output", default="dist/sharpmod-source.tar.gz")
    bundle.add_argument(
        "--confirm-write",
        action="store_true",
        help="actually write the archive (default is a dry run)",
    )
    bundle.add_argument("--report")
    bundle.set_defaults(handler=bundle_command)

    shard = subparsers.add_parser("shard", help="event-indivisible shard split")
    _add_config_arguments(shard)
    shard.add_argument("--catalog", required=True)
    shard.add_argument("--output-dir")
    shard.set_defaults(handler=shard_command)

    plan = subparsers.add_parser("plan", help="render the full dry-run plan")
    _add_config_arguments(plan)
    plan.add_argument("--catalog")
    plan.add_argument("--cases", type=int, default=600)
    plan.add_argument("--bundle-report")
    plan.add_argument("--output-dir")
    plan.set_defaults(handler=plan_command)

    submit = subparsers.add_parser("submit", help="print the submit command")
    _add_config_arguments(submit)
    submit.add_argument("--confirm-submit", action="store_true")
    submit.set_defaults(handler=submit_command)

    status = subparsers.add_parser("status", help="print status/resume commands")
    _add_config_arguments(status)
    status.set_defaults(handler=status_command)

    merge = subparsers.add_parser(
        "verify-merge", help="verify and merge shard outputs"
    )
    merge.add_argument("--shard-dir", action="append", required=True)
    merge.add_argument("--expect-keys", action="append")
    merge.add_argument("--output")
    merge.set_defaults(handler=verify_merge_command)

    cleanup = subparsers.add_parser("cleanup", help="inventory cleanup targets")
    _add_config_arguments(cleanup)
    cleanup.add_argument("--confirm-delete", action="store_true")
    cleanup.add_argument("--output")
    cleanup.set_defaults(handler=cleanup_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except BatchPlanError as exc:
        print(f"sharpmod-toi-batch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
