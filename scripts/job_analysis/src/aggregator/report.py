"""Aggregator: distribution tables, co-occurrence matrix, per-instance JSONL."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DistributionTable:
    """A named distribution table with counts and percentages."""

    name: str
    headers: list[str]  # [category, count, percentage]
    rows: list[list[Any]]  # [[category, count, pct], ...]
    total: int = 0


@dataclass
class CoOccurrenceMatrix:
    """Co-occurrence counts between two categories."""

    row_labels: list[str]
    col_labels: list[str]
    matrix: list[list[int]]  # row x col


@dataclass
class AggregationReport:
    """Complete aggregation report."""

    primary_distribution: DistributionTable
    axis_distributions: dict[str, DistributionTable]
    flag_distribution: DistributionTable
    secondary_co_occurrence: CoOccurrenceMatrix | None
    verdict_distribution: DistributionTable
    summary: dict[str, Any]


def _compute_distribution(items: list[str], name: str) -> DistributionTable:
    """Compute distribution table from a list of category values."""
    counts = Counter(items)
    total = len(items)
    rows = []
    for cat, count in counts.most_common():
        pct = round(count / total * 100, 1) if total > 0 else 0.0
        rows.append([cat, count, pct])

    return DistributionTable(
        name=name,
        headers=["category", "count", "percentage"],
        rows=rows,
        total=total,
    )


def _compute_co_occurrence(
    primary_labels: list[str],
    secondary_labels: list[list[str]],
) -> CoOccurrenceMatrix:
    """Compute co-occurrence matrix between primary and secondary failures."""
    # Collect all unique secondary labels
    all_secondary: set[str] = set()
    for secs in secondary_labels:
        all_secondary.update(secs)
    all_primary = sorted(set(primary_labels))
    all_secondary = sorted(all_secondary)

    # Build matrix
    primary_idx = {p: i for i, p in enumerate(all_primary)}
    secondary_idx = {s: j for j, s in enumerate(all_secondary)}

    matrix = [[0] * len(all_secondary) for _ in range(len(all_primary))]

    for prim, secs in zip(primary_labels, secondary_labels):
        for sec in secs:
            if prim in primary_idx and sec in secondary_idx:
                matrix[primary_idx[prim]][secondary_idx[sec]] += 1

    return CoOccurrenceMatrix(
        row_labels=all_primary,
        col_labels=all_secondary,
        matrix=matrix,
    )


def format_table(table: DistributionTable) -> str:
    """Format a distribution table as a readable string."""
    # Calculate column widths
    col_widths = [len(h) for h in table.headers]
    for row in table.rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    # Build header
    header = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(table.headers))
    separator = "-+-".join("-" * w for w in col_widths)

    # Build rows
    lines = [f"## {table.name}", header, separator]
    for row in table.rows:
        line = " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))
        lines.append(line)
    lines.append(f"Total: {table.total}")

    return "\n".join(lines)


def format_co_occurrence(matrix: CoOccurrenceMatrix) -> str:
    """Format co-occurrence matrix as a readable string."""
    if not matrix.row_labels or not matrix.col_labels:
        return "## Secondary Co-occurrence\n(empty)"

    # Column width
    label_width = (
        max(len(label) for label in matrix.row_labels) if matrix.row_labels else 10
    )
    col_widths = [max(len(label), 3) for label in matrix.col_labels]

    lines = ["## Secondary Co-occurrence Matrix"]

    # Header
    header = " " * label_width + " | "
    header += " | ".join(
        label.ljust(w) for label, w in zip(matrix.col_labels, col_widths)
    )
    lines.append(header)

    separator = "-" * label_width + "-+-"
    separator += "-+-".join("-" * w for w in col_widths)
    lines.append(separator)

    # Rows
    for i, row_label in enumerate(matrix.row_labels):
        row = row_label.ljust(label_width) + " | "
        row += " | ".join(
            str(matrix.matrix[i][j]).ljust(w) for j, w in enumerate(col_widths)
        )
        lines.append(row)

    return "\n".join(lines)


def aggregate(
    instance_results: list[dict],
) -> AggregationReport:
    """Aggregate per-instance results into distribution reports."""
    if not instance_results:
        logger.warning("No instance results to aggregate")
        empty = DistributionTable("empty", ["category", "count", "percentage"], [], 0)
        return AggregationReport(
            primary_distribution=empty,
            axis_distributions={},
            flag_distribution=empty,
            secondary_co_occurrence=None,
            verdict_distribution=empty,
            summary={"total_instances": 0},
        )

    # Primary failure distribution
    primaries = [r["primary_failure"] for r in instance_results]
    primary_dist = _compute_distribution(primaries, "Primary Failure Distribution")

    # Axis distributions
    axis_names = [
        "localization",
        "diagnosis",
        "implementation",
        "tool_usage",
        "long_horizon",
    ]
    axis_dists = {}
    for axis in axis_names:
        values = [r["axes"].get(axis, "unknown") for r in instance_results]
        axis_dists[axis] = _compute_distribution(
            values, f"{axis.title()} Axis Distribution"
        )

    # Flag distribution
    all_flags: list[str] = []
    for r in instance_results:
        all_flags.extend(r.get("flags", []))
    flag_dist = _compute_distribution(all_flags, "Flag Distribution")

    # Verdict distribution
    verdicts = [r["correctness_verdict"] for r in instance_results]
    verdict_dist = _compute_distribution(verdicts, "Correctness Verdict Distribution")

    # Secondary co-occurrence
    secondaries = [r.get("secondary_failures", []) for r in instance_results]
    co_occurrence = _compute_co_occurrence(primaries, secondaries)

    # Summary stats
    primary_counts = Counter(primaries)
    summary = {
        "total_instances": len(instance_results),
        "unique_primary_categories": len(set(primaries)),
        "top_primary": primary_counts.most_common(1)[0][0] if primary_counts else None,
        "instances_with_flags": sum(1 for r in instance_results if r.get("flags")),
    }

    return AggregationReport(
        primary_distribution=primary_dist,
        axis_distributions=axis_dists,
        flag_distribution=flag_dist,
        secondary_co_occurrence=co_occurrence,
        verdict_distribution=verdict_dist,
        summary=summary,
    )


def write_report(
    report: AggregationReport,
    output_dir: Path,
    instance_results: list[dict],
    suffix: str = "",
) -> None:
    """Write report to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build file name suffix
    sfx = f"_{suffix}" if suffix else ""

    # Write per-instance JSONL
    instances_path = output_dir / f"instances{sfx}.jsonl"
    with open(instances_path, "w") as f:
        for result in instance_results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
    logger.info(
        "Wrote %d instance results to %s", len(instance_results), instances_path
    )

    # Write report JSON
    report_data = {
        "summary": report.summary,
        "primary_distribution": {
            "name": report.primary_distribution.name,
            "rows": report.primary_distribution.rows,
            "total": report.primary_distribution.total,
        },
        "axis_distributions": {
            k: {"name": v.name, "rows": v.rows, "total": v.total}
            for k, v in report.axis_distributions.items()
        },
        "flag_distribution": {
            "name": report.flag_distribution.name,
            "rows": report.flag_distribution.rows,
            "total": report.flag_distribution.total,
        },
        "verdict_distribution": {
            "name": report.verdict_distribution.name,
            "rows": report.verdict_distribution.rows,
            "total": report.verdict_distribution.total,
        },
    }

    if report.secondary_co_occurrence:
        report_data["secondary_co_occurrence"] = {
            "row_labels": report.secondary_co_occurrence.row_labels,
            "col_labels": report.secondary_co_occurrence.col_labels,
            "matrix": report.secondary_co_occurrence.matrix,
        }

    report_path = output_dir / f"report{sfx}.json"
    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)
    logger.info("Wrote report to %s", report_path)

    # Write human-readable text report
    title = (
        "SWE-bench Failure Analysis Report"
        if suffix in ("", "failed")
        else "SWE-bench Resolved Analysis Report"
    )
    text_path = output_dir / f"report{sfx}.txt"
    with open(text_path, "w") as f:
        f.write("=" * 60 + "\n")
        f.write(title + "\n")
        f.write("=" * 60 + "\n\n")
        f.write(
            "Hack scope note: D-type git-history probing is ignored for "
            "SWE-bench image-based tasks because the task images strip local "
            "repository git-log data.\n\n"
        )

        f.write(format_table(report.primary_distribution))
        f.write("\n\n")

        for axis_name, axis_dist in report.axis_distributions.items():
            f.write(format_table(axis_dist))
            f.write("\n\n")

        f.write(format_table(report.flag_distribution))
        f.write("\n\n")

        f.write(format_table(report.verdict_distribution))
        f.write("\n\n")

        if report.secondary_co_occurrence:
            f.write(format_co_occurrence(report.secondary_co_occurrence))
            f.write("\n\n")

        f.write("## Summary\n")
        for k, v in report.summary.items():
            f.write(f"  {k}: {v}\n")

    logger.info("Wrote text report to %s", text_path)


