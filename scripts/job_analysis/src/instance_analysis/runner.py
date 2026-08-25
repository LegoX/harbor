"""Cross-tabulate metadata tags / difficulty against resolved/unresolved.

Inputs
------
* Per-instance records with at least ``instance_id`` and ``resolved`` (bool).
  These can come from ``instances.jsonl`` (main pipeline output) or be derived
  directly from Harbor / OpenHands job metadata via ``load_instance_records``.
* ``<dataset_dir>/<instance_id>/task.toml`` – Harbor task config whose
  ``[metadata]`` block carries the tags produced by
  ``scripts/task_analysis/tag_task_metadata.py``: ``difficulty`` (easy / medium
  / hard) and a 4-part ``tags`` list ``[language, area, topic, bug_class]``.
  ``difficulty`` is mapped to ``difficulty_label`` and also encoded as an
  ordinal ``difficulty_score`` (easy=1, medium=2, hard=3) so it can be
  correlated against ``resolved``.

Outputs (under ``output_dir / instance_analysis/``)
---------------------------------------------
* ``contingency_difficulty_label.csv`` / ``.txt``
* ``contingency_tag1.csv`` / ``.txt``  (first tag: language)
* ``contingency_tag2.csv`` / ``.txt``  (second tag: area)
* ``contingency_tag3.csv`` / ``.txt``  (third tag: topic / framework)
* ``contingency_tag4.csv`` / ``.txt``  (fourth tag: bug_class)
* ``correlations.json`` / ``.txt`` – Pearson/Spearman/point-biserial of
  numeric features (``difficulty_score``, every ``score_dimensions[*].score``
  / ``raw``, every ``metrics[*]``) vs. ``resolved``
* ``summary.json`` – machine-readable rollup of everything above

NOTE: Original implementation could not be fully recovered from Claude
conversation transcripts (~46% coverage in runner.py.partial). This is a
complete reimplementation matching observed output format.

P-values may differ from original outputs in the last 2-3 significant digits
due to scipy version differences and floating-point precision.
"""

from __future__ import annotations

import json
import logging
import math
import tomllib
from collections import defaultdict
from pathlib import Path

from scipy.stats import t as scipy_t

logger = logging.getLogger(__name__)

# Ordinal encoding for the task.toml difficulty label, so difficulty can be
# correlated (point-biserial) against the 0/1 resolved indicator.
_DIFFICULTY_ORDINAL = {"easy": 1.0, "medium": 2.0, "hard": 3.0, "very_hard": 4.0}


def _load_task_toml_metadata(task_dir: Path) -> dict | None:
    """Read the ``[metadata]`` block from ``<task_dir>/task.toml``.

    Returns the metadata dict, or ``None`` when the file is missing or has no
    ``[metadata]`` table. Tags are produced by
    ``scripts/task_analysis/tag_task_metadata.py``.
    """
    toml_path = task_dir / "task.toml"
    if not toml_path.is_file():
        return None
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        logger.warning("Could not parse %s", toml_path, exc_info=True)
        return None
    metadata = data.get("metadata") if isinstance(data, dict) else None
    return metadata if isinstance(metadata, dict) else None


def _manual_pearson(xs: list[float], ys: list[float]) -> float | None:
    """Two-pass Pearson correlation (matches original computation)."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = 0.0
    dx2 = 0.0
    dy2 = 0.0
    for x, y in zip(xs, ys):
        dx = x - mx
        dy = y - my
        num += dx * dy
        dx2 += dx * dx
        dy2 += dy * dy
    denom = math.sqrt(dx2 * dy2)
    if denom == 0:
        return None
    return num / denom


def _rank(xs: list[float]) -> list[float]:
    """Rank array with average rank for ties."""
    indexed = sorted(enumerate(xs), key=lambda p: p[1])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _manual_spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman correlation via rank transformation + Pearson."""
    return _manual_pearson(_rank(xs), _rank(ys))


