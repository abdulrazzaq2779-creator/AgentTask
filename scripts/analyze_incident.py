"""Analyze an incident in stateless, memory, or both modes.

Examples:
  python scripts/analyze_incident.py --demo 1 --mode both
  python scripts/analyze_incident.py --text "checkout timeouts, workers at 100%" --mode memory
  python scripts/analyze_incident.py --file incident.txt --mode stateless
  python scripts/analyze_incident.py --demo 1 --mode both --retain   # explicit opt-in retention
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# Allow running as a script from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def _safe_console() -> Console:
    """Console that never crashes on characters cp1252 cannot encode
    (LLM output may contain Unicode dashes, arrows, etc.)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # non-reconfigurable stdout; rich will degrade gracefully
    return Console()


console = _safe_console()

from failureloop.config import get_config
from failureloop.extraction.extractor import FingerprintExtractor
from failureloop.llm.client import LLMClient
from failureloop.memory import MemoryLayer
from failureloop.recommend.engine import (
    EngineDeps,
    recommend_stateless,
    recommend_with_memory,
)
from failureloop.storage import Storage
from scripts.demo_data import load_demo_dataset

console = Console()


def _print_recommendation(title: str, output) -> None:
    rec = output.recommendation
    console.print(
        Panel(
            f"[bold]{rec.likely_pattern.name}[/bold]\n{rec.likely_pattern.explanation}",
            title=f"{title} | confidence {rec.confidence:.2f} | {output.model_used}",
            border_style="cyan" if output.mode == "memory" else "yellow",
        )
    )
    steps = Table.grid(padding=(0, 1))
    for i, step in enumerate(rec.recommended_next_steps, 1):
        citations = (
            f" [dim]({', '.join(step.supporting_incident_ids)})[/dim]"
            if step.supporting_incident_ids
            else " [dim](general reasoning)[/dim]"
        )
        steps.add_row(f"{i}.", step.step + citations)
        if step.rationale:
            steps.add_row("", f"[dim]{step.rationale}[/dim]")
    console.print(steps)
    if rec.fixes_to_avoid:
        console.print("[red]Fixes to avoid:[/red]")
        for fix in rec.fixes_to_avoid:
            console.print(f"  x {fix.action} - {fix.why} [dim]({', '.join(fix.incident_ids)})[/dim]")
    if rec.matched_incidents:
        console.print("[cyan]Matched incidents:[/cyan]")
        for m in rec.matched_incidents:
            console.print(
                f"  = {m.incident_id} ({m.similarity:.2f}) {m.title} - {m.why_matched}"
                + (f" [yellow]| DIFFERS: {m.differences}[/yellow]" if m.differences else "")
            )
    if rec.alternative_hypotheses:
        console.print("[magenta]Alternative hypotheses:[/magenta]")
        for h in rec.alternative_hypotheses:
            console.print(f"  ? {h}")
    if rec.data_gaps:
        console.print("[dim]Data gaps:[/dim]")
        for gap in rec.data_gaps:
            console.print(f"  - {gap}")
    console.print(f"[dim]{rec.safety_note}[/dim]")


def main() -> int:
    parser = argparse.ArgumentParser(description="FailureLoop incident analysis")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Raw incident text")
    source.add_argument("--file", help="Path to a file with incident text")
    source.add_argument("--demo", type=int, help="Demo incident number from data/demo_incidents.json")
    parser.add_argument("--mode", choices=["stateless", "memory", "both"], default="both")
    parser.add_argument(
        "--retain",
        action="store_true",
        help="Explicitly retain this incident to Hindsight (normally Stage 3 does this after feedback)",
    )
    parser.add_argument("--incident-id", default="", help="Optional incident ID")
    args = parser.parse_args()

    if args.demo:
        demo = load_demo_dataset().by_number(args.demo)
        raw_text, incident_id = demo.text, demo.incident_id
        console.print(f"[bold]Demo incident #{args.demo}: {demo.title}[/bold]\n")
    elif args.file:
        raw_text = Path(args.file).read_text(encoding="utf-8")
        incident_id = args.incident_id
    else:
        raw_text, incident_id = args.text, args.incident_id

    config = get_config()
    if not config.has_llm and not config.openai_api_key:
        console.print("[red]No LLM key configured. Set LLM_API_KEY (Groq) in .env[/red]")
        return 2

    llm = LLMClient(config)
    memory = MemoryLayer(config)
    storage = Storage(config.db_path)
    # Local-fallback mode: SQLite doubles as the recall corpus.
    memory.seed_local_from_storage(storage)

    console.print("[dim]Extracting fingerprint...[/dim]")
    extraction = FingerprintExtractor(llm).extract(raw_text, incident_id=incident_id)
    fp = extraction.fingerprint
    chain = " -> ".join(fp.failure_chain) if fp.failure_chain else "(none extracted)"
    console.print(
        Panel(
            f"service=[bold]{fp.service or '?'}[/bold] dependency=[bold]{fp.dependency or '?'}[/bold]\n"
            f"trigger={fp.triggering_change or '?'}\n"
            f"chain: {chain}\n"
            f"root_cause={fp.root_cause or '(unknown; not yet confirmed)'}\n"
            f"field_confidence={extraction.field_confidence}",
            title="Failure fingerprint",
            border_style="green",
        )
    )
    if extraction.extraction_notes:
        console.print("[dim]Extraction notes:[/dim]")
        for note in extraction.extraction_notes:
            console.print(f"  - {note}")

    results = {}
    if args.mode in ("stateless", "both"):
        console.print("\n[yellow]Generating STATELESS recommendation (no memory)...[/yellow]")
        results["stateless"] = recommend_stateless(
            fp, raw_text, llm, incident_id=incident_id, storage=storage
        )
    if args.mode in ("memory", "both"):
        console.print("\n[cyan]Generating MEMORY recommendation (recall + fixes + reflect)...[/cyan]")
        deps = EngineDeps(llm=llm, memory=memory, storage=storage)
        results["memory"] = recommend_with_memory(
            fp, raw_text, deps, incident_id=incident_id
        )

    console.print()
    for title_prefix, output in results.items():
        _print_recommendation(f"{output.mode.upper()}", output)
        console.print()

    if "stateless" in results and "memory" in results:
        s_rec = results["stateless"].recommendation
        m_rec = results["memory"].recommendation
        delta = m_rec.confidence - s_rec.confidence
        summary = Table(title="What memory added", show_header=False)
        summary.add_row(
            "Extra evidence",
            f"{len(m_rec.matched_incidents)} matched incident(s) cited"
            if m_rec.matched_incidents
            else "none",
        )
        summary.add_row(
            "Fixes to avoid",
            f"{len(m_rec.fixes_to_avoid)}"
            if m_rec.fixes_to_avoid
            else "none identified",
        )
        summary.add_row(
            "Confidence",
            f"stateless {s_rec.confidence:.2f} -> memory {m_rec.confidence:.2f} "
            f"({'+' if delta >= 0 else ''}{delta:.2f})",
        )
        console.print(summary)

    if args.retain:
        memory.retain_incident(fp, raw_text=raw_text)
        console.print("[green]Retained incident to memory (explicit --retain).[/green]")
    else:
        console.print(
            "[dim]Not retained (auto-retention happens in Stage 3 after engineer feedback; "
            "use --retain to opt in).[/dim]"
        )

    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