def _distribution_to_dict(table: DistributionTable) -> dict[str, float]:
    """Convert a DistributionTable to {category: percentage} dict."""
    return {row[0]: row[2] for row in table.rows}


def _format_comparison_table(
    name: str,
    failed_dict: dict[str, float],
    resolved_dict: dict[str, float],
) -> str:
    """Format a side-by-side comparison table for a distribution."""
    all_cats = sorted(set(failed_dict.keys()) | set(resolved_dict.keys()))

    # Column widths
    cat_width = max((len(c) for c in all_cats), default=10)
    cat_width = max(cat_width, len("category"))
    failed_header = "failed(%)"
    resolved_header = "resolved(%)"
    diff_header = "diff"

    lines = [f"## {name}"]
    header = (
        f"{'category'.ljust(cat_width)} | "
        f"{failed_header.ljust(11)} | "
        f"{resolved_header.ljust(12)} | "
        f"{diff_header}"
    )
    lines.append(header)
    separator = f"{'-' * cat_width}-+-{'-' * 11}-+-{'-' * 12}-+-{'-' * 6}"
    lines.append(separator)

    for cat in all_cats:
        f_pct = failed_dict.get(cat, 0.0)
        r_pct = resolved_dict.get(cat, 0.0)
        diff = round(r_pct - f_pct, 1)
        diff_str = f"+{diff}" if diff > 0 else f"{diff}"
        lines.append(
            f"{cat.ljust(cat_width)} | "
            f"{str(f_pct).ljust(11)} | "
            f"{str(r_pct).ljust(12)} | "
            f"{diff_str}"
        )

    return "\n".join(lines)


