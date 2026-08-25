"""JSONL read/write helpers for traj_analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Load all records from a JSONL file."""
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON at {path}:{line_no}: {e}") from e
            if isinstance(record, dict):
                records.append(record)
    return records


def save_jsonl(path: Path | str, records: list[dict[str, Any]]) -> None:
    """Write records to a JSONL file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
