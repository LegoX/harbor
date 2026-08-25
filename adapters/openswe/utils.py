"""Helper utilities for converting OpenSWE instances into Harbor tasks."""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from pathlib import Path

# Matches `FROM openswe-python-3.11`, `FROM openswe-python-2.7`, etc.
_OPENSWE_BASE_RE = re.compile(
    r"^\s*FROM\s+openswe-python-(?P<ver>[0-9]+(?:\.[0-9]+)?)\s*$",
    re.MULTILINE,
)

# Matches the opening line of a (possibly indented) heredoc, capturing the
# delimiter. The closing line is the delimiter on a line by itself.
_HEREDOC_START_RE = re.compile(r"<<-?\s*[\"']?(?P<delim>[A-Za-z0-9_]+)[\"']?")

# Matches a heredoc opening line that writes a patch/diff file, e.g.
# `cat > /tmp/x.patch <<'EOF'` or `tee fix.diff <<EOF`.
_WRITES_PATCH_FILE_RE = re.compile(r"\.(patch|diff)\b")


def read_text(path: Path) -> str:
    """Read text from a file path, raising FileNotFoundError if it doesn't exist."""
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text()


def render_literal(template_text: str, **repls: str) -> str:
    """Replace only exact placeholders like {key} with provided values."""
    out = template_text
    for k, v in repls.items():
        out = out.replace("{" + k + "}", v)
    return out


def load_filtered_ids(local_csv: Path | None = None) -> set[str]:
    """Return the set of difficulty-filtered instance_ids from filtered_ids.csv.

    If ``local_csv`` is provided it is read directly; otherwise the file is
    downloaded from the gated HuggingFace dataset (requires a cached token).
    """
    if local_csv is None:
        from huggingface_hub import hf_hub_download

        local_csv = Path(
            hf_hub_download(
                repo_id="GAIR/OpenSWE",
                filename="filtered_ids.csv",
                repo_type="dataset",
            )
        )

    ids: set[str] = set()
    with Path(local_csv).open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            value = (row.get("instance_id") or "").strip()
            if value:
                ids.add(value)
    return ids


def rewrite_dockerfile(dockerfile: str) -> str:
    """Make an OpenSWE Dockerfile self-contained.

    OpenSWE Dockerfiles inherit from prebuilt ``openswe-python-X.Y`` base
    images that are not published to any registry. We inline the equivalent
    base (continuumio/miniconda3 + a ``testbed`` conda env), matching OpenSWE's
    ``scripts/prepare_baseimg.py``. Everything else (notably ``COPY repo
    /testbed`` and the project install steps) is preserved verbatim.
    """

    def _replace(match: re.Match[str]) -> str:
        ver = match.group("ver")
        return (
            "FROM continuumio/miniconda3:25.3.1-1\n"
            f"RUN conda create -n testbed python={ver} -y \\\n"
            '    && echo "conda activate testbed" >> ~/.bashrc'
        )

    return _OPENSWE_BASE_RE.sub(_replace, dockerfile)


def uses_openswe_base(dockerfile: str) -> bool:
    """True if the Dockerfile inherits from an ``openswe-python-*`` base image."""
    return bool(_OPENSWE_BASE_RE.search(dockerfile))


