# Additional Benchmark Adapters

This fork adds adapters for OpenSWE and SWE-rebench-V2. Each adapter produces
standard Harbor task directories with an instruction, environment, tests, and
an optional oracle solution.

## OpenSWE

`adapters/openswe/` converts the gated `GAIR/OpenSWE` dataset.

Key behavior:

- supports the gold-patch `openswe_oss` and evaluation-only `openswe_other`
  configurations;
- reconstructs unpublished OpenSWE base-image behavior from a public Miniconda
  image;
- clones each repository at its base commit;
- removes inline gold-patch application from released evaluation scripts;
- reapplies the released test patch separately during verification;
- includes oracle solutions where a gold patch exists;
- supports selected instances, conversion limits, and filtered subsets.

```bash
cd adapters/openswe
uv run run_adapter.py --config openswe_oss --limit 2
```

The source dataset requires accepting its Hugging Face access terms. Some
released Dockerfiles refer to build-context files that are not available; such
instances are reported as conversion/build failures.

## SWE-rebench-V2

`adapters/swerebenchv2/` converts the multi-language SWE-rebench-V2 dataset.

Key behavior:

- supports more than twenty programming languages;
- uses per-instance prebuilt images and `install_config` test commands;
- grades with the released language-specific log parsers;
- applies test patches separately from agent changes;
- supports include/exclude language filters and parallel generation;
- includes oracle solution scripts and a pinned 200-task selection helper.

```bash
cd adapters/swerebenchv2
uv run run_adapter.py --limit 2
uv run run_adapter.py --language python --limit 2
```

## Adapter Acceptance Criteria

The following are release acceptance criteria, not a claim that every item has
already passed for every dataset instance:

1. conversion succeeds for a small representative sample;
2. generated `task.toml` files validate;
3. environment paths and test commands match the task layout;
4. an oracle trial passes when a gold solution exists;
5. a no-op trial fails for a task that requires a change;
6. unsupported or heuristic modes are clearly documented;
7. parity status is recorded in the adapter's `parity_experiment.json`.

See the adapter-specific README files for source licensing, full CLI options,
task layouts, and known dataset limitations.

Current status: unit-level conversion and parser coverage exists, while full
dataset conversion, image-build coverage, and oracle/no-op parity remain
environment-dependent and are not certified by this repository. Consult each
adapter's `parity_experiment.json` before citing parity results.
