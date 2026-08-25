import json
from pathlib import Path

from src.traj_analysis.comparison import _read_last_jsonl_record


def test_read_last_jsonl_record_from_file_tail(tmp_path: Path):
    path = tmp_path / "trajectory.jsonl"
    path.write_text(
        "\n".join(json.dumps({"idx": i}) for i in range(3)) + "\n\n",
        encoding="utf-8",
    )

    assert _read_last_jsonl_record(path) == {"idx": 2}
