"""Prompt template loader.

Prompt templates live in the repo-level prompts/ folder so they can be
reviewed and edited without touching code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Load a prompt template by file name (with or without extension)."""
    candidates = [PROMPTS_DIR / name, PROMPTS_DIR / f"{name}.md", PROMPTS_DIR / f"{name}.txt"]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt template not found: {name} in {PROMPTS_DIR}")
