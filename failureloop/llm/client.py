"""Shared LLM client for every FailureLoop model call.

Points the OpenAI SDK at an OpenAI-compatible provider (Groq by default).
All JSON cleaning, validation repair retries, fallback-model switching and
rate-limit backoff live here so callers never touch the SDK directly.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable, TypeVar

from openai import OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from failureloop.config import Config

logger = logging.getLogger("failureloop.llm")

T = TypeVar("T", bound=BaseModel)

MAX_BACKOFF_SECONDS = 8.0


class LLMError(RuntimeError):
    """Raised when the LLM cannot produce a valid response after all retries."""


class LLMClient:
    """Single entry point for all FailureLoop LLM calls.

    - complete_json: JSON output validated against a Pydantic schema, with
      repair retries feeding validation errors back, then fallback model.
    - complete_text: plain text with the same retry/fallback policy.
    - 429 responses get exponential backoff.
    - Every success logs model, latency and retry count.
    """

    def __init__(
        self,
        config: Config,
        client: OpenAI | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        api_key = config.llm_api_key or config.openai_api_key
        if not api_key:
            raise LLMError("No LLM API key configured (set LLM_API_KEY in .env).")
        self.config = config
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=config.llm_base_url,
        )
        self._sleep = sleep_fn

    # ------------------------------------------------------------- internals

    def _chat(self, model: str, system: str, user: str, json_mode: bool) -> str:
        """Single chat call with 429 exponential backoff. Returns raw content."""
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        delay = 1.0
        while True:
            try:
                response = self._client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except RateLimitError:
                if delay >= MAX_BACKOFF_SECONDS:
                    raise
                logger.warning("Rate limited (429) on %s; backing off %.1fs", model, delay)
                self._sleep(delay)
                delay = min(delay * 2, MAX_BACKOFF_SECONDS)

    @staticmethod
    def _strip_to_json(text: str) -> str:
        """Strip markdown fences and reasoning text around a JSON object."""
        cleaned = text.strip()
        # Remove <think>...</think> reasoning blocks (qwen-style models).
        cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
        if fence:
            cleaned = fence.group(1).strip()
        if not cleaned.startswith(("{", "[")):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end > start:
                cleaned = cleaned[start : end + 1]
        return cleaned

    def _log(self, model: str, latency: float, retries: int) -> None:
        logger.info("llm model=%s latency=%.2fs retries=%d", model, latency, retries)

    def _attempt_pairs(self) -> list[tuple[str, int]]:
        """(model, max_tries) pairs: primary first, then fallback."""
        tries = self.config.llm_max_retries + 1
        return [(self.config.llm_model, tries), (self.config.llm_fallback_model, tries)]

    # ------------------------------------------------------------------ JSON

    def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        """Request JSON, validate against `schema`, repair on failure.

        Retries with the validation error fed back to the model, then switches
        to the fallback model, then raises LLMError.
        """
        validation_error = ""
        last_error: Exception | None = None
        total_attempts = 0

        for model, max_tries in self._attempt_pairs():
            for _ in range(max_tries):
                total_attempts += 1
                prompt = user
                if validation_error:
                    prompt = (
                        f"{user}\n\nYour previous reply was invalid or failed schema "
                        f"validation. Validation error:\n{validation_error}\n\n"
                        f"Return ONLY corrected JSON matching the schema. No prose."
                    )
                started = time.perf_counter()
                try:
                    raw = self._chat(model, system, prompt, json_mode=True)
                except Exception as exc:  # API/network failure on this model
                    last_error = exc
                    logger.warning("LLM call failed on %s: %s", model, exc)
                    validation_error = ""
                    continue
                latency = time.perf_counter() - started
                try:
                    payload = json.loads(self._strip_to_json(raw))
                    result = schema.model_validate(payload)
                    self._log(model, latency, total_attempts - 1)
                    return result
                except (json.JSONDecodeError, ValidationError) as exc:
                    validation_error = str(exc)[:2000]
                    last_error = exc
                    logger.warning("Invalid JSON from %s (attempt %d)", model, total_attempts)

        raise LLMError(
            f"LLM could not produce valid {schema.__name__} JSON after "
            f"{total_attempts} attempts across primary and fallback models. "
            f"Last error: {last_error}"
        )

    # ------------------------------------------------------------------ text

    def complete_text(self, system: str, user: str) -> str:
        """Plain text completion with the same retry/fallback policy."""
        last_error: Exception | None = None
        total_attempts = 0

        for model, max_tries in self._attempt_pairs():
            for _ in range(max_tries):
                total_attempts += 1
                started = time.perf_counter()
                try:
                    raw = self._chat(model, system, user, json_mode=False)
                except Exception as exc:
                    last_error = exc
                    logger.warning("LLM call failed on %s: %s", model, exc)
                    continue
                if raw.strip():
                    self._log(model, time.perf_counter() - started, total_attempts - 1)
                    return raw
                last_error = LLMError("empty response")

        raise LLMError(
            f"LLM text completion failed after {total_attempts} attempts. Last error: {last_error}"
        )
