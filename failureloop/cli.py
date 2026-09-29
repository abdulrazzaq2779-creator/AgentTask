"""Command-line interface for FailureLoop.

Examples:
  python -m failureloop.cli load-history
  python -m failureloop.cli analyze --text "Checkout requests timing out, worker util 100%"
  python -m failureloop.cli analyze --example INC-201
  python -m failureloop.cli analyze --example INC-201 --mode stateless
  python -m failureloop.cli feedback INC-201 worked "retry cap fixed it" --fix "bounded retries + circuit breaker"
  python -m failureloop.cli stats
"""

from __future__ import annotations

import argparse
import json

from failureloop.agent import FailureLoopAgent
from failureloop.data import TEST_INCIDENTS


def main() -> None:
    parser = argparse.ArgumentParser(description="FailureLoop incident agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("load-history", help="Load the 8 synthetic historical incidents")

    p_analyze = sub.add_parser("analyze", help="Analyze a new incident")
    p_analyze.add_argument("--text", help="Raw incident text")
    p_analyze.add_argument("--example", help=f"One of {[t['incident_id'] for t in TEST_INCIDENTS]}")
    p_analyze.add_argument("--mode", choices=["memory", "stateless"], default="memory")

    p_fb = sub.add_parser("feedback", help="Record engineer feedback")
    p_fb.add_argument("incident_id")
    p_fb.add_argument("outcome", choices=["worked", "partially_worked", "failed", "root_cause_different", "not_verified"])
    p_fb.add_argument("text", nargs="?", default="")
    p_fb.add_argument("--root-cause", default="")
    p_fb.add_argument("--fix", default="")

    sub.add_parser("stats", help="Show memory statistics")

    args = parser.parse_args()
    agent = FailureLoopAgent()

    if args.command == "load-history":
        n = agent.load_historical()
        print(f"Loaded {n} historical incidents.")
        print(json.dumps(agent.memory_stats(), indent=2))

    elif args.command == "analyze":
        text = args.text
        if args.example:
            text = next((t["raw_text"] for t in TEST_INCIDENTS if t["incident_id"] == args.example), "")
        if not text:
            parser.error("Provide --text or --example")
        incident_id = args.example or ""
        result = agent.analyze(text, incident_id=incident_id, mode=args.mode)
        print(f"\n=== {result.incident_id} ({args.mode} mode, memory: {result.memory_mode}) ===")
        print("\nFingerprint:")
        fp = {k: v for k, v in result.fingerprint.model_dump().items() if v}
        print(json.dumps(fp, indent=2))
        print("\nMatches:")
        for m in result.matches:
            print(f"  {m.incident_id}: {m.score:.2f} ({', '.join(m.reasons) or 'overlap'})")
        if not result.matches:
            print("  (none; possible new failure pattern)")
        rec = result.recommendation
        print(f"\nConfidence: {rec.confidence.upper()}")
        print(f"Summary: {rec.summary}")
        if rec.likely_root_cause:
            print(f"Likely root cause: {rec.likely_root_cause}")
        print("Investigation steps:")
        for i, step in enumerate(rec.investigation_steps, 1):
            print(f"  {i}. {step}")
        print("Proven fixes:")
        for fix in rec.proven_fixes:
            print(f"  + {fix}")
        print("Failed-fix warnings:")
        for w in rec.failed_fix_warnings:
            print(f"  ! {w}")
        print("Evidence:")
        for ev in rec.evidence:
            print(f"  - {ev}")

    elif args.command == "feedback":
        result = agent.record_feedback(
            incident_id=args.incident_id,
            outcome=args.outcome,
            feedback_text=args.text,
            confirmed_root_cause=args.root_cause,
            confirmed_fix=args.fix,
        )
        print("Feedback recorded:")
        print(json.dumps({k: v for k, v in result.items() if k != "updated_fingerprint"}, indent=2))

    elif args.command == "stats":
        print(json.dumps(agent.memory_stats(), indent=2))

    agent.close()


if __name__ == "__main__":
    main()
