# SWE-rebench-V2 → Harbor Adapter

Adapter for converting [SWE-rebench-V2](https://huggingface.co/datasets/nebius/SWE-rebench-V2) tasks into the [Harbor](https://harborframework.com/) evaluation format.

## Overview

SWE-rebench-V2 is a curated dataset of 32,079 software-engineering tasks derived from real GitHub issues and pull requests, spanning 20+ programming languages: Python, Go, TypeScript, JavaScript, Rust, Java, PHP, Kotlin, Julia, Elixir, Scala, Swift, Dart, C, C++, C#, R, Clojure, OCaml, Lua, and more.

### Key differences from SWE-rebench (V1)

| Feature | SWE-rebench (V1) | SWE-rebench-V2 |
|---------|------------------|-----------------|
| Languages | Python only | 20+ languages |
| Dataset size | 21k (test) / 6.5k (filtered) | 32,079 |
| HF dataset | `nebius/SWE-rebench` | `nebius/SWE-rebench-V2` |
| Environment | Conda + swebench images | Pre-built Docker images per-instance |
| Log parsers | swebench fork | SWE-rebench-V2 repo (80+ parsers) |
| Grading | swebench harness | Standalone log parsing + test comparison |

## Features

- Implements conversion for the 32,079-record SWE-rebench-V2 dataset; complete
  conversion and runtime validation depend on source/image availability
- Supports per-instance Docker images and test commands via `install_config`
- Multi-language grading using V2 log parsers (pytest, gotest, cargo, jest, maven, etc.)
- Filter by programming language via `--language`
- Exclude one or more programming languages via `--exclude-lang`
- Oracle solution scripts included for each task
- Fixed 200-task teacher-selection subset for comparing different teacher agents/models

## Quick Start

### 1. Generate task directories

```bash
# Attempt conversion of the full dataset
uv run run_adapter.py --output-dir /path/to/harbor-datasets/datasets/swerebenchv2
uv run run_adapter.py --output-dir /absolute/path/to/harbor/datasets/swerebenchv2

# Convert a single task
uv run run_adapter.py --instance-id "unidata__netcdf-c-1925" --output-dir ./test_output

# Convert first 100 tasks
uv run run_adapter.py --limit 100

# Convert only Python tasks
uv run run_adapter.py --language python --output-dir ./datasets/swerebenchv2
uv run run_adapter.py --language python --output-dir ../../datasets/swerebenchv2-python

# Exclude Python tasks
uv run run_adapter.py --exclude-lang python --workers 8 --output-dir ../../datasets/swerebenchv2-no-python

# Keep Python tasks but exclude repos labeled as JavaScript language records
uv run run_adapter.py --language python --exclude-lang javascript --output-dir ./datasets/swerebenchv2-python-only
```

Generated tasks default to 2 CPUs, 8 GiB memory, and 20 GiB storage. Override
these limits with `--cpus`, `--memory-mb`, and `--storage-mb`. The verifier uses
public networking and forces Java dependency resolution over IPv4 to match the
upstream SWE-rebench-V2 evaluator more closely.

### 1a. Generate the fixed 200-task teacher-selection subset

This adapter also includes a pinned 200-task subset used for teacher selection comparisons.
The task ids are stored in `swerebenchv2_teacher_selection_200.txt` and correspond to the
local `datasets/swerebenchv2-200-260429` subset.

The `260429` refresh keeps the original deterministic ID ordering, except for two
oracle-failing tasks (`0xpolygonhermez__zkevm-node-3782`, `ably__ably-go-478`)
which are replaced by the next unused sorted IDs
(`0xs34n__starknet.js-495`, `0xs34n__starknet.js-520`).

```bash
# From the repository root
uv run adapters/swerebenchv2/generate_teacher_selection_200.py \
  --output-dir datasets/swerebenchv2-200-260429 \
  --overwrite
```

### 2. Run with Harbor

```bash
# Run oracle agent on a single task
uv run harbor trials start -p datasets/swerebenchv2/<task-id>

# Run on entire dataset with config
uv run harbor jobs start -c adapters/swerebenchv2/swerebenchv2.yaml -a <agent-name> -m <model-name>

# Run from local dataset path
uv run harbor jobs start -p datasets/swerebenchv2 -a <agent-name> -m <model-name>
```

## Task Directory Structure

Each converted task follows the Harbor format:

```
<task-id>/
  task.toml              # Task configuration (difficulty, timeouts)
  instruction.md         # Problem statement from the GitHub issue
  environment/
    Dockerfile           # Pre-built V2 Docker image + log parsers
  tests/
    test.sh              # Test patch application + grading script
    config.json          # Full task record (install_config, FAIL_TO_PASS, etc.)
  solution/
    solve.sh             # Oracle solution (applies the gold patch)
```

## Architecture

### Docker Environment

Each task uses a pre-built Docker image from the SWE-rebench-V2 project that already includes:
- The repository cloned at the correct base commit
- All dependencies installed per `install_config`
- The appropriate language toolchain

The Harbor Dockerfile layer adds:
- Python3 (for grading, if not already present)
- The SWE-rebench-V2 evaluation code (for log parsers)
- Standard utilities (patch, curl)

### Grading

The test script:
1. Applies the test patch from `config.json`
2. Runs the test command(s) from `install_config.test_cmd`
3. Parses test output using the log parser specified in `install_config.log_parser`
4. Checks that all expected `FAIL_TO_PASS` tests pass and no expected `PASS_TO_PASS` tests regress
5. Writes reward (1 for resolved, 0 otherwise) to `/logs/verifier/reward.txt`

## CLI Reference

```
usage: run_adapter.py [-h] [--instance-id ID] [--all | --no-all]
                      [--task-id ID] [--output-dir DIR] [--timeout SECS]
                      [--template-dir DIR] [--overwrite] [--limit N] [--workers N]
                      [--language LANG] [--exclude-lang LANG]

Options:
  --instance-id ID    Single instance_id to convert
  --all / --no-all    Convert all instances (default: --all)
  --task-id ID        Local task directory name (default: instance-id)
  --output-dir DIR    Output root (default: ../../datasets/swerebenchv2)
  --timeout SECS      Agent/verifier timeout (default: 3000)
  --template-dir DIR  Override template directory
  --overwrite         Overwrite existing task directories
  --limit N           Max instances to convert
  --workers N         Task-generation threads (default: 8)
  --language LANG     Filter by programming language
  --exclude-lang LANG Exclude a programming language; repeatable
```

Fixed 200-task teacher-selection subset:

```
usage: generate_teacher_selection_200.py [-h] [--output-dir DIR]
                                         [--task-ids-file FILE]
                                         [--timeout SECS]
                                         [--template-dir DIR]
                                         [--overwrite]

Options:
  --output-dir DIR     Output Harbor tasks root directory
                       (default: ../../datasets/swerebenchv2-200-260429)
  --task-ids-file FILE Path to the fixed task id list
  --timeout SECS       Agent/verifier timeout (default: 3000)
  --template-dir DIR   Override template directory
  --overwrite          Overwrite existing task directories
```

## Links

- [SWE-rebench-V2 Dataset](https://huggingface.co/datasets/nebius/SWE-rebench-V2)
- [SWE-rebench-V2 Evaluation Code](https://github.com/SWE-rebench/SWE-rebench-V2)
- [SWE-rebench-V2 Paper](https://arxiv.org/abs/2602.23866)
- [Harbor Documentation](https://harborframework.com/docs)

## Parity Experiments

Parity experiments pending. Results will be recorded in `parity_experiment.json`.
