"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass
class Config:
    """Runtime configuration for the FailureLoop agent."""

    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o-mini"))

    hindsight_api_key: str = field(default_factory=lambda: _env("HINDSIGHT_API_KEY"))
    hindsight_base_url: str = field(
        default_factory=lambda: _env("HINDSIGHT_BASE_URL", "https://api.hindsight.vectorize.io")
    )
    hindsight_bank_id: str = field(default_factory=lambda: _env("HINDSIGHT_BANK_ID", "failureloop"))

    # --- LLM (Groq via the OpenAI-compatible endpoint) ---
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_base_url: str = field(
        default_factory=lambda: _env("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    )
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "openai/gpt-oss-120b"))
    llm_fallback_model: str = field(
        default_factory=lambda: _env("LLM_FALLBACK_MODEL", "qwen/qwen3.8-27b")
    )
    llm_max_retries: int = field(default_factory=lambda: int(_env("LLM_MAX_RETRIES", "2")))

    db_path: str = field(default_factory=lambda: _env("FAILURELOOP_DB", "failureloop.db"))
    local_fallback: bool = field(
        default_factory=lambda: _env("FAILURELOOP_LOCAL_FALLBACK", "1") not in ("0", "false", "no")
    )

    @property
    def has_openai(self) -> bool:
        # Stage 1 legacy alias; now true when ANY LLM key is configured.
        return bool(self.llm_api_key or self.openai_api_key)

    @property
    def has_hindsight(self) -> bool:
        return bool(self.hindsight_api_key)

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_api_key)


def get_config() -> Config:
    return Config()
