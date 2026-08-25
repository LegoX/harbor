from src.aggregator.report import aggregate


def _instance(primary: str) -> dict:
    return {
        "primary_failure": primary,
        "axes": {
            "localization": "miss",
            "diagnosis": "wrong",
            "implementation": "wrong_logic",
            "tool_usage": "ok",
            "long_horizon": "ok",
        },
        "secondary_failures": [],
        "flags": [],
        "correctness_verdict": "V4",
        "deterministic_features": {},
    }


def test_top_primary_uses_most_common_category():
    results = [
        _instance("implementation"),
        _instance("implementation"),
        _instance("localization"),
    ]
    report = aggregate(results)
    assert report.summary["top_primary"] == "implementation"
