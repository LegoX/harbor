"""CLI entry point to convert OpenSWE instances into Harbor task directories."""

from __future__ import annotations

import argparse
from pathlib import Path

from adapter import CONFIGS, OSS_CONFIG, OpenSWELoader, OpenSWEToHarbor
from utils import load_filtered_ids


def _default_output_dir(base: Path, config: str, filtered: bool) -> Path:
    name = f"{config}_filtered" if filtered else config
    return base / name


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert GAIR/OpenSWE instance(s) to Harbor task directories"
    )

    ap.add_argument(
        "--config",
        choices=CONFIGS,
        default=OSS_CONFIG,
        help=f"OpenSWE dataset config to convert (default: {OSS_CONFIG}).",
    )
    ap.add_argument(
        "--instance-id",
        type=str,
        action="append",
        default=None,
        help="Convert only this instance_id (repeatable).",
    )
    ap.add_argument(
        "--filtered",
        action="store_true",
        help="Restrict to the difficulty-filtered subset (filtered_ids.csv).",
    )
    ap.add_argument(
        "--filtered-ids-csv",
        type=Path,
        default=None,
        help="Local path to filtered_ids.csv (otherwise downloaded from HF).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of instances to convert.",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Output directory. Defaults to datasets/<config>[_filtered] "
            "relative to the repo root."
        ),
    )
    ap.add_argument(
        "--base-output-dir",
        type=Path,
        default=Path("../../datasets"),
        help="Base directory used when --output-dir is not given.",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=3600.0,
        help="Agent/verifier timeout seconds.",
    )
    ap.add_argument(
        "--template-dir",
        type=Path,
        default=None,
        help="Override template directory (defaults to ./template next to adapter.py).",
    )
    ap.add_argument(
        "--github-base",
        type=str,
        default="https://github.com",
        help="Base URL used to clone source repositories.",
    )
    ap.add_argument(
        "--no-clone",
        action="store_true",
        help="Skip cloning the source repo build context (for quick inspection).",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite target dirs if they already exist.",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of concurrent workers. Cloning is network-bound, so values "
        "like 8-16 give a large speedup (default: 1).",
    )

    args = ap.parse_args()

    output_dir = args.output_dir or _default_output_dir(
        args.base_output_dir, args.config, args.filtered
    )

    instance_ids: set[str] | None = None
    if args.instance_id:
        instance_ids = set(args.instance_id)
    if args.filtered:
        filtered = load_filtered_ids(args.filtered_ids_csv)
        instance_ids = filtered if instance_ids is None else (instance_ids & filtered)

    conv = OpenSWEToHarbor(
        config=args.config,
        out_root=output_dir,
        max_timeout_sec=args.timeout,
        template_dir=args.template_dir,
        github_base=args.github_base,
        clone_repos=not args.no_clone,
    )

    loader = OpenSWELoader(args.config)
    records = loader.iter_records(instance_ids=instance_ids, limit=args.limit)

    print(
        f"Converting OpenSWE config '{args.config}'"
        f"{' (filtered)' if args.filtered else ''} into {output_dir} ..."
    )
    ok, bad = conv.generate_from_records(
        records, overwrite=args.overwrite, workers=args.workers
    )
    print(f"Done. Success: {len(ok)}  Failures: {len(bad)}")
    if bad:
        print("Failures:")
        for iid, reason in bad:
            print(f"  - {iid}: {reason}")


if __name__ == "__main__":
    main()
