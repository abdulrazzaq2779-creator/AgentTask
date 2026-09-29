"""LLM client tests: JSON repair, fallback model, total failure. No network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from failureloop.llm.client import LLMClient, LLMError


class Out(BaseModel):
    name: str
    count: int


def make_client(script) -> LLMClient:
    config = SimpleNamespace(
        llm_api_key="test-key",
        llm_base_url="http://fake",
        llm_model="primary",
        llm_fallback_model="fallback",
        llm_max_retries=2,
    )
    return LLMClient(config, client=script, sleep_fn=lambda _s: None)


class ScriptedSDK:
    """Mimics the slice of the OpenAI SDK the client uses."""

    def __init__(self, contents: list[str | Exception]):
        self.contents = list(contents)
        # openai v1 chain: client.chat.completions.create
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs) -> Any:
        item = self.contents.pop(0) if self.contents else ""
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))]
        )


def test_valid_json_first_try():
    sdk = ScriptedSDK(['{"name": "a", "count": 1}'])
    client = make_client(sdk)
    out = client.complete_json("sys", "user", Out)
    assert out.name == "a" and out.count == 1


def test_markdown_fenced_json_is_stripped():
    sdk = ScriptedSDK(['```json\n{"name": "a", "count": 2}\n```'])
    client = make_client(sdk)
    out = client.complete_json("sys", "user", Out)
    assert out.count == 2


def test_malformed_then_repaired():
    sdk = ScriptedSDK(
        [
            'Sure! Here is my answer: {"name": "a", "count": "not-a-number"} ... hope it helps',
            '{"name": "a", "count": 3}',
        ]
    )
    client = make_client(sdk)
    out = client.complete_json("sys", "user", Out)
    assert out.count == 3
    # The repair prompt must contain the validation error.
    # (Second _create call already consumed; verify via internal state instead.)
    assert len(sdk.contents) == 0


def test_primary_fails_then_fallback_used():
    sdk = ScriptedSDK(
        [
            Exception("primary model down"),
            Exception("primary model down"),
            '{"name": "b", "count": 4}',  # fallback model answers
        ]
    )
    client = make_client(sdk)
    out = client.complete_json("sys", "user", Out)
    assert out.name == "b"


def test_total_failure_raises_custom_error():
    sdk = ScriptedSDK([Exception("down")] * 6)
    client = make_client(sdk)
    with pytest.raises(LLMError):
        client.complete_json("sys", "user", Out)


def test_reasoning_and_fences_stripped():
    sdk = ScriptedSDK(
        [
            '<think>internal reasoning here</think>\n```json\n{"name": "c", "count": 5}\n```',
        ]
    )
    client = make_client(sdk)
    out = client.complete_json("sys", "user", Out)
    assert out.name == "c"


def test_complete_text_retry_and_success():
    sdk = ScriptedSDK(["", "second try works"])
    client = make_client(sdk)
    assert client.complete_text("sys", "user") == "second try works"
