# OpenSWE -> Harbor Adapter

## Overview

This adapter converts the [GAIR/OpenSWE](https://huggingface.co/datasets/GAIR/OpenSWE)
dataset into **Harbor-compatible tasks**. OpenSWE
([daVinci-Env](https://arxiv.org/abs/2603.13023)) is a large-scale, fully
transparent SWE environment-synthesis dataset comprising tens of thousands of
executable Docker environments built from real GitHub repositories, with all
Dockerfiles and evaluation scripts open-sourced.

- **Benchmark type:** Software engineering / bug-fixing (SWE-bench-like)
- **Language:** Python
- **Source:** <https://github.com/GAIR-NLP/OpenSWE>
- **Licensing:** mixed permissive (`openswe_oss`); AGPL-3.0 for the OpenSWE project
- **Access:** the dataset is **gated**; you must accept the terms on HuggingFace
  and have a cached token (`huggingface-cli login`).

## Dataset configs

OpenSWE ships two dataset configs, converted into **separate output folders**:

| Config | Output folder | Gold patch? | Oracle verifiable? |
|--------|---------------|-------------|--------------------|
| `openswe_oss` | `datasets/openswe_oss/` | yes (`patch` + `test_patch`) | yes |
| `openswe_other` | `datasets/openswe_other/` | no | no (eval-only / best-effort) |

A `filtered_ids.csv` file lists the difficulty-filtered subset (the
"quality-guaranteed" instances). Use `--filtered` to restrict to it; the output
goes to a `*_filtered` folder.

## How the adaptation works

Each OpenSWE instance carries its own `Dockerfile` and `eval_script`. The
adapter translates one instance into a Harbor task as follows.

### Environment (`environment/`)

- **`Dockerfile`** – the instance Dockerfile, made self-contained. OpenSWE
  Dockerfiles inherit from prebuilt `openswe-python-X.Y` base images that are
  **not published to any registry**. The adapter rewrites the `FROM` line to an
  inlined equivalent (matching OpenSWE's `scripts/prepare_baseimg.py`):

  ```dockerfile
  FROM continuumio/miniconda3:25.3.1-1
  RUN conda create -n testbed python=X.Y -y \
      && echo "conda activate testbed" >> ~/.bashrc
  ```

  Everything else (notably `COPY repo /testbed` and the project install steps)
  is preserved verbatim.
- **`repo/`** – the build context the Dockerfile copies. The adapter clones the
  source repository at `base_commit` (keeping `.git`, which the verifier needs).

### Instruction (`instruction.md`)

The instance `problem_statement`, wrapped with a short preamble telling the
agent to edit `/testbed` in place and not to modify the test suite.

### Grading (`tests/`)

OpenSWE grading is **rule-based**: each `eval_script` emits an
`OPENSWE_EXIT_CODE=<rc>` marker after running the relevant tests
(`FAIL_TO_PASS`/`PASS_TO_PASS` are empty). The released `eval_script`s are
construction-time *validation* scripts, so most of them **apply the gold fix
patch (and the test patch) inline** before running tests — using them verbatim
would make every task pass regardless of the agent.

The adapter therefore splits grading into:

- **`tests/eval_body.sh`** – the `eval_script` with the inline `git apply ...
  <<'EOF'` heredoc blocks stripped, leaving conda activation, dependency
  installation, the test invocation, and the `OPENSWE_EXIT_CODE` marker.
  - `openswe_oss`: **all** patch heredocs are stripped (the test patch is
    re-applied separately, see below).
  - `openswe_other`: only heredocs whose surrounding comments mark them as the
    gold/fix patch are stripped; test-patch heredocs are kept (best-effort,
    since there is no separate `test_patch` field).
- **`tests/test_patch.diff`** – the released `test_patch` (`openswe_oss` only).
- **`tests/test.sh`** – the Harbor verifier: applies `test_patch.diff` on top of
  the agent's edits, runs `eval_body.sh`, parses `OPENSWE_EXIT_CODE`, and writes
  the reward to `/logs/verifier/reward.txt` (`1` if `0`, else `0`).

This design is intended to make the oracle (gold patch) pass and a no-op agent
fail. Full-dataset oracle/no-op parity has not yet been established; see
`parity_experiment.json` before treating that behavior as validated evidence.

### Solution (`solution/solve.sh`)

For `openswe_oss`, applies the gold `patch`. Not generated for `openswe_other`
(no gold patch is released).

## Generated task structure

```
datasets/openswe_oss/
└── {instance_id}/
    ├── task.toml
    ├── instruction.md
    ├── environment/
    │   ├── Dockerfile
    │   └── repo/            # source cloned at base_commit
    ├── solution/
    │   └── solve.sh         # openswe_oss only
    └── tests/
        ├── test.sh
        ├── eval_body.sh
        ├── test_patch.diff
        └── config.json
```

## Usage: create task directories

```bash
cd adapters/openswe

# Convert a couple of openswe_oss instances (quick smoke test)
uv run run_adapter.py --config openswe_oss --limit 2

# Convert a specific instance
uv run run_adapter.py --config openswe_oss --instance-id Zac-HD__shed-91

# Convert the difficulty-filtered subset of openswe_oss
uv run run_adapter.py --config openswe_oss --filtered

# Convert openswe_other (eval-only)
uv run run_adapter.py --config openswe_other --limit 5

# Custom output directory
uv run run_adapter.py --config openswe_oss --limit 2 --output-dir /path/to/out
```

By default tasks are written under `datasets/<config>[_filtered]/`
(relative to the repo root). Inspect the generated files without cloning repos
using `--no-clone`.

## Run with Harbor

```bash
# Single task with the oracle agent (openswe_oss)
uv run harbor trial start -p datasets/openswe_oss/<instance_id> -a oracle

# Whole local dataset with a custom agent
uv run harbor run -p datasets/openswe_oss -a <agent> -m "<model>"

# Using the reference job config
uv run harbor run -c adapters/openswe/openswe.yaml -a <agent> -m "<model>"
```

## Known issues and caveats

- **Gated dataset:** requires accepting the HuggingFace terms and a cached
  token.
- **Reconstructed base images:** `openswe-python-*` images are not public; they
  are inlined as `continuumio/miniconda3` + a `testbed` conda env. Instances
  whose Dockerfile does not inherit from an `openswe-python-*` base are skipped
  (the `testbed` env cannot be guaranteed).
- **Missing build-context files:** a small minority of Dockerfiles reference
  files such as `deps/test.txt` that are not part of the release; those image
  builds fail and are reported as failures by the adapter.
- **`openswe_other` is eval-only:** no gold patch is released, so oracle
  solutions cannot be verified and the test-patch stripping is heuristic.
- **Parity:** not yet run — see [`parity_experiment.json`](./parity_experiment.json).

## Citation

```bibtex
@misc{fu2026davincienvopensweenvironment,
      title={daVinci-Env: Open SWE Environment Synthesis at Scale},
      author={Dayuan Fu and Shenyu Wu and Yunze Wu and Zerui Peng and Yaxing Huang and Jie Sun and Ji Zeng and Mohan Jiang and Lin Zhang and Yukun Li and Jiarui Hu and Liming Liu and Jinlong Hou and Pengfei Liu},
      year={2026},
      eprint={2603.13023},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2603.13023}
}
```
