# uv Cache Space

If dependency installation fails because the default cache filesystem is full,
point uv at a filesystem with sufficient space:

```bash
export UV_CACHE_DIR=/path/with/free-space/uv-cache
uv sync --all-extras --dev
```

Add the export to your shell configuration only after confirming the selected
path is persistent and private to the intended users.

Inspect or clean the cache with:

```bash
uv cache dir
uv cache clean
```

Cache cleanup is recoverable through re-downloading, but it can make the next
installation significantly slower.
