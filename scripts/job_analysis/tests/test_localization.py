from src.features.localization import _func_name_in_text, _paths_match
from src.parser.patch_parser import PatchInfo
from src.parser.trajectory_parser import Trajectory, TrajectoryStep
from src.features.localization import extract_localization_features


def test_paths_match_requires_suffix_not_substring():
    assert _paths_match("pkg/utils.py", "utils.py")
    assert not _paths_match("not_utils.py", "utils.py")


def test_func_name_requires_word_boundary():
    assert not _func_name_in_text("set", "the dataset is ready")
    assert _func_name_in_text("set", "call set() now")


def test_extract_localization_features_without_gold_paths():
    trajectory = Trajectory(
        instance_id="x",
        steps=[
            TrajectoryStep(
                step_id=0,
                timestamp="",
                action_type="file_view",
                action_detail="/testbed/pkg/mod.py",
                observation="def target(): pass",
                thought="",
                reasoning_content="",
                is_error=False,
                raw_kind="",
            )
        ],
    )
    gold = PatchInfo(file_paths={"pkg/mod.py"}, func_names={"target"})
    model = PatchInfo()
    features = extract_localization_features(trajectory, gold, model)
    assert features.C1_file_read is True
    assert features.C2_func_read is True
