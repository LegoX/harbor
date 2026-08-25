"""Task 分析聚合与报告生成."""

from __future__ import annotations

import json
import logging
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.task_analysis.features import TaskDifficultyFeatures
from src.task_analysis.classifier import TaskClassification

logger = logging.getLogger(__name__)


@dataclass
class TaskAnalysisReport:
    """Task 分析聚合报告."""

    # 难度分布
    difficulty_tier_dist: dict[str, dict]  # {tier: {count, pct}}
    swebench_difficulty_dist: dict[str, dict]  # {swebench_level: {count, pct}}

    # 难度指标统计 (按 resolved/failed 分组)
    difficulty_stats_by_group: dict[str, dict]  # {group: {metric: {mean, median, ...}}}

    # domain 分布
    domain_dist: dict[str, dict]  # {domain: {count, pct}}
    domain_by_group: dict[str, dict]  # {group: {domain: pct}}

    # bug 类型分布
    bug_type_dist: dict[str, dict]  # {bug_type: {count, pct}}
    bug_type_by_group: dict[str, dict]  # {group: {bug_type: pct}}

    # 交叉分析: 难度 × 解决率
    difficulty_resolve_rate: dict[str, dict]  # {tier: {total, resolved, rate}}

    # 交叉分析: domain × 解决率
    domain_resolve_rate: dict[str, dict]  # {domain: {total, resolved, rate}}

    # 交叉分析: bug_type × 解决率
    bug_type_resolve_rate: dict[str, dict]  # {bug_type: {total, resolved, rate}}

    summary: dict[str, Any]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _dist(counter: Counter, total: int) -> dict[str, dict]:
    """将 Counter 转为 {key: {count, pct}} 字典."""
    return {
        k: {"count": c, "pct": round(c / total * 100, 1)}
        for k, c in counter.most_common()
    }


