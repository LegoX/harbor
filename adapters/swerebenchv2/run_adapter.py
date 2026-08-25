from __future__ import annotations

import argparse
from pathlib import Path

from adapter import SWERebenchV2ToHarbor


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert SWE-rebench-V2 instance(s) to Harbor task directories"
    )

    # mode flags
    ap.add_argument(
        "--instance-id",
        type=str,
        help="Single SWE-rebench-V2 instance_id. If provided, overrides --all.",
    )
    ap.add_argument(
        "--all",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Convert all instances (default: True). Use --no-all to disable.",
    )

    # single mode args
    ap.add_argument(
        "--task-id",
        type=str,
        help="Local task directory name to create (default: instance-id when single)",
    )

    # general
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent.parent
        / "datasets"
        / "swerebenchv2",
        help="Output Harbor tasks root directory (default: ../../datasets/swerebenchv2)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=3000.0,
        help="Agent/verifier timeout seconds",
    )
    ap.add_argument("--cpus", type=int, default=2, help="CPUs per generated task")
    ap.add_argument(
        "--memory-mb",
        type=int,
        default=8192,
        help="Memory limit in MiB per generated task",
    )
    ap.add_argument(
        "--storage-mb",
        type=int,
        default=20480,
        help="Storage limit in MiB per generated task",
    )
    ap.add_argument(
        "--template-dir",
        type=Path,
        default=None,
        help="Override template directory (defaults to ./template next to adapter.py)",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite target dirs if they already exist",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of instances to convert when using --all",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of task-generation threads (default: 8)",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help=(
            "Only include instances of the specified programming language "
            "(e.g., python, go, rust, typescript). Case-insensitive."
        ),
    )
    ap.add_argument(
        "--exclude-lang",
        type=str,
        action="append",
        default=None,
        help=(
            "Exclude instances of the specified programming language. "
            "Can be passed multiple times. Case-insensitive."
        ),
    )

    args = ap.parse_args()

    if not args.all and not args.instance_id:
        ap.error("You used --no-all but did not provide --instance-id.")
    if args.workers < 1:
        ap.error("--workers must be at least 1.")

    conv = SWERebenchV2ToHarbor(
        harbor_tasks_root=args.output_dir,
        max_timeout_sec=args.timeout,
        template_dir=args.template_dir,
        cpus=args.cpus,
        memory_mb=args.memory_mb,
        storage_mb=args.storage_mb,
    )

    if args.instance_id:
        local = (args.task_id or args.instance_id).lower()
        out = conv.generate_task(
            args.instance_id,
            local,
            overwrite=args.overwrite,
        )
        print(f"Harbor task created at: {out}")
        return

    ids = conv.get_all_ids()

    if args.language or args.exclude_lang:
        before = len(ids)
        ids = conv.get_ids_by_languages(
            include_languages=[args.language] if args.language else None,
            exclude_languages=args.exclude_lang,
        )

        filter_parts = []
        if args.language:
            filter_parts.append(f"include language '{args.language}'")
        if args.exclude_lang:
            excluded = ", ".join(f"'{language}'" for language in args.exclude_lang)
            filter_parts.append(f"exclude language(s) {excluded}")

        print(
            f"Filtered to {len(ids)} instance(s) using {', '.join(filter_parts)} "
            f"(excluded {before - len(ids)})."
        )

    if args.limit is not None:
        ids = ids[: args.limit]

    print(f"Converting {len(ids)} SWE-rebench-V2 instances into {args.output_dir} ...")
    ok, bad = conv.generate_many(
        ids,
        name_fn=lambda iid: iid.lower(),
        overwrite=args.overwrite,
        max_workers=args.workers,
    )
    print(f"Done. Success: {len(ok)}  Failures: {len(bad)}")
    if bad:
        print("Failures:")
        for iid, reason in bad:
            print(f"  - {iid}: {reason}")


if __name__ == "__main__":
    main()