def _t_to_p(r: float | None, n: int) -> float | None:
    """Convert correlation r to two-sided p-value via t-distribution."""
    if r is None or abs(r) >= 1.0:
        return None
    df = n - 2
    t = r * math.sqrt(df / (1 - r * r))
    return float(2 * scipy_t.sf(abs(t), df))


def run_instance_analysis(
    *,
    dataset_dir: Path,
    out_dir: Path,
    instances_jsonl: Path | None = None,
    instances: list[dict] | None = None,
) -> dict:
    """Generate instance metadata contingency tables and numeric feature correlations.

    Provide either ``instances_jsonl`` or ``instances`` (not both required if
    the other is given).

    Returns summary dict with tables and correlations.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    if instances is None:
        if instances_jsonl is None:
            raise ValueError("Either instances_jsonl or instances must be provided")
        instances = []
        with open(instances_jsonl) as f:
            for line in f:
                instances.append(json.loads(line))

    # Join with metadata from each task's task.toml [metadata] block (tags
    # written by scripts/task_analysis/tag_task_metadata.py).
    rows = []
    missing = []
    for inst in instances:
        iid = inst["instance_id"]
        resolved = bool(inst.get("resolved"))
        meta = _load_task_toml_metadata(dataset_dir / iid)
        if meta is None:
            missing.append(iid)
            continue

        # task.toml uses 'difficulty' for the label; encode it ordinally so it
        # can also participate in the numeric correlation section.
        difficulty_label = meta.get("difficulty")
        difficulty_score = _DIFFICULTY_ORDINAL.get(difficulty_label)
        # Tags are [language, area, topic, bug_class].
        tags = meta.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tag1 = tags[0] if len(tags) >= 1 else None
        tag2 = tags[1] if len(tags) >= 2 else None
        tag3 = tags[2] if len(tags) >= 3 else None
        tag4 = tags[3] if len(tags) >= 4 else None

        rows.append(
            {
                "instance_id": iid,
                "resolved": resolved,
                "difficulty_label": difficulty_label,
                "difficulty_score": difficulty_score,
                "tag1": tag1,
                "tag2": tag2,
                "tag3": tag3,
                "tag4": tag4,
                # task.toml carries no per-dimension scores or metrics.
                "score_dimensions": [],
                "metrics": {},
            }
        )

    logger.info(
        f"instance_analysis: {len(rows)} rows joined, {len(missing)} instances missing metadata"
    )

    if not rows:
        logger.warning(
            "instance_analysis: no instances had task.toml [metadata]; "
            "skipping contingency/correlation output. Run "
            "scripts/task_analysis/tag_task_metadata.py on the dataset first."
        )
        summary = {
            "n_rows": 0,
            "n_resolved": 0,
            "n_unresolved": 0,
            "n_missing_metadata": len(missing),
            "instances_jsonl": str(instances_jsonl),
            "dataset_dir": str(dataset_dir),
            "tables": {},
            "correlations": {},
        }
        with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return summary

    # Build contingency tables for categorical fields
    tables_summary = {}
    for axis, field in [
        ("difficulty_label", "difficulty_label"),
        ("tag1", "tag1"),
        ("tag2", "tag2"),
        ("tag3", "tag3"),
        ("tag4", "tag4"),
    ]:
        counts = defaultdict(lambda: {"resolved": 0, "unresolved": 0})
        for row in rows:
            val = row.get(field)
            if val is None:
                continue
            if row["resolved"]:
                counts[val]["resolved"] += 1
            else:
                counts[val]["unresolved"] += 1

        # Sort categories by total descending
        categories = sorted(
            counts.keys(),
            key=lambda c: counts[c]["resolved"] + counts[c]["unresolved"],
            reverse=True,
        )

        n_resolved = sum(1 for r in rows if r["resolved"])
        n_unresolved = len(rows) - n_resolved

        # Build table dict
        table = {
            "totals": {
                "resolved": n_resolved,
                "unresolved": n_unresolved,
                "total": len(rows),
            },
            "categories": categories,
            "counts": {},
            "proportions_by_category": {},
            "proportions_by_resolved": {"resolved": {}, "unresolved": {}},
        }

        for cat in categories:
            res = counts[cat]["resolved"]
            unr = counts[cat]["unresolved"]
            tot = res + unr
            table["counts"][cat] = {"resolved": res, "unresolved": unr, "total": tot}
            table["proportions_by_category"][cat] = {
                "resolved": res / tot if tot > 0 else 0.0,
                "unresolved": unr / tot if tot > 0 else 0.0,
            }

        for cat in categories:
            res = counts[cat]["resolved"]
            unr = counts[cat]["unresolved"]
            table["proportions_by_resolved"]["resolved"][cat] = (
                res / n_resolved if n_resolved > 0 else 0.0
            )
            table["proportions_by_resolved"]["unresolved"][cat] = (
                unr / n_unresolved if n_unresolved > 0 else 0.0
            )

        _write_contingency_table(
            table,
            out_dir / f"contingency_{axis}.csv",
            out_dir / f"contingency_{axis}.txt",
            axis,
        )
        tables_summary[axis] = table

    # Compute correlations for numeric features
    correlations = {}
    numeric_features = ["difficulty_score"]

    # Collect all score_dimensions and metrics keys
    all_sd_keys = set()
    all_metrics_keys = set()
    for row in rows:
        for sd in row.get("score_dimensions", []):
            nm = sd.get("name")
            if nm:
                all_sd_keys.add(f"score_dimensions.{nm}.raw")
                all_sd_keys.add(f"score_dimensions.{nm}.score")
        for k in row.get("metrics", {}).keys():
            all_metrics_keys.add(f"metrics.{k}")

    numeric_features.extend(sorted(all_metrics_keys))
    numeric_features.extend(sorted(all_sd_keys))

    for feat in numeric_features:
        xs = []
        ys = []
        for row in rows:
            if feat == "difficulty_score":
                val = row.get("difficulty_score")
            elif feat.startswith("metrics."):
                key = feat[len("metrics.") :]
                val = row.get("metrics", {}).get(key)
            elif feat.startswith("score_dimensions."):
                parts = feat.split(".")
                if len(parts) == 3:
                    sd_name = parts[1]
                    sd_field = parts[2]
                    val = None
                    for sd in row.get("score_dimensions", []):
                        if sd.get("name") == sd_name:
                            val = sd.get(sd_field)
                            break
                else:
                    val = None
            else:
                val = None

            if val is None:
                continue
            xs.append(float(val))
            ys.append(1.0 if row["resolved"] else 0.0)

        n = len(xs)
        if n < 2:
            continue

        n_res = sum(1 for y in ys if y == 1.0)
        n_unr = n - n_res
        mean_res = (
            sum(xs[i] for i in range(n) if ys[i] == 1.0) / n_res if n_res > 0 else 0.0
        )
        mean_unr = (
            sum(xs[i] for i in range(n) if ys[i] == 0.0) / n_unr if n_unr > 0 else 0.0
        )

        r = _manual_pearson(xs, ys)
        p = _t_to_p(r, n)
        sr = _manual_spearman(xs, ys)
        sp = _t_to_p(sr, n)

        correlations[feat] = {
            "n": n,
            "n_resolved": n_res,
            "n_unresolved": n_unr,
            "mean_resolved": mean_res,
            "mean_unresolved": mean_unr,
            "pearson_r": r,
            "pearson_p": p,
            "spearman_r": sr,
            "spearman_p": sp,
        }

    # Write correlations
    _write_correlations(
        correlations, out_dir / "correlations.json", out_dir / "correlations.txt"
    )

    # Build summary
    summary = {
        "n_rows": len(rows),
        "n_resolved": sum(1 for r in rows if r["resolved"]),
        "n_unresolved": sum(1 for r in rows if not r["resolved"]),
        "n_missing_metadata": len(missing),
        "instances_jsonl": str(instances_jsonl),
        "dataset_dir": str(dataset_dir),
        "tables": tables_summary,
        "correlations": correlations,
    }

    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
        f.write("\n")

    logger.info(f"instance_analysis: outputs written to {out_dir}")
    return summary


def _write_contingency_table(
    table: dict,
    csv_path: Path,
    txt_path: Path,
    axis: str,
) -> None:
    """Write contingency table to CSV and TXT formats."""
    categories = table["categories"]
    n_res = table["totals"]["resolved"]
    n_unr = table["totals"]["unresolved"]
    n_tot = table["totals"]["total"]

    # CSV: by-category section + by-resolved section
    with open(csv_path, "w", encoding="utf-8", newline="\r\n") as f:
        # Section 1: by category
        f.write(
            "category,n_resolved,n_unresolved,n_total,pct_resolved,pct_unresolved\n"
        )
        for cat in categories:
            counts = table["counts"][cat]
            props = table["proportions_by_category"][cat]
            f.write(
                f"{cat},{counts['resolved']},{counts['unresolved']},{counts['total']},{props['resolved']:.6f},{props['unresolved']:.6f}\n"
            )
        f.write(
            f"__TOTAL__,{n_res},{n_unr},{n_tot},{n_res / n_tot:.6f},{n_unr / n_tot:.6f}\n"
        )
        f.write("\n")

        # Section 2: by resolved (counts)
        f.write(
            "resolved," + ",".join(f"{cat}_count" for cat in categories) + ",total\n"
        )
        res_counts = [str(table["counts"][cat]["resolved"]) for cat in categories]
        f.write("resolved," + ",".join(res_counts) + f",{n_res}\n")
        unr_counts = [str(table["counts"][cat]["unresolved"]) for cat in categories]
        f.write("unresolved," + ",".join(unr_counts) + f",{n_unr}\n")

        # Section 3: by resolved (pct)
        f.write(
            "resolved," + ",".join(f"{cat}_pct" for cat in categories) + ",total_pct\n"
        )
        res_pcts = [
            f"{table['proportions_by_resolved']['resolved'][cat]:.6f}"
            for cat in categories
        ]
        f.write("resolved," + ",".join(res_pcts) + ",1.000000\n")
        unr_pcts = [
            f"{table['proportions_by_resolved']['unresolved'][cat]:.6f}"
            for cat in categories
        ]
        f.write("unresolved," + ",".join(unr_pcts) + ",1.000000\n")

    # TXT: formatted tables
    with open(txt_path, "w", encoding="utf-8") as f:
        # Table 1: resolve rate within each category
        f.write(f"[1] Resolve rate within each {axis} category\n\n")

        # Determine max category width for alignment
        max_cat_len = max(len(cat) for cat in categories) if categories else 0
        if max_cat_len <= 10:
            cat_width = 11
        else:
            cat_width = max_cat_len + 2

        f.write(
            f"{'category':<{cat_width}} {'n_res':>7} {'n_unr':>7} {'n_tot':>7}  {'pct_res':>9}  {'pct_unr':>9}\n"
        )
        f.write("-" * (cat_width + 54) + "\n")
        for cat in categories:
            counts = table["counts"][cat]
            props = table["proportions_by_category"][cat]
            f.write(
                f"{cat:<{cat_width}} {counts['resolved']:>7} {counts['unresolved']:>7} {counts['total']:>7}  {props['resolved'] * 100:>8.2f}%  {props['unresolved'] * 100:>8.2f}%\n"
            )
        f.write("-" * (cat_width + 54) + "\n")
        f.write(
            f"{'TOTAL':<{cat_width}} {n_res:>7} {n_unr:>7} {n_tot:>7}  {n_res / n_tot * 100:>8.2f}%  {n_unr / n_tot * 100:>8.2f}%\n"
        )
        f.write("\n\n")

        # Table 2: category mix within resolved/unresolved
        # Match original capitalization: Difficulty_label, Tag1, Tag2
        axis_title = (
            "Difficulty_label" if axis == "difficulty_label" else axis.capitalize()
        )
        f.write(f"[2] {axis_title} mix within resolved / unresolved\n\n")

        label_width = max(len("unresolved (%)"), len("resolved (n)"))
        column_widths = [
            max(len(cat), len("100.00%"), len(str(table["counts"][cat]["total"])))
            for cat in categories
        ]
        total_width = max(len("total"), len("100.00%"), len(str(n_tot)))

        def _format_mix_row(label: str, values: list[str], total: str) -> str:
            parts = [f"{label:<{label_width}}"]
            parts.extend(
                f"{value:>{width}}" for value, width in zip(values, column_widths)
            )
            parts.append(f"{total:>{total_width}}")
            return "  ".join(parts)

        header = _format_mix_row("", categories, "total")
        sep = "-" * len(header)
        res_n = _format_mix_row(
            "resolved (n)",
            [str(table["counts"][cat]["resolved"]) for cat in categories],
            str(n_res),
        )
        unr_n = _format_mix_row(
            "unresolved (n)",
            [str(table["counts"][cat]["unresolved"]) for cat in categories],
            str(n_unr),
        )
        res_pct = _format_mix_row(
            "resolved (%)",
            [
                f"{table['proportions_by_resolved']['resolved'][cat] * 100:.2f}%"
                for cat in categories
            ],
            "100.00%",
        )
        unr_pct = _format_mix_row(
            "unresolved (%)",
            [
                f"{table['proportions_by_resolved']['unresolved'][cat] * 100:.2f}%"
                for cat in categories
            ],
            "100.00%",
        )

        f.write(header + "\n")
        f.write(sep + "\n")
        f.write(res_n + "\n")
        f.write(unr_n + "\n\n")
        f.write(res_pct + "\n")
        f.write(unr_pct + "\n")


def _write_correlations(
    correlations: dict,
    json_path: Path,
    txt_path: Path,
) -> None:
    """Write correlations to JSON and TXT formats."""
    # JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(correlations, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # TXT
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("Correlation of numeric metadata features with resolved (0/1).\n")
        f.write(
            "Pearson r against the 0/1 indicator is the point-biserial correlation.\n"
        )
        f.write("p-values are two-sided t-approximations.\n\n")

        # Header
        f.write(
            f"{'feature':<48} {'n':>4}  {'mean_res':>10}  {'mean_unr':>10} {'pearson_r':>11}{'p(pearson)':>12}{'spearman_r':>13}{'p(spearman)':>12}\n"
        )
        f.write("-" * 125 + "\n")

        # Sort features
        features = sorted(
            correlations.keys(),
            key=lambda k: (
                0 if k == "difficulty_score" else 1 if k.startswith("metrics.") else 2
            ),
        )

        for feat in features:
            vals = correlations[feat]
            n = vals["n"]
            mr = vals["mean_resolved"]
            mu = vals["mean_unresolved"]
            pr = vals["pearson_r"]
            pp = vals["pearson_p"]
            sr = vals["spearman_r"]
            sp = vals["spearman_p"]

            if pr is None:
                pr_str = "NA"
                pp_str = "NA"
                sr_str = "NA"
                sp_str = "NA"
            else:
                pr_str = f"{pr:>10.4f}"
                pp_str = (
                    f"{pp:>11.4f}"
                    if pp is not None and pp >= 0.0001
                    else f"{pp:>11.4e}"
                    if pp is not None
                    else "NA"
                )
                sr_str = f"{sr:>12.4f}" if sr is not None else "NA"
                sp_str = (
                    f"{sp:>11.4f}"
                    if sp is not None and sp >= 0.0001
                    else f"{sp:>11.4e}"
                    if sp is not None
                    else "NA"
                )

            f.write(
                f"{feat:<48} {n:>4}  {mr:>10.4f}  {mu:>10.4f} {pr_str:>11}{pp_str:>12}{sr_str:>13}{sp_str:>12}\n"
            )
