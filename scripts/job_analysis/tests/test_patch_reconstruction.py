from src.parser.patch_parser import compute_patch_overlap, parse_patch
from src.parser.trajectory_parser import (
    _FileStateTracker,
    _normalize_patch_path,
    _parse_numbered_file_lines,
)


def test_normalize_patch_path_strips_known_roots():
    assert _normalize_patch_path("/app/pkg/mod.py") == "pkg/mod.py"
    assert _normalize_patch_path("/testbed/pkg/mod.py") == "pkg/mod.py"
    assert _normalize_patch_path("/workspace/pkg/mod.py") == "pkg/mod.py"
    assert _normalize_patch_path("/other/pkg/mod.py") == "other/pkg/mod.py"


def test_parse_numbered_file_lines_preserves_content():
    observation = """Here's the result of running `cat -n` on /testbed/a.py:
     1	def foo():
     2	    return 1
     3
     4	class Bar:
"""
    assert _parse_numbered_file_lines(observation) == [
        "def foo():",
        "    return 1",
        "",
        "class Bar:",
    ]


def test_full_state_str_replace_uses_real_line_numbers_and_context():
    tracker = _FileStateTracker()
    lines = [f"line {i}" for i in range(1, 50)]
    lines[20] = "def target():"
    lines[21] = "    return 1"
    observation = "\n".join(f"{i + 1:6d}\t{line}" for i, line in enumerate(lines))
    tracker.record_view("/testbed/pkg/mod.py", observation)

    assert tracker.apply_str_replace(
        "/testbed/pkg/mod.py",
        "    return 1",
        "    return 2",
        "The file /testbed/pkg/mod.py has been edited.",
    )

    patch = tracker.generate_patch()
    assert "diff --git a/pkg/mod.py b/pkg/mod.py" in patch
    assert "@@ -19,7 +19,7 @@ def target" in patch
    assert "-    return 1" in patch
    assert "+    return 2" in patch


def test_full_state_insert_and_undo():
    tracker = _FileStateTracker()
    observation = "     1	alpha\n     2	gamma"
    tracker.record_view("/testbed/a.txt", observation)

    assert tracker.apply_insert("/testbed/a.txt", 1, "beta", "edited")
    assert "beta" in tracker.generate_patch()

    assert tracker.undo("/testbed/a.txt")
    assert tracker.generate_patch() == ""


def test_duplicate_file_sections_count_for_hunk_overlap():
    gold = parse_patch(
        """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -10,3 +10,3 @@ def one():
-a
+b
@@ -30,3 +30,3 @@ def two():
-c
+d
"""
    )
    model = parse_patch(
        """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,3 +1,3 @@ def nope():
-x
+y

diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -30,3 +30,3 @@ def two():
-c
+d
"""
    )
    assert compute_patch_overlap(gold, model)["hunk_overlap"] == 0.5


def test_snippet_fallback_can_exclude_full_state_paths():
    edits = [
        {
            "command": "str_replace",
            "path": "/testbed/a.py",
            "old_str": "old",
            "new_str": "new",
        },
        {
            "command": "str_replace",
            "path": "/testbed/b.py",
            "old_str": "old",
            "new_str": "new",
        },
    ]
    from src.parser.trajectory_parser import _reconstruct_patch_from_edits

    patch = _reconstruct_patch_from_edits(edits, exclude_paths={"a.py"})
    assert "diff --git a/a.py b/a.py" not in patch
    assert "diff --git a/b.py b/b.py" in patch


def test_extract_funcs_from_diff_body_lines():
    patch = parse_patch(
        """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,4 @@
+def added_func():
+    return 1
+
 class Existing:
"""
    )
    assert "added_func" in patch.func_names
    assert "Existing" in patch.func_names