def _compute_avg_features(instance_results: list[dict]) -> dict[str, float]:
    """Compute average values for key numeric deterministic features."""
    if not instance_results:
        return {}
    keys = [
        "C1_file_read",
        "C2_func_read",
        "C3_file_alignment",
        "C4_func_alignment",
        "C5_test_executed",
        "diff_hunk_overlap",
        "loop_detected",
        "premature_stop",
        "tool_error_storm",
        "context_truncation",
    ]
    avgs = {}
    for k in keys:
        vals = [
            r["deterministic_features"].get(k, 0)
            for r in instance_results
            if r.get("deterministic_features")
        ]
        if vals:
            avg = (
                sum(bool(v) if isinstance(v, bool) else v for v in vals)
                / len(vals)
                * 100
            )
            avgs[k] = round(avg, 1)
    return avgs


def _format_feature_comparison(
    failed_avgs: dict[str, float],
    resolved_avgs: dict[str, float],
) -> str:
    """Format feature comparison table."""
    all_keys = sorted(set(failed_avgs.keys()) | set(resolved_avgs.keys()))
    if not all_keys:
        return "## Feature Comparison\n(no data)"

    key_width = max(len(k) for k in all_keys)
    key_width = max(key_width, len("feature"))

    lines = [
        "## Deterministic Feature Comparison (%, higher=better for C1-C5/overlap, lower=better for pathologies)"
    ]
    header = (
        f"{'feature'.ljust(key_width)} | "
        f"{'failed(%)'.ljust(11)} | "
        f"{'resolved(%)'.ljust(12)} | "
        f"{'diff'.ljust(6)}"
    )
    lines.append(header)
    separator = f"{'-' * key_width}-+-{'-' * 11}-+-{'-' * 12}-+-{'-' * 6}"
    lines.append(separator)

    for k in all_keys:
        f_val = failed_avgs.get(k, 0.0)
        r_val = resolved_avgs.get(k, 0.0)
        diff = round(r_val - f_val, 1)
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        lines.append(
            f"{k.ljust(key_width)} | "
            f"{str(f_val).ljust(11)} | "
            f"{str(r_val).ljust(12)} | "
            f"{diff_str.ljust(6)}"
        )

    return "\n".join(lines)


