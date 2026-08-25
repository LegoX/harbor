from src.features.localization import LocalizationFeatures
from src.features.pathology import PathologyFlags
from src.hack_detector.reward_hack import HackResult
from src.judge.tier1_judge import Tier1Judgment
from src.labeler.axis_labeler import label_instance


def _loc_features(**overrides) -> LocalizationFeatures:
    defaults = dict(
        C1_file_read=False,
        C2_func_read=False,
        C3_file_alignment=False,
        C4_func_alignment=False,
        C5_test_executed=False,
        diff_hunk_overlap=0.0,
        diff_line_delta=0,
        diff_file_delta=0,
        modified_test_file=False,
        trajectory_length=10,
        max_iterations=100,
        file_overlap_count=0,
        func_overlap_count=0,
    )
    defaults.update(overrides)
    return LocalizationFeatures(**defaults)


def _pathology() -> PathologyFlags:
    return PathologyFlags(
        loop_detected=False,
        loop_details="",
        premature_stop=False,
        premature_stop_remaining_frac=0.0,
        tool_error_storm=False,
        tool_error_storm_count=0,
        context_truncation=False,
    )


def _judgment(**overrides) -> Tier1Judgment:
    defaults = dict(
        localization_quality="miss",
        behavioral_understanding="wrong",
        implementation_quality="wrong_location",
        tool_usage_health="ok",
        failure_attribution="localization",
    )
    defaults.update(overrides)
    return Tier1Judgment(**defaults)


def test_missing_gold_flag_and_primary():
    labels = label_instance(
        _loc_features(),
        _pathology(),
        HackResult("not_hack", [], [], 1.0),
        _judgment(),
        missing_gold=True,
    )
    assert "missing_gold" in labels.flags
    assert labels.primary_failure == "error"


def test_environment_noise_when_error_with_patch():
    labels = label_instance(
        _loc_features(C1_file_read=True),
        _pathology(),
        HackResult("not_hack", [], [], 1.0),
        _judgment(localization_quality="partial"),
        has_trajectory_error=True,
        is_error_instance=False,
        is_empty_patch=False,
    )
    assert "environment_noise" in labels.flags
    assert labels.primary_failure == "environment"


def test_alternative_fix_for_resolved_low_overlap():
    labels = label_instance(
        _loc_features(diff_hunk_overlap=0.2, C3_file_alignment=True),
        _pathology(),
        HackResult("not_hack", [], [], 1.0),
        _judgment(implementation_quality="correct"),
        is_resolved=True,
    )
    assert "alternative_fix" in labels.flags
    assert labels.correctness_verdict == "V2"


def test_hack_takes_priority_over_localization():
    labels = label_instance(
        _loc_features(C1_file_read=False),
        _pathology(),
        HackResult("confirmed_hack", ["H1"], ["evidence"], 0.9),
        _judgment(),
    )
    assert labels.primary_failure == "hack"
