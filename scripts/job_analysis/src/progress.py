"""Small stderr progress display for long local analysis loops."""

from __future__ import annotations

import sys
import time


class Progress:
    """Render a lightweight progress line without requiring third-party deps."""

    def __init__(
        self,
        label: str,
        total: int | None = None,
        *,
        min_interval: float = 0.5,
    ) -> None:
        self.label = label
        self.total = total
        self.min_interval = min_interval
        self.current = 0
        self._last_render = 0.0
        self._is_tty = sys.stderr.isatty()
        self._render(force=True)

    def update(self, step: int = 1) -> None:
        self.current += step
        self._render()

    def close(self) -> None:
        self._render(force=True)
        if self._is_tty:
            sys.stderr.write("\n")
            sys.stderr.flush()

    def _render(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_render < self.min_interval:
            return
        self._last_render = now

        message = self._message()
        end = "\r" if self._is_tty else "\n"
        sys.stderr.write(message + end)
        sys.stderr.flush()

    def _message(self) -> str:
        if self.total is None or self.total <= 0:
            return f"{self.label}: {self.current}"

        pct = min(100.0, self.current / self.total * 100)
        width = 24
        filled = min(width, int(width * pct / 100))
        bar = "#" * filled + "-" * (width - filled)
        return f"{self.label}: [{bar}] {self.current}/{self.total} {pct:5.1f}%"
