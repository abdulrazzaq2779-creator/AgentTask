"""Shared Stage 2 test fixtures: fake LLM, fake Hindsight memory, seeded storage.

No test in this suite touches the network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from failureloop.data import HISTORICAL_INCIDENTS
from failureloop.fingerprint import FailureFingerprint
from failureloop.matching.matcher import CandidateJudgeResponse, CandidateScore
from failureloop.memory import MemoryLayer
from failureloop.recommend.models import Recommendation
from failureloop.storage import Storage


# ---------------------------------------------------------------- fake LLM

class FakeLLM:
    """Scripted stand-in for LLMClient, dispatched by requested schema."""

    def __init__(self, responses_by_schema: dict[type, list] | None = None):
        self.responses_by_schema = responses_by_schema or {}
        self.json_calls: list[dict] = []
        self.text_calls: list[dict] = []
        self.config = SimpleNamespace(
            llm_model="fake-primary", llm_fallback_model="fake-fallback"
        )

    def complete_json(self, system: str, user: str, schema):
        self.json_calls.append({"system": system, "user": user, "schema": schema})
        queue = self.responses_by_schema.get(schema, [])
        if not queue:
            raise AssertionError(f"FakeLLM has no scripted response for {schema.__name__}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def complete_text(self, system: str, user: str) -> str:
        self.text_calls.append({"system": system, "user": user})
        return "fake text"


# ---------------------------------------------------------------- fake memory

class FakeMemory(MemoryLayer):
    """MemoryLayer that never calls Hindsight; recall is scripted."""

    def __init__(self):
        # Skip the real __init__: no config, no client.
        self.config = SimpleNamespace(
            hindsight_api_key="", hindsight_base_url="", hindsight_bank_id="test"
        )
        self.pattern_hits: list[dict] = []
        self.surface_hits: list[dict] = []
        self.reflection: str = ""
        self.recall_calls: list[str] = []
        self.reflect_calls: list[str] = []
        self.retained: list = []

    def recall_text(self, query: str, max_results: int = 10) -> list[dict]:
        self.recall_calls.append(query)
        if "surface symptom" in query:
            return list(self.surface_hits)
        return list(self.pattern_hits)

    def reflect_text(self, query: str) -> str:
        self.reflect_calls.append(query)
        return self.reflection

    def retain_incident(self, *args, **kwargs):  # pragma: no cover - Stage 3
        self.retained.append((args, kwargs))

    def seed_local_from_storage(self, storage) -> int:  # pragma: no cover - no-op in tests
        return 0


# ---------------------------------------------------------------- storage

@pytest.fixture()
def seeded_storage(tmp_path) -> Storage:
    """SQLite storage pre-loaded with a few historical incidents."""
    storage = Storage(str(tmp_path / "test.db"))
    wanted = {"INC-104", "INC-112", "INC-118", "INC-124"}
    for incident in HISTORICAL_INCIDENTS:
        if incident["incident_id"] not in wanted:
            continue
        fp = incident["fingerprint"].model_copy(deep=True)
        fp.incident_id = incident["incident_id"]
        storage.save_incident(fp, incident["raw_text"], status="resolved")
    yield storage
    storage.close()


def make_judge_response(scores: dict[str, float]) -> CandidateJudgeResponse:
    return CandidateJudgeResponse(
        scores=[
            CandidateScore(incident_id=iid, causal_similarity=score, rationale="scripted")
            for iid, score in scores.items()
        ]
    )


def make_recommendation(**overrides) -> Recommendation:
    defaults = {
        "confidence": 0.7,
        "confidence_rationale": "scripted",
        "safety_note": "",
    }
    defaults.update(overrides)
    return Recommendation(**defaults)


def fingerprint_by_id(incident_id: str) -> FailureFingerprint:
    for incident in HISTORICAL_INCIDENTS:
        if incident["incident_id"] == incident_id:
            fp = incident["fingerprint"].model_copy(deep=True)
            fp.incident_id = incident_id
            return fp
    raise KeyError(incident_id)
