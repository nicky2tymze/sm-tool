"""sm-tool — scrum-master pipeline (skeleton).

Iteration 1 lives here. The skeleton defines the module shape and the
LOG_PATH constant; behavior lands in subsequent stories.

Stdlib only; Python 3.10+.
"""

from __future__ import annotations

from pathlib import Path

LOG_PATH: Path = Path(__file__).resolve().parent / "log.jsonl"

__all__ = [
    "LOG_PATH",
]
