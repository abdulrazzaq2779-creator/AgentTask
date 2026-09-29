"""Light evaluation: run demo incidents in both modes and check outputs.

For each demo incident, checks (via one cheap LLM-judge call per check, with a
deterministic fallback) whether the expected pattern and the expected
successful fix appear in the memory-mode recommendation, and reports the
confidence delta between modes.

  python scripts/evaluate.py
"""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table


def _safe_console() -> Console:
    """Console that never crashes on characters cp1252 cannot encode."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    return Console()


console = _safe_console()

from failureloop.config import get_config
from failureloop.extraction.extractor import FingerprintExtractor
from failureloop.llm.client import LLMClient
from failureloop.memory import MemoryLayer
from failureloop.recommend.engine import EngineDeps, recommend_stateless, recommend_with_memory
from failureloop.recommend.models import Recommendation
from failureloop.storage import Storage
from scripts.demo_data import load_demo_dataset

console = Console()


class JudgeVerdict(BaseModel):
    """LLM-judge answer for one check."""

    satisfied: bool
    reason: str = ""


JUDGE_SYSTEM = (
    "You are a strict evaluator. Given a recommendation JSON and a target "
    "description, decide whether the recommendation satisfies the target. "
    'Answer JSON {"satisfied": true|false, "reason": "one sentence"}.'
)


def _llm_judge_check(llm: LLMClient, rec: Recommendation, target: str) -> tuple[bool, str]:
    payload = rec.model_dump_json()
    try:
        verdict = llm.complete_json(
            JUDGE_SYSTEM,
            f"RECOMMENDATION:\n{payload[:8000]}\n\nTARGET: {target}",
            JudgeVerdict,
        )
        return verdict.satisfied, verdict.reason
    except Exception as exc:
        # Deterministic fallback: case-insensitive substring check.
        text = json.dumps(rec.model_dump()).lower()
        ok = target.lower() in text
        return ok, f"fallback substring check ({exc})"


@dataclass
class EvalRow:
    demo_id: str
    pattern_found: bool
    pattern_reason: str
    fix_found: bool
    fix_reason: str
    confidence_stateless: float
    confidence_memory: float


def main() -> int:
    config = get_config()
    if not config.has_llm and not config.openai_api_key:
        console.print("[red]No LLM key configured. Set LLM_API_KEY in .env[/red]")
        return 2

    llm = LLMClient(config)
    memory = MemoryLayer(config)
    storage = Storage(config.db_path)
    memory.seed_local_from_storage(storage)
    deps = EngineDeps(llm=llm, memory=memory, storage=storage)
    dataset = load_demo_dataset()

    if storage.incident_count() == 0:
        console.print("[red]Memory bank empty. Run: python scripts/load_history.py[/red]")
        return 2

    rows: list[EvalRow] = []
    for demo in dataset.incidents:
        console.print(f"[dim]Evaluating {demo.incident_id} ({demo.title})...[/dim]")
        extraction = FingerprintExtractor(llm).extract(demo.text, incident_id=demo.incident_id)
        fp = extraction.fingerprint
        stateless = recommend_stateless(
            fp, demo.text, llm, incident_id=demo.incident_id, storage=storage
        )
        mem = recommend_with_memory(fp, demo.text, deps, incident_id=demo.incident_id)

        pattern_ok, pattern_why = _llm_judge_check(
            llm, mem.recommendation, f"identifies the failure pattern: {demo.expected_pattern}"
        )
        if demo.expected_successful_fix:
            fix_ok, fix_why = _llm_judge_check(
                llm,
                mem.recommendation,
                f"recommends or mentions the successful fix: {demo.expected_successful_fix}",
            )
        else:
            fix_ok, fix_why = True, "no expected fix for this incident"

        rows.append(
            EvalRow(
                demo_id=demo.incident_id,
                pattern_found=pattern_ok,
                pattern_reason=pattern_why,
                fix_found=fix_ok,
                fix_reason=fix_why,
                confidence_stateless=stateless.recommendation.confidence,
                confidence_memory=mem.recommendation.confidence,
            )
        )

    table = Table(title="FailureLoop Stage 2 evaluation")
    table.add_column("incident")
    table.add_column("pattern")
    table.add_column("expected fix")
    table.add_column("conf (stateless)")
    table.add_column("conf (memory)")
    table.add_column("delta")
    for row in rows:
        delta = row.confidence_memory - row.confidence_stateless
        table.add_row(
            row.demo_id,
            "[green]OK[/green]" if row.pattern_found else "[red]MISS[/red]",
            "[green]OK[/green]" if row.fix_found else "[red]MISS[/red]",
            f"{row.confidence_stateless:.2f}",
            f"{row.confidence_memory:.2f}",
            f"{delta:+.2f}",
        )
    console.print(table)

    pattern_accuracy = sum(r.pattern_found for r in rows) / max(len(rows), 1)
    fix_accuracy = sum(r.fix_found for r in rows) / max(len(rows), 1)
    console.print(
        f"Pattern accuracy: {pattern_accuracy:.0%} | Expected-fix accuracy: {fix_accuracy:.0%}"
    )
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