def write_comparison_report(
    failed_report: AggregationReport,
    resolved_report: AggregationReport,
    output_dir: Path,
    failed_results: list[dict],
    resolved_results: list[dict],
) -> None:
    """Write a comparison report between failed and resolved instances."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build comparison dicts for each distribution
    primary_failed = _distribution_to_dict(failed_report.primary_distribution)
    primary_resolved = _distribution_to_dict(resolved_report.primary_distribution)

    axis_comparisons = {}
    for axis_name in [
        "localization",
        "diagnosis",
        "implementation",
        "tool_usage",
        "long_horizon",
    ]:
        f_dict = _distribution_to_dict(
            failed_report.axis_distributions.get(
                axis_name, DistributionTable("", [], [], 0)
            )
        )
        r_dict = _distribution_to_dict(
            resolved_report.axis_distributions.get(
                axis_name, DistributionTable("", [], [], 0)
            )
        )
        axis_comparisons[axis_name] = (f_dict, r_dict)

    verdict_failed = _distribution_to_dict(failed_report.verdict_distribution)
    verdict_resolved = _distribution_to_dict(resolved_report.verdict_distribution)

    flag_failed = _distribution_to_dict(failed_report.flag_distribution)
    flag_resolved = _distribution_to_dict(resolved_report.flag_distribution)

    # Compute feature averages
    failed_avgs = _compute_avg_features(failed_results)
    resolved_avgs = _compute_avg_features(resolved_results)

    # Write text report
    text_path = output_dir / "score_comparison.txt"
    with open(text_path, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("SWE-bench Failed vs Resolved Comparison Report\n")
        f.write("=" * 70 + "\n\n")
        f.write(
            "Hack scope note: D-type git-history probing is ignored for "
            "SWE-bench image-based tasks because the task images strip local "
            "repository git-log data.\n\n"
        )

        f.write(
            f"Failed instances: {failed_report.summary.get('total_instances', 0)}\n"
        )
        f.write(
            f"Resolved instances: {resolved_report.summary.get('total_instances', 0)}\n\n"
        )

        # Primary attribution comparison
        f.write(
            _format_comparison_table(
                "Primary Attribution Distribution",
                primary_failed,
                primary_resolved,
            )
        )
        f.write("\n\n")

        # Axis comparisons
        for axis_name, (f_dict, r_dict) in axis_comparisons.items():
            f.write(
                _format_comparison_table(
                    f"{axis_name.title()} Axis Distribution",
                    f_dict,
                    r_dict,
                )
            )
            f.write("\n\n")

        # Verdict comparison
        f.write(
            _format_comparison_table(
                "Correctness Verdict Distribution",
                verdict_failed,
                verdict_resolved,
            )
        )
        f.write("\n\n")

        # Flag comparison
        f.write(
            _format_comparison_table(
                "Flag Distribution",
                flag_failed,
                flag_resolved,
            )
        )
        f.write("\n\n")

        # Feature comparison
        f.write(_format_feature_comparison(failed_avgs, resolved_avgs))
        f.write("\n\n")

        # Key findings
        f.write("## Key Findings\n")
        f.write(
            _generate_key_findings(
                primary_failed,
                primary_resolved,
                axis_comparisons,
                failed_avgs,
                resolved_avgs,
                failed_report,
                resolved_report,
            )
        )

    logger.info("Wrote comparison report to %s", text_path)

    # Write JSON report
    comparison_data = {
        "failed_total": failed_report.summary.get("total_instances", 0),
        "resolved_total": resolved_report.summary.get("total_instances", 0),
        "primary_attribution": {
            "failed": primary_failed,
            "resolved": primary_resolved,
        },
        "axis_distributions": {
            axis: {"failed": f_dict, "resolved": r_dict}
            for axis, (f_dict, r_dict) in axis_comparisons.items()
        },
        "verdict_distribution": {
            "failed": verdict_failed,
            "resolved": verdict_resolved,
        },
        "flag_distribution": {
            "failed": flag_failed,
            "resolved": flag_resolved,
        },
        "feature_averages": {
            "failed": failed_avgs,
            "resolved": resolved_avgs,
        },
    }

    json_path = output_dir / "score_comparison.json"
    with open(json_path, "w") as f:
        json.dump(comparison_data, f, indent=2, ensure_ascii=False)
    logger.info("Wrote comparison JSON to %s", json_path)


def _generate_key_findings(
    primary_failed: dict[str, float],
    primary_resolved: dict[str, float],
    axis_comparisons: dict[str, tuple[dict[str, float], dict[str, float]]],
    failed_avgs: dict[str, float],
    resolved_avgs: dict[str, float],
    failed_report: AggregationReport,
    resolved_report: AggregationReport,
) -> str:
    """Generate key findings from the comparison data."""
    lines = []

    # Localization comparison
    loc_f = axis_comparisons.get("localization", ({}, {}))
    loc_hit_diff = round(loc_f[1].get("hit", 0) - loc_f[0].get("hit", 0), 1)
    if abs(loc_hit_diff) >= 5:
        lines.append(
            f"  - Localization hit rate: resolved {loc_f[1].get('hit', 0)}% vs failed {loc_f[0].get('hit', 0)}% "
            f"(diff: {'+' if loc_hit_diff > 0 else ''}{loc_hit_diff}pp)"
        )

    # Diagnosis comparison
    diag = axis_comparisons.get("diagnosis", ({}, {}))
    diag_correct_diff = round(diag[1].get("correct", 0) - diag[0].get("correct", 0), 1)
    if abs(diag_correct_diff) >= 5:
        lines.append(
            f"  - Diagnosis correct rate: resolved {diag[1].get('correct', 0)}% vs failed {diag[0].get('correct', 0)}% "
            f"(diff: {'+' if diag_correct_diff > 0 else ''}{diag_correct_diff}pp)"
        )

    # Implementation comparison
    impl = axis_comparisons.get("implementation", ({}, {}))
    impl_correct_diff = round(impl[1].get("correct", 0) - impl[0].get("correct", 0), 1)
    if abs(impl_correct_diff) >= 5:
        lines.append(
            f"  - Implementation correct rate: resolved {impl[1].get('correct', 0)}% vs failed {impl[0].get('correct', 0)}% "
            f"(diff: {'+' if impl_correct_diff > 0 else ''}{impl_correct_diff}pp)"
        )

    # C1-C5 feature comparison
    for ck in [
        "C1_file_read",
        "C2_func_read",
        "C3_file_alignment",
        "C4_func_alignment",
        "C5_test_executed",
    ]:
        diff = round(resolved_avgs.get(ck, 0) - failed_avgs.get(ck, 0), 1)
        if abs(diff) >= 5:
            lines.append(
                f"  - {ck}: resolved {resolved_avgs.get(ck, 0)}% vs failed {failed_avgs.get(ck, 0)}% "
                f"(diff: {'+' if diff > 0 else ''}{diff}pp)"
            )

    # Hunk overlap
    hunk_diff = round(
        resolved_avgs.get("diff_hunk_overlap", 0)
        - failed_avgs.get("diff_hunk_overlap", 0),
        1,
    )
    if abs(hunk_diff) >= 5:
        lines.append(
            f"  - Diff hunk overlap: resolved {resolved_avgs.get('diff_hunk_overlap', 0)}% vs failed {failed_avgs.get('diff_hunk_overlap', 0)}% "
            f"(diff: {'+' if hunk_diff > 0 else ''}{hunk_diff}pp)"
        )

    # Pathology comparison
    for pk in [
        "loop_detected",
        "premature_stop",
        "tool_error_storm",
        "context_truncation",
    ]:
        diff = round(resolved_avgs.get(pk, 0) - failed_avgs.get(pk, 0), 1)
        if abs(diff) >= 5:
            lines.append(
                f"  - {pk}: resolved {resolved_avgs.get(pk, 0)}% vs failed {failed_avgs.get(pk, 0)}% "
                f"(diff: {'+' if diff > 0 else ''}{diff}pp)"
            )

    if not lines:
        lines.append("  (No significant differences found)")

    return "\n".join(lines) + "\n"
