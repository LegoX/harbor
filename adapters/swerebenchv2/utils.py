import re
import shlex
from pathlib import Path
from textwrap import dedent


def read_text(path: Path) -> str:
    """Read text from a file path, raising FileNotFoundError if it doesn't exist."""
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text()


def render_literal(template_text: str, **repls: str) -> str:
    """Replace only exact placeholders like {key} with provided values."""
    out = template_text
    for key, value in repls.items():
        out = out.replace("{" + key + "}", value)
    return out


def get_image_name(record: dict) -> str:
    """Return the pre-built Docker image name for a SWE-rebench-V2 record."""
    if record.get("image_name"):
        return record["image_name"]

    install_config = record.get("install_config") or {}
    if install_config.get("image_name"):
        return install_config["image_name"]

    raise ValueError(
        f"No image_name found for instance {record.get('instance_id', '?')}"
    )


def get_workdir(record: dict) -> str:
    """Return the repository working directory inside the task container."""
    repo = record.get("repo", "")
    if "/" in repo:
        return f"/{repo.split('/')[1]}"
    return "/testbed"


def get_difficulty(record: dict) -> str:
    """Extract the normalized task difficulty from dataset metadata."""
    metadata = record.get("meta")
    if not isinstance(metadata, dict):
        return "unknown"

    llm_metadata = metadata.get("llm_metadata")
    if isinstance(llm_metadata, list) and llm_metadata:
        llm_metadata = llm_metadata[0]
    if not isinstance(llm_metadata, dict):
        return "unknown"

    difficulty = llm_metadata.get("difficulty", "")
    return difficulty if difficulty in {"easy", "medium", "hard"} else "unknown"


def _extract_patch_paths(test_patch: str) -> tuple[list[str], list[str]]:
    """Return tracked pre-image paths and all paths touched by a unified diff."""
    preimage_paths = [
        path
        for path in re.findall(r"^--- a/(.*)$", test_patch, re.MULTILINE)
        if path != "/dev/null"
    ]
    postimage_paths = [
        path
        for path in re.findall(r"^\+\+\+ b/(.*)$", test_patch, re.MULTILINE)
        if path != "/dev/null"
    ]
    return (
        list(dict.fromkeys(preimage_paths)),
        list(dict.fromkeys(preimage_paths + postimage_paths)),
    )


def get_test_commands(record: dict) -> str:
    """Build the shell fragment that applies the test patch and runs tests."""
    test_patch = record.get("test_patch") or ""
    base_commit = record["base_commit"]
    install_config = record.get("install_config") or {}
    test_cmds = install_config.get("test_cmd", [])
    if isinstance(test_cmds, str):
        test_cmds = [test_cmds]
    if not test_cmds:
        raise ValueError(
            f"No test command found for instance {record.get('instance_id', '?')}"
        )

    workdir = get_workdir(record)
    tracked_paths, all_paths = _extract_patch_paths(test_patch)
    quoted_tracked_paths = " ".join(shlex.quote(path) for path in tracked_paths)
    quoted_all_paths = " ".join(shlex.quote(path) for path in all_paths)
    restore_tracked_files = (
        f"git checkout {shlex.quote(base_commit)} -- {quoted_tracked_paths}"
        if quoted_tracked_paths
        else ":"
    )
    remove_untracked_files = (
        dedent(
            f"""
            for path in {quoted_all_paths}; do
                if [ -e "$path" ] && ! git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
                    rm -rf -- "$path"
                fi
            done
            """
        ).strip()
        if quoted_all_paths
        else ":"
    )
    restore_steps = f"{restore_tracked_files}\n{remove_untracked_files}"
    indented_restore_steps = "\n".join(
        f"    {line}" for line in restore_steps.splitlines()
    )
    rust_preflight = ""
    if str(record.get("language", "")).lower() == "rust":
        rust_preflight = """
# Some upstream Rust images omit rustfmt from the stable toolchain even though
# workspace/xtask tests invoke it. Install it only when the component is absent.
if command -v rustup >/dev/null 2>&1; then
    if ! rustfmt --version >/dev/null 2>&1; then
        rustup component add rustfmt
    fi
    if ! rustup run stable rustfmt --version >/dev/null 2>&1; then
        rustup component add rustfmt --toolchain stable
    fi
    active_sysroot=$(rustc --print sysroot)
    if [ ! -d "$active_sysroot/lib/rustlib/src/rust/library" ]; then
        rustup component add rust-src
    fi
fi
export CI=1
"""
    indented_test_commands = "\n".join(f"    {command}" for command in test_cmds)
    return f"""#!/bin/bash
set -euo pipefail

cd {shlex.quote(workdir)}

# Restore files owned by the benchmark test patch before applying it. Oracle
# patches include the PR's test changes, so applying test_patch directly on top
# would duplicate those changes; this also prevents candidate-written tests
# from influencing verification.
restore_test_files() {{
{indented_restore_steps}
}}
restore_test_files

# Apply the canonical benchmark test patch. A failed three-way application can
# leave some paths changed, so restore the complete test-owned state before
# each fallback.
echo {shlex.quote(test_patch)} > /tmp/test_patch.diff
if git apply -v --3way --recount --ignore-space-change --whitespace=nowarn /tmp/test_patch.diff; then
    :
else
    restore_test_files
    if git apply /tmp/test_patch.diff; then
        :
    else
        restore_test_files
        patch --batch --forward --fuzz=5 -p1 -i /tmp/test_patch.diff
    fi
fi
export TEST_PATCH_APPLIED=1
{rust_preflight}

# Record terminal output while preserving the real test-command exit status.
LOG_FILE=$(mktemp)
export LOG_FILE
set +e
(
    set -e
{indented_test_commands}
) 2>&1 | tee "$LOG_FILE"
TEST_EXIT_CODE=${{PIPESTATUS[0]}}
set -e
export TEST_EXIT_CODE
"""
