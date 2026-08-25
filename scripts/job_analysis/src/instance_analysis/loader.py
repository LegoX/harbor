"""Load instance records for instance_analysis without the main pipeline."""

from __future__ import annotations

from src.config import PipelineConfig
from src.report_metadata import build_instance_records


def load_instance_records(cfg: PipelineConfig) -> list[dict]:
    """Derive instance_id + resolved records directly from job / report metadata.

    ``instance_analysis`` only needs the minimal resolved/unresolved records
    before joining dataset metadata, so this path intentionally avoids loading
    full trajectories.
    """
    return build_instance_records(cfg)
