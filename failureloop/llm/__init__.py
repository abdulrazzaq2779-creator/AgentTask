"""Shared LLM layer: one client, retry/fallback policy, prompt templates."""

from failureloop.llm.client import LLMClient, LLMError

__all__ = ["LLMClient", "LLMError"]