def _numeric_stats(values: list[float]) -> dict[str, float]:
    """计算数值统计量."""
    if not values:
        return {"mean": 0, "median": 0, "min": 0, "max": 0, "p25": 0, "p75": 0}
    sorted_v = sorted(values)
    n = len(sorted_v)
    return {
        "mean": round(statistics.mean(sorted_v), 2),
        "median": round(statistics.median(sorted_v), 2),
        "min": sorted_v[0],
        "max": sorted_v[-1],
        "p25": round(sorted_v[max(0, n // 4)], 2),
        "p75": round(sorted_v[min(n - 1, 3 * n // 4)], 2),
    }


def _group_pct(counter: Counter, total: int) -> dict[str, float]:
    """将 Counter 转为 {key: pct} 字典."""
    return {k: round(c / total * 100, 1) for k, c in counter.most_common()}


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def aggregate_task_analysis(
    features_list: list[TaskDifficultyFeatures],
    classifications_list: list[TaskClassification],
    resolved_set: set[str],
) -> TaskAnalysisReport:
    """聚合 task 分析结果."""
    total = len(features_list)
    if total == 0:
        return TaskAnalysisReport(
            difficulty_tier_dist={},
            swebench_difficulty_dist={},
            difficulty_stats_by_group={},
            domain_dist={},
            domain_by_group={},
            bug_type_dist={},
            bug_type_by_group={},
            difficulty_resolve_rate={},
            domain_resolve_rate={},
            bug_type_resolve_rate={},
            summary={"total_instances": 0},
        )

    # 建立查找表
    cls_by_id: dict[str, TaskClassification] = {
        c.instance_id: c for c in classifications_list
    }

    # ---- 难度分布 ----
    tier_counter = Counter(f.difficulty_tier for f in features_list)
    swebench_counter = Counter(f.swebench_difficulty for f in features_list)

    # ---- 难度指标按 resolved/failed 分组统计 ----
    numeric_fields = [
        "gold_files_modified",
        "gold_lines_changed",
        "gold_lines_added",
        "gold_lines_removed",
        "gold_hunk_count",
        "fail_to_pass_count",
        "problem_length",
    ]
    difficulty_stats_by_group: dict[str, dict] = {}
    for group_name, group_filter in [("resolved", True), ("failed", False)]:
        group_feats = [
            f for f in features_list if (f.instance_id in resolved_set) == group_filter
        ]
        if not group_feats:
            continue
        stats: dict[str, dict] = {}
        for field_name in numeric_fields:
            values = [getattr(f, field_name) for f in group_feats]
            stats[field_name] = _numeric_stats(values)
        difficulty_stats_by_group[group_name] = stats

    # ---- domain 分布 ----
    domain_counter = Counter(
        cls_by_id[f.instance_id].domain
        for f in features_list
        if f.instance_id in cls_by_id
    )

    # ---- bug_type 分布 ----
    bug_type_counter = Counter(
        cls_by_id[f.instance_id].bug_type
        for f in features_list
        if f.instance_id in cls_by_id
    )

    # ---- 按 resolved/failed 分组的 domain/bug_type 分布 ----
    domain_by_group: dict[str, dict] = {}
    bug_type_by_group: dict[str, dict] = {}
    for group_name, group_filter in [("resolved", True), ("failed", False)]:
        group_ids = {
            f.instance_id
            for f in features_list
            if (f.instance_id in resolved_set) == group_filter
        }
        group_cls = [c for c in classifications_list if c.instance_id in group_ids]
        n = len(group_cls) or 1
        domain_by_group[group_name] = _group_pct(
            Counter(c.domain for c in group_cls), n
        )
        bug_type_by_group[group_name] = _group_pct(
            Counter(c.bug_type for c in group_cls), n
        )

    # ---- 交叉分析: 难度 × 解决率 ----
    difficulty_resolve_rate: dict[str, dict] = {}
    for tier in sorted(tier_counter.keys()):
        tier_ids = {f.instance_id for f in features_list if f.difficulty_tier == tier}
        tier_total = len(tier_ids)
        tier_resolved = len(tier_ids & resolved_set)
        difficulty_resolve_rate[tier] = {
            "total": tier_total,
            "resolved": tier_resolved,
            "rate": round(tier_resolved / tier_total * 100, 1) if tier_total else 0,
        }

    # ---- 交叉分析: domain × 解决率 ----
    domain_resolve_rate: dict[str, dict] = {}
    for domain in sorted(domain_counter.keys()):
        domain_ids = {c.instance_id for c in classifications_list if c.domain == domain}
        domain_total = len(domain_ids)
        domain_resolved = len(domain_ids & resolved_set)
        domain_resolve_rate[domain] = {
            "total": domain_total,
            "resolved": domain_resolved,
            "rate": round(domain_resolved / domain_total * 100, 1)
            if domain_total
            else 0,
        }

    # ---- 交叉分析: bug_type × 解决率 ----
    bug_type_resolve_rate: dict[str, dict] = {}
    for bt in sorted(bug_type_counter.keys()):
        bt_ids = {c.instance_id for c in classifications_list if c.bug_type == bt}
        bt_total = len(bt_ids)
        bt_resolved = len(bt_ids & resolved_set)
        bug_type_resolve_rate[bt] = {
            "total": bt_total,
            "resolved": bt_resolved,
            "rate": round(bt_resolved / bt_total * 100, 1) if bt_total else 0,
        }

    summary = {
        "total_instances": total,
        "resolved_instances": len(
            resolved_set & {f.instance_id for f in features_list}
        ),
        "failed_instances": total
        - len(resolved_set & {f.instance_id for f in features_list}),
        "overall_resolve_rate": round(
            len(resolved_set & {f.instance_id for f in features_list}) / total * 100, 1
        ),
    }

    return TaskAnalysisReport(
        difficulty_tier_dist=_dist(tier_counter, total),
        swebench_difficulty_dist=_dist(swebench_counter, total),
        difficulty_stats_by_group=difficulty_stats_by_group,
        domain_dist=_dist(domain_counter, total),
        domain_by_group=domain_by_group,
        bug_type_dist=_dist(bug_type_counter, total),
        bug_type_by_group=bug_type_by_group,
        difficulty_resolve_rate=difficulty_resolve_rate,
        domain_resolve_rate=domain_resolve_rate,
        bug_type_resolve_rate=bug_type_resolve_rate,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# 报告格式化
# ---------------------------------------------------------------------------


def _format_dist_table(name: str, dist: dict[str, dict]) -> str:
    """格式化分布表."""
    if not dist:
        return f"## {name}\n(no data)\n"

    cat_width = max((len(k) for k in dist), default=10)
    cat_width = max(cat_width, len("category"))
    lines = [f"## {name}"]
    header = f"{'category'.ljust(cat_width)} | {'count'.ljust(7)} | {'pct(%)'.ljust(7)}"
    lines.append(header)
    lines.append("-" * cat_width + "-+-" + "-" * 7 + "-+-" + "-" * 7)
    for k, v in dist.items():
        lines.append(
            f"{k.ljust(cat_width)} | {str(v['count']).ljust(7)} | {str(v['pct']).ljust(7)}"
        )
    return "\n".join(lines)


def _format_resolve_rate_table(name: str, data: dict[str, dict]) -> str:
    """格式化解决率交叉表."""
    if not data:
        return f"## {name}\n(no data)\n"

    cat_width = max((len(k) for k in data), default=10)
    cat_width = max(cat_width, len("category"))
    lines = [f"## {name}"]
    header = f"{'category'.ljust(cat_width)} | {'total'.ljust(7)} | {'resolved'.ljust(9)} | {'rate(%)'.ljust(8)}"
    lines.append(header)
    lines.append("-" * cat_width + "-+-" + "-" * 7 + "-+-" + "-" * 9 + "-+-" + "-" * 8)
    for k, v in data.items():
        lines.append(
            f"{k.ljust(cat_width)} | {str(v['total']).ljust(7)} | "
            f"{str(v['resolved']).ljust(9)} | {str(v['rate']).ljust(8)}"
        )
    return "\n".join(lines)


def _format_group_comparison(name: str, by_group: dict[str, dict[str, float]]) -> str:
    """格式化 resolved vs failed 分组对比表."""
    if not by_group:
        return f"## {name}\n(no data)\n"

    resolved = by_group.get("resolved", {})
    failed = by_group.get("failed", {})
    all_cats = sorted(set(resolved.keys()) | set(failed.keys()))

    cat_width = max((len(c) for c in all_cats), default=10)
    cat_width = max(cat_width, len("category"))

    lines = [f"## {name}"]
    header = f"{'category'.ljust(cat_width)} | {'failed(%)'.ljust(11)} | {'resolved(%)'.ljust(12)} | {'diff'.ljust(6)}"
    lines.append(header)
    lines.append(
        "-" * cat_width + "-+-" + "-" * 11 + "-+-" + "-" * 12 + "-+-" + "-" * 6
    )

    for cat in all_cats:
        f_pct = failed.get(cat, 0.0)
        r_pct = resolved.get(cat, 0.0)
        diff = round(r_pct - f_pct, 1)
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        lines.append(
            f"{cat.ljust(cat_width)} | {str(f_pct).ljust(11)} | "
            f"{str(r_pct).ljust(12)} | {diff_str.ljust(6)}"
        )
    return "\n".join(lines)


def _format_difficulty_stats(name: str, stats_by_group: dict[str, dict]) -> str:
    """格式化难度指标统计对比表."""
    if not stats_by_group:
        return f"## {name}\n(no data)\n"

    resolved_stats = stats_by_group.get("resolved", {})
    failed_stats = stats_by_group.get("failed", {})
    all_metrics = sorted(set(resolved_stats.keys()) | set(failed_stats.keys()))

    metric_width = max((len(m) for m in all_metrics), default=10)
    metric_width = max(metric_width, len("metric"))

    lines = [f"## {name}"]
    header = (
        f"{'metric'.ljust(metric_width)} | "
        f"{'failed_mean'.ljust(12)} | {'failed_med'.ljust(11)} | "
        f"{'resolved_mean'.ljust(14)} | {'resolved_med'.ljust(13)}"
    )
    lines.append(header)
    lines.append(
        "-" * metric_width
        + "-+-"
        + "-" * 12
        + "-+-"
        + "-" * 11
        + "-+-"
        + "-" * 14
        + "-+-"
        + "-" * 13
    )

    for metric in all_metrics:
        f_s = failed_stats.get(metric, {})
        r_s = resolved_stats.get(metric, {})
        lines.append(
            f"{metric.ljust(metric_width)} | "
            f"{str(f_s.get('mean', 0)).ljust(12)} | {str(f_s.get('median', 0)).ljust(11)} | "
            f"{str(r_s.get('mean', 0)).ljust(14)} | {str(r_s.get('median', 0)).ljust(13)}"
        )
    return "\n".join(lines)


def _generate_task_findings(report: TaskAnalysisReport) -> str:
    """从报告数据中提取关键发现."""
    lines = []

    # 难度与解决率
    for tier in ["easy", "medium", "hard", "very_hard"]:
        data = report.difficulty_resolve_rate.get(tier)
        if data and data["total"] > 0:
            lines.append(
                f"  - {tier}: resolve rate {data['rate']}% ({data['resolved']}/{data['total']})"
            )

    # domain 与解决率: 找最高/最低
    if report.domain_resolve_rate:
        sorted_domains = sorted(
            report.domain_resolve_rate.items(),
            key=lambda x: x[1]["rate"],
        )
        if sorted_domains:
            lowest = sorted_domains[0]
            highest = sorted_domains[-1]
            lines.append(
                f"  - Hardest domain: {lowest[0]} ({lowest[1]['rate']}% resolve rate)"
            )
            lines.append(
                f"  - Easiest domain: {highest[0]} ({highest[1]['rate']}% resolve rate)"
            )

    # bug_type 与解决率: 找最高/最低
    if report.bug_type_resolve_rate:
        sorted_bugs = sorted(
            report.bug_type_resolve_rate.items(),
            key=lambda x: x[1]["rate"],
        )
        if sorted_bugs:
            lowest = sorted_bugs[0]
            highest = sorted_bugs[-1]
            lines.append(
                f"  - Hardest bug type: {lowest[0]} ({lowest[1]['rate']}% resolve rate, {lowest[1]['total']} instances)"
            )
            lines.append(
                f"  - Easiest bug type: {highest[0]} ({highest[1]['rate']}% resolve rate, {highest[1]['total']} instances)"
            )

    # 难度指标差异
    res_stats = report.difficulty_stats_by_group.get("resolved", {})
    fail_stats = report.difficulty_stats_by_group.get("failed", {})
    for metric in ["gold_files_modified", "gold_lines_changed", "gold_hunk_count"]:
        r_mean = res_stats.get(metric, {}).get("mean", 0)
        f_mean = fail_stats.get(metric, {}).get("mean", 0)
        diff = round(r_mean - f_mean, 2)
        if abs(diff) >= 0.5:
            direction = "higher" if diff > 0 else "lower"
            lines.append(
                f"  - {metric}: resolved avg {r_mean} vs failed avg {f_mean} "
                f"({direction} for resolved)"
            )

    if not lines:
        lines.append("  (No significant findings)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 写报告
# ---------------------------------------------------------------------------


def write_task_analysis_report(
    report: TaskAnalysisReport,
    output_dir: Path,
    features_list: list[TaskDifficultyFeatures],
    classifications_list: list[TaskClassification],
) -> None:
    """写 task 分析报告 (txt + json)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- 写 per-instance task 特征 JSONL ----
    task_instances_path = output_dir / "task_instances.jsonl"
    cls_by_id = {c.instance_id: c for c in classifications_list}
    with open(task_instances_path, "w") as f:
        for feat in features_list:
            cls = cls_by_id.get(feat.instance_id)
            record = {
                "instance_id": feat.instance_id,
                "difficulty_tier": feat.difficulty_tier,
                "swebench_difficulty": feat.swebench_difficulty,
                "gold_files_modified": feat.gold_files_modified,
                "gold_lines_changed": feat.gold_lines_changed,
                "gold_lines_added": feat.gold_lines_added,
                "gold_lines_removed": feat.gold_lines_removed,
                "gold_hunk_count": feat.gold_hunk_count,
                "gold_cross_file": feat.gold_cross_file,
                "gold_cross_dir": feat.gold_cross_dir,
                "fail_to_pass_count": feat.fail_to_pass_count,
                "pass_to_pass_count": feat.pass_to_pass_count,
                "problem_length": feat.problem_length,
                "problem_has_code_block": feat.problem_has_code_block,
                "problem_has_traceback": feat.problem_has_traceback,
                "problem_has_error_msg": feat.problem_has_error_msg,
                "bug_type": cls.bug_type if cls else "unknown",
                "domain": cls.domain if cls else "unknown",
                "sub_domain": cls.sub_domain if cls else "unknown",
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info(
        "Wrote %d task instance features to %s", len(features_list), task_instances_path
    )

    # ---- 写 JSON 报告 ----
    json_data = {
        "summary": report.summary,
        "difficulty_tier_distribution": report.difficulty_tier_dist,
        "swebench_difficulty_distribution": report.swebench_difficulty_dist,
        "difficulty_stats_by_group": report.difficulty_stats_by_group,
        "domain_distribution": report.domain_dist,
        "domain_by_group": report.domain_by_group,
        "bug_type_distribution": report.bug_type_dist,
        "bug_type_by_group": report.bug_type_by_group,
        "difficulty_resolve_rate": report.difficulty_resolve_rate,
        "domain_resolve_rate": report.domain_resolve_rate,
        "bug_type_resolve_rate": report.bug_type_resolve_rate,
    }
    json_path = output_dir / "report_task_analysis.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    logger.info("Wrote task analysis JSON to %s", json_path)

    # ---- 写文本报告 ----
    txt_path = output_dir / "report_task_analysis.txt"
    with open(txt_path, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("SWE-bench Task Analysis Report\n")
        f.write("  (Difficulty / Domain / Bug Type Profiling)\n")
        f.write("=" * 70 + "\n\n")

        f.write(f"Total instances: {report.summary.get('total_instances', 0)}\n")
        f.write(f"Resolved: {report.summary.get('resolved_instances', 0)}\n")
        f.write(f"Failed: {report.summary.get('failed_instances', 0)}\n")
        f.write(
            f"Overall resolve rate: {report.summary.get('overall_resolve_rate', 0)}%\n\n"
        )

        # 难度分布
        f.write(
            _format_dist_table(
                "Difficulty Tier Distribution", report.difficulty_tier_dist
            )
        )
        f.write("\n\n")

        f.write(
            _format_dist_table(
                "SWE-bench Native Difficulty Distribution",
                report.swebench_difficulty_dist,
            )
        )
        f.write("\n\n")

        # 难度 × 解决率
        f.write(
            _format_resolve_rate_table(
                "Resolve Rate by Difficulty Tier", report.difficulty_resolve_rate
            )
        )
        f.write("\n\n")

        # 难度指标统计对比
        f.write(
            _format_difficulty_stats(
                "Difficulty Metrics: Failed vs Resolved (mean / median)",
                report.difficulty_stats_by_group,
            )
        )
        f.write("\n\n")

        # domain 分布
        f.write(_format_dist_table("Domain Distribution", report.domain_dist))
        f.write("\n\n")

        f.write(
            _format_resolve_rate_table(
                "Resolve Rate by Domain", report.domain_resolve_rate
            )
        )
        f.write("\n\n")

        f.write(
            _format_group_comparison(
                "Domain Distribution: Failed vs Resolved",
                report.domain_by_group,
            )
        )
        f.write("\n\n")

        # bug_type 分布
        f.write(_format_dist_table("Bug Type Distribution", report.bug_type_dist))
        f.write("\n\n")

        f.write(
            _format_resolve_rate_table(
                "Resolve Rate by Bug Type", report.bug_type_resolve_rate
            )
        )
        f.write("\n\n")

        f.write(
            _format_group_comparison(
                "Bug Type Distribution: Failed vs Resolved",
                report.bug_type_by_group,
            )
        )
        f.write("\n\n")

        # 关键发现
        f.write("## Key Findings\n")
        f.write(_generate_task_findings(report))
        f.write("\n")

    logger.info("Wrote task analysis text report to %s", txt_path)
