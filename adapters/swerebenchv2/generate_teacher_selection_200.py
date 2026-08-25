from __future__ import annotations

import argparse
from pathlib import Path

from adapter import SWERebenchV2ToHarbor


SCRIPT_DIR = Path(__file__).resolve().parent
TASK_IDS_PATH = SCRIPT_DIR / "swerebenchv2_teacher_selection_200.txt"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR.parent.parent / "datasets" / "swerebenchv2-200-260429"


def load_task_ids(path: Path) -> list[str]:
    task_ids = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(task_ids) != 200:
        raise ValueError(f"Expected 200 task ids in {path}, found {len(task_ids)}")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError(f"Duplicate task ids found in {path}")
    return task_ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the fixed 200-task SWE-rebench-V2 teacher-selection subset."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Output Harbor tasks root directory "
            "(default: ../../datasets/swerebenchv2-200-260429)"
        ),
    )
    parser.add_argument(
        "--task-ids-file",
        type=Path,
        default=TASK_IDS_PATH,
        help="Path to the fixed task id list",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3000.0,
        help="Agent/verifier timeout seconds",
    )
    parser.add_argument(
        "--template-dir",
        type=Path,
        default=None,
        help="Override template directory (defaults to ./template next to adapter.py)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing task directories",
    )

    args = parser.parse_args()
    task_ids = load_task_ids(args.task_ids_file)
    converter = SWERebenchV2ToHarbor(
        harbor_tasks_root=args.output_dir,
        max_timeout_sec=args.timeout,
        template_dir=args.template_dir,
    )

    print(
        f"Converting fixed teacher-selection subset "
        f"({len(task_ids)} SWE-rebench-V2 instances) into {args.output_dir} ..."
    )
    ok, bad = converter.generate_many(
        task_ids,
        name_fn=lambda instance_id: instance_id.lower(),
        overwrite=args.overwrite,
    )
    print(f"Done. Success: {len(ok)}  Failures: {len(bad)}")
    if bad:
        print("Failures:")
        for instance_id, reason in bad:
            print(f"  - {instance_id}: {reason}")


if __name__ == "__main__":
    main()
