"""FastAPI backend for the FailureLoop React frontend.

Run:  uvicorn failureloop.api:app --port 8000

Auto-seeds the memory bank with the 10 bundled incidents on startup so the
UI is never empty. Endpoints:
  GET  /api/health                 - config + counts
  GET  /api/incidents              - all stored incidents (summary)
  GET  /api/incidents/{id}         - full fingerprint
  POST /api/analyze                - full pipeline: extract, recall, recommend
  GET  /api/demo-incidents         - the 4 demo scenarios (for the picker)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from failureloop.config import get_config
from failureloop.data import HISTORICAL_INCIDENTS
from failureloop.extraction.extractor import FingerprintExtractor
from failureloop.llm.client import LLMClient
from failureloop.matching.fix_ranker import rank_fixes
from failureloop.matching.matcher import find_similar_incidents
from failureloop.memory import MemoryLayer
from failureloop.recommend.engine import (
    EngineDeps,
    recommend_stateless,
    recommend_with_memory,
)
from failureloop.storage import Storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("failureloop.api")

app = FastAPI(title="FailureLoop API", version="0.2.0")

# React dev server + local previews.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local demo; tighten for real deployments
    allow_methods=["*"],
    allow_headers=["*"],
)

config = get_config()
storage = Storage(config.db_path)
memory = MemoryLayer(config)
memory.seed_local_from_storage(storage)
llm: LLMClient | None = None
if config.has_llm:
    try:
        llm = LLMClient(config)
    except Exception as exc:  # pragma: no cover
        logger.error("LLM client failed: %s", exc)


@app.on_event("startup")
def seed_history() -> None:
    """Preload the 10 bundled incidents so the demo is never empty."""
    existing = {fp.incident_id for fp in storage.get_fingerprint_map().values()}
    added = 0
    for incident in HISTORICAL_INCIDENTS:
        iid = incident["incident_id"]
        if iid in existing:
            continue
        fp = incident["fingerprint"].model_copy(deep=True)
        fp.incident_id = iid
        storage.save_incident(fp, incident["raw_text"], status="resolved")
        memory.retain_incident(fp, raw_text=incident["raw_text"])
        added += 1
    # 8 bundled + demo scenarios that were retained previously.
    logger.info("seeded %d new incidents (bank now has %d)", added, storage.incident_count())


class AnalyzeRequest(BaseModel):
    text: str
    incident_id: str = ""
    mode: str = "both"  # stateless | memory | both


def _serialize_fingerprint(fp: Any) -> dict:
    return fp.model_dump()


def _serialize_match(m: Any) -> dict:
    fp = m.fingerprint
    return {
        "incident_id": m.incident_id,
        "total": m.total,
        "components": m.components,
        "match_type": m.match_type,
        "judge_rationale": m.judge_rationale,
        "difference_note": m.difference_note,
        "title": (fp.symptom if fp else "")[:140],
        "service": fp.service if fp else "",
        "dependency": fp.dependency if fp else "",
        "failure_chain": fp.failure_chain if fp else [],
        "successful_fixes": fp.successful_fixes if fp else [],
        "failed_fixes": fp.failed_fixes if fp else [],
        "severity": fp.severity if fp else "",
    }


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_configured": llm is not None,
        "llm_model": config.llm_model,
        "memory_mode": memory.mode,
        "incident_count": storage.incident_count(),
    }


@app.get("/api/demo-incidents")
def demo_incidents() -> list[dict]:
    from scripts.demo_data import load_demo_dataset

    return [
        {
            "incident_id": d.incident_id,
            "demo_number": d.demo_number,
            "title": d.title,
            "text": d.text,
            "expected_pattern": d.expected_pattern,
        }
        for d in load_demo_dataset().incidents
    ]


@app.get("/api/incidents")
def list_incidents() -> list[dict]:
    fps = storage.get_fingerprint_map()
    created = storage.get_incident_created_map()
    result = []
    for iid, fp in sorted(fps.items()):
        result.append(
            {
                "incident_id": iid,
                "service": fp.service,
                "symptom": fp.symptom,
                "dependency": fp.dependency,
                "severity": fp.severity,
                "root_cause": fp.root_cause,
                "failure_chain": fp.failure_chain,
                "successful_fixes": fp.successful_fixes,
                "failed_fixes": fp.failed_fixes,
                "preventive_action": fp.preventive_action,
                "created_at": created.get(iid, ""),
                "resolved": fp.final_outcome == "Resolved" or fp.final_outcome == "",
            }
        )
    return result


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict:
    loaded = storage.load_incident(incident_id)
    if loaded is None:
        raise HTTPException(404, f"Unknown incident {incident_id}")
    fp, raw = loaded
    return {"fingerprint": _serialize_fingerprint(fp), "raw_text": raw}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    if llm is None:
        raise HTTPException(
            503, "No LLM key configured. Set LLM_API_KEY in .env and restart the API."
        )
    if not req.text.strip():
        raise HTTPException(400, "text must be non-empty")

    extraction = FingerprintExtractor(llm).extract(req.text, incident_id=req.incident_id)
    fp = extraction.fingerprint

    matching = None
    if req.mode in ("memory", "both"):
        matching = find_similar_incidents(fp, memory=memory, storage=storage, llm=llm)
        ranked = rank_fixes(matching.matches)
        fix_evidence = {
            "proven": [
                {
                    "action": f.action,
                    "incidents": f.supporting_incident_ids,
                    "count": f.incident_count,
                    "rank_score": f.rank_score,
                }
                for f in ranked.proven_fixes
            ],
            "failed": [
                {"action": f.action, "incidents": f.supporting_incident_ids}
                for f in ranked.failed_fixes
            ],
            "conflicting": [
                {
                    "action": c.action,
                    "worked_in": c.worked_in,
                    "failed_in": c.failed_in,
                }
                for c in ranked.conflicting_fixes
            ],
        }
    else:
        fix_evidence = {"proven": [], "failed": [], "conflicting": []}

    stateless = (
        recommend_stateless(fp, req.text, llm, incident_id=fp.incident_id, storage=storage)
        if req.mode in ("stateless", "both")
        else None
    )
    memory_rec = (
        recommend_with_memory(
            fp,
            req.text,
            EngineDeps(llm=llm, memory=memory, storage=storage),
            incident_id=fp.incident_id,
            matching=matching,
        )
        if req.mode in ("memory", "both")
        else None
    )

    return {
        "incident_id": fp.incident_id,
        "fingerprint": _serialize_fingerprint(fp),
        "extraction_notes": extraction.extraction_notes,
        "field_confidence": extraction.field_confidence,
        "matches": [_serialize_match(m) for m in (matching.matches if matching else [])],
        "cold_start": matching.cold_start if matching else False,
        "matching_reason": matching.reason if matching else "",
        "fix_evidence": fix_evidence,
        "recommendations": {
            "stateless": stateless.recommendation.model_dump() if stateless else None,
            "memory": memory_rec.recommendation.model_dump() if memory_rec else None,
        },
    }