def build_eval_body(eval_script: str, *, strip_all_patches: bool) -> str:
    """Transform an OpenSWE eval_script into a Harbor-friendly evaluation body.

    OpenSWE's released eval_scripts are construction-time validation scripts:
    they frequently apply the gold fix patch (and the test patch) inline via
    ``git apply ... <<'EOF'`` heredocs before running the tests. Using them
    verbatim would make a task pass regardless of the agent.

    - ``strip_all_patches=True`` (openswe_oss): remove every ``git apply``
      heredoc block. The caller re-applies only the separate ``test_patch``
      before invoking this body, so grading reflects the agent's edits.
    - ``strip_all_patches=False`` (openswe_other): there is no separate
      ``test_patch`` field, so keep the test-patch heredocs and remove only the
      ones whose surrounding comments mark them as the gold/fix patch.

    The conda activation, dependency installation, the test invocation, and the
    ``OPENSWE_EXIT_CODE=...`` marker are always preserved.
    """
    lines = eval_script.splitlines()
    out: list[str] = []
    recent_comments: list[str] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("#"):
            recent_comments.append(stripped.lower())
            recent_comments = recent_comments[-3:]
            out.append(line)
            i += 1
            continue

        heredoc = _HEREDOC_START_RE.search(line)
        is_git_apply = "git apply" in line
        # A heredoc that writes a *.patch/*.diff file, e.g. `cat > x.patch <<EOF`.
        writes_patch_file = (
            heredoc is not None
            and ">" in line
            and bool(_WRITES_PATCH_FILE_RE.search(line))
        )
        is_patch_heredoc = heredoc is not None and (is_git_apply or writes_patch_file)

        if is_patch_heredoc:
            label = " ".join(recent_comments) + " " + stripped.lower()
            is_gold = ("gold" in label) or ("fix patch" in label)
            is_test = (
                ("test patch" in label)
                or ("test-only" in label)
                or ("test only" in label)
            )

            if strip_all_patches:
                # oss: the authoritative test_patch is re-applied by the
                # verifier, so remove every inline patch (gold and test).
                should_strip = True
            else:
                # other: keep test patches, drop only the gold/fix patch.
                should_strip = is_git_apply and is_gold and not is_test

            assert heredoc is not None
            delim = heredoc.group("delim")
            # Consume the heredoc block (up to and including the closing delim).
            j = i + 1
            while j < n and lines[j].strip() != delim:
                j += 1
            if j < n:
                j += 1  # include the closing delimiter line

            if not should_strip:
                out.extend(lines[i:j])
            i = j
            recent_comments = []
            continue

        if stripped:
            recent_comments = []
        out.append(line)
        i += 1

    body = "\n".join(out)
    if not body.startswith("#!"):
        body = "#!/bin/bash\n" + body
    if not body.endswith("\n"):
        body += "\n"
    return body


def clone_repo_at_commit(
    repo: str,
    base_commit: str,
    dest: Path,
    *,
    github_base: str = "https://github.com",
) -> None:
    """Materialize ``<repo>`` at ``base_commit`` into ``dest`` (keeping .git).

    The OpenSWE Dockerfiles ``COPY repo /testbed``, so the build context needs
    a ``repo/`` directory. The verifier and solution scripts rely on ``git`` in
    the checkout, so the ``.git`` directory is preserved.

    To avoid downloading entire repository histories (which dominates runtime
    and disk usage), three strategies are tried in order of increasing cost:

    1. shallow fetch of just the target commit (``fetch --depth 1 origin <sha>``);
    2. a treeless partial clone (``clone --filter=tree:0``) then checkout, for
       commits the host won't serve as a shallow SHA but are still reachable;
    3. a full clone then checkout, as a last resort.
    """
    dest = Path(dest)
    url = f"{github_base}/{repo}.git"

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True)

    def _reset_dest() -> None:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

    # Strategy 1: shallow-fetch only the target commit (no history, no branches).
    _reset_dest()
    _run(["git", "init", "--quiet", str(dest)])
    _run(["git", "-C", str(dest), "remote", "add", "origin", url])
    fetch = _run(
        [
            "git",
            "-C",
            str(dest),
            "fetch",
            "--quiet",
            "--depth",
            "1",
            "origin",
            base_commit,
        ]
    )
    if fetch.returncode == 0:
        checkout = _run(["git", "-C", str(dest), "checkout", "--quiet", "FETCH_HEAD"])
        if checkout.returncode == 0:
            return

    # Strategy 2: treeless partial clone (fetches the commit graph but defers
    # trees/blobs), then materialize just the target commit on checkout.
    if dest.exists():
        shutil.rmtree(dest)
    partial = _run(
        ["git", "clone", "--quiet", "--filter=tree:0", "--no-checkout", url, str(dest)]
    )
    if partial.returncode == 0:
        checkout = _run(["git", "-C", str(dest), "checkout", "--quiet", base_commit])
        if checkout.returncode == 0:
            return

    # Strategy 3: full clone then checkout.
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    clone = _run(["git", "clone", "--quiet", url, str(dest)])
    if clone.returncode != 0:
        raise RuntimeError(f"git clone failed for {url}: {clone.stderr.strip()}")

    checkout = _run(["git", "-C", str(dest), "checkout", "--quiet", base_commit])
    if checkout.returncode != 0:
        fetch = _run(
            ["git", "-C", str(dest), "fetch", "--quiet", "origin", base_commit]
        )
        if fetch.returncode == 0:
            checkout = _run(
                ["git", "-C", str(dest), "checkout", "--quiet", "FETCH_HEAD"]
            )
        if checkout.returncode != 0:
            raise RuntimeError(
                f"git checkout {base_commit} failed for {repo}: "
                f"{checkout.stderr.strip()}"
            )
