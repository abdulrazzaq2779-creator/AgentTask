"""FailureLoop demo console - interactive Streamlit UI.

Run:  streamlit run failureloop/ui.py

Layout:
- Wide multi-tab console over the Stage 2 engine.
- Analyze: submit an incident, watch stateless vs memory side by side,
  explore match evidence, failure chains, timeline and fix evidence.
- Demo walkthrough: 4 scripted steps that show the memory effect on
  pre-canned demo incidents.
- Memory bank: browse retained incidents and memory stats.
"""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

# Project root importable when run via `streamlit run failureloop/ui.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from failureloop.agent import FailureLoopAgent  # noqa: E402  (Stage 1 load/reset)
from failureloop.config import get_config  # noqa: E402
from failureloop.extraction.extractor import FingerprintExtractor  # noqa: E402
from failureloop.llm.client import LLMClient  # noqa: E402
from failureloop.matching.fix_ranker import rank_fixes  # noqa: E402
from failureloop.matching.matcher import MatchingOutput, find_similar_incidents  # noqa: E402
from failureloop.memory import MemoryLayer  # noqa: E402
from failureloop.recommend.engine import (  # noqa: E402
    EngineDeps,
    recommend_stateless,
    recommend_with_memory,
)
from failureloop.recommend.models import Recommendation, RecommendationOutput  # noqa: E402
from failureloop.storage import Storage  # noqa: E402

try:  # demo dataset lives in scripts/
    from scripts.demo_data import load_demo_dataset  # type: ignore[import-not-found]  # noqa: E402
except Exception:  # pragma: no cover
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "demo_data", Path(__file__).resolve().parents[1] / "scripts" / "demo_data.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    load_demo_dataset = module.load_demo_dataset  # type: ignore[assignment]

st.set_page_config(
    page_title="FailureLoop",
    page_icon="🔁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Wide, readable app: constrain Streamlit's default paddings a little less.
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.2rem; padding-bottom: 4rem; max-width: 1500px; }
      h1 { letter-spacing: -0.02em; }
      div[data-testid="stMetricValue"] { font-size: 1.4rem; }
      .fl-chain-step {
          background: rgba(49, 51, 63, 0.06); border-radius: 8px;
          padding: 0.4rem 0.7rem; margin: 0.15rem 0; font-size: 0.92rem;
      }
      .fl-arrow { color: #ff4b4b; font-weight: 700; text-align: center; }
      .fl-cite {
          display: inline-block; background: #1f6feb22; color: #1f6feb;
          border-radius: 6px; padding: 0 6px; margin-left: 6px; font-size: 0.8rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

PATTERN_LABELS = {
    "retry-amplification": "Retry amplification",
    "missing-idempotency": "Missing idempotency",
    "dlq-retry-storm": "DLQ retry storm",
    "new-pattern": "New pattern",
}


# ----------------------------------------------------------------- state


@dataclass
class Session:
    """Everything the UI needs across reruns, cached in st.session_state."""

    storage: Storage
    memory: MemoryLayer
    llm: LLMClient
    extraction: object | None = None
    raw_text: str = ""
    incident_id: str = ""
    matching: MatchingOutput | None = None
    stateless: RecommendationOutput | None = None
    memory_rec: RecommendationOutput | None = None
    mode: str = "both"
    walkthrough_step: int = 0


@st.cache_resource
def _deps():
    config = get_config()
    storage = Storage(config.db_path)
    memory = MemoryLayer(config)
    # Guard against a stale server process holding an older MemoryLayer
    # module in memory (missing Stage 2 methods): warn instead of crashing.
    if hasattr(memory, "seed_local_from_storage"):
        memory.seed_local_from_storage(storage)
    else:  # pragma: no cover - only on a stale reload
        st.warning(
            "Memory layer is stale (restart Streamlit to pick up Stage 2 code: "
            "Ctrl+C, then `streamlit run failureloop/ui.py`).",
            icon="⚠️",
        )
    llm = None
    if config.has_llm:
        try:
            llm = LLMClient(config)
        except Exception as exc:
            st.error(f"LLM client failed to start: {exc}")
    return config, storage, memory, llm


config, storage, memory, llm = _deps()

if "sess" not in st.session_state:
    st.session_state.sess = Session(storage=storage, memory=memory, llm=llm)
sess: Session = st.session_state.sess


def _has_llm() -> bool:
    return llm is not None


# ----------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown("## 🔁 FailureLoop")
    st.caption("Incident recurrence prevention agent")

    if not _has_llm():
        st.error("No LLM key. Set `LLM_API_KEY` (Groq) in `.env`.")

    mode_label = {
        "local-fallback": "SQLite fallback",
        "hindsight": "Hindsight Cloud",
    }.get(memory.mode, memory.mode)
    st.metric("Memory layer", mode_label)
    st.metric("Stored incidents", storage.incident_count())

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📥 Load history", use_container_width=True):
            agent = FailureLoopAgent(config)
            n = agent.load_historical()
            memory.seed_local_from_storage(storage)
            st.toast(f"Loaded {n} historical incidents", icon="✅")
            st.rerun()
    with col2:
        if st.button("🧹 Fresh DB", use_container_width=True):
            storage.close()
            Path(config.db_path).unlink(missing_ok=True)
            st.cache_resource.clear()
            st.rerun()

    st.divider()
    st.caption("**Run modes**")
    st.info(
        "**Stateless** = LLM sees only the incident.\n\n"
        "**Memory** = recall + fix evidence + reflection.",
        icon=None,
    )

    st.divider()
    st.caption("Stage 2 core intelligence · LLM "
               f"`{config.llm_model.split('/')[-1]}`")

# ----------------------------------------------------------------- tabs

tab_analyze, tab_walkthrough, tab_bank = st.tabs(
    ["🚨 Analyze incident", "🎯 Guided demo", "🗄 Memory bank"]
)

# ============================================================ helpers


def _chain_html(steps: list[str]) -> str:
    if not steps:
        return "_No failure chain extracted._"
    rows = []
    for i, step in enumerate(steps):
        if i:
            rows.append('<div class="fl-arrow">↓</div>')
        rows.append(f'<div class="fl-chain-step">{step}</div>')
    return "".join(rows)


def _cite(ids: list[str]) -> str:
    if not ids:
        return ""
    return "".join(f'<span class="fl-cite">{iid}</span>' for iid in ids)


def _render_recommendation(rec: Recommendation, out: RecommendationOutput, border: str) -> None:
    kind = "🧠 Memory-grounded" if out.mode == "memory" else "👻 Stateless (no history)"
    st.markdown(
        f"""
        <div style="border:1px solid rgba(255,75,75,.35); border-radius:10px;
                    padding:0.9rem 1.1rem; margin-bottom:0.8rem; background:rgba(255,75,75,.04);">
          <div style="font-weight:700; font-size:1.05rem;">{kind}
            <span style="float:right; font-weight:600;">confidence
              <span style="font-size:1.3rem;">{rec.confidence:.2f}</span></span>
          </div>
          <div style="margin-top:4px;"><b>{rec.likely_pattern.name}</b></div>
          <div style="opacity:.85;">{rec.likely_pattern.explanation}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Next steps**")
        for i, step in enumerate(rec.recommended_next_steps, 1):
            cite = _cite(step.supporting_incident_ids)
            if cite:
                st.markdown(f"{i}. {step.step}{cite}", unsafe_allow_html=True)
            else:
                st.markdown(
                    f"{i}. {step.step} <span class='fl-cite' "
                    f"style='background:#8882; color:inherit'>general reasoning</span>",
                    unsafe_allow_html=True,
                )
            if step.rationale:
                st.markdown(f"   <span style='opacity:.7; font-size:.88rem'>{step.rationale}</span>",
                            unsafe_allow_html=True)
    with right:
        if rec.fixes_to_avoid:
            st.markdown("**⛔ Fixes to avoid**")
            for fix in rec.fixes_to_avoid:
                st.markdown(
                    f"- ~~{fix.action}~~ — {fix.why} {_cite(fix.incident_ids)}",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No failed-fix warnings (stateless mode cannot know history).")
        if rec.matched_incidents:
            st.markdown("**Matched incidents**")
            for m in rec.matched_incidents:
                st.markdown(
                    f"- `{m.incident_id}` · {m.similarity:.2f} — {m.title}\n\n"
                    f"  <span style='opacity:.75; font-size:.85rem'>{m.why_matched}</span>"
                    + (f" <i>DIFFERS: {m.differences}</i>" if m.differences else ""),
                    unsafe_allow_html=True,
                )

    alt_col, gap_col = st.columns(2)
    with alt_col:
        if rec.alternative_hypotheses:
            with st.expander("🤔 Alternative hypotheses"):
                for h in rec.alternative_hypotheses:
                    st.markdown(f"- {h}")
    with gap_col:
        if rec.data_gaps:
            with st.expander("🕳 Data gaps"):
                for gap in rec.data_gaps:
                    st.markdown(f"- {gap}")

    st.caption(rec.safety_note)


def _render_match_cards(matching: MatchingOutput) -> None:
    if matching.cold_start:
        st.warning(matching.reason or "Cold start: empty memory bank.", icon="🧊")
        return
    if matching.reason:
        st.caption(matching.reason)
    for m in matching.matches:
        badge = "🟢 match" if m.match_type == "match" else "🟡 partial (near-miss)"
        with st.container(border=True):
            top1, top2 = st.columns([3, 1])
            with top1:
                st.markdown(f"**`{m.incident_id}`** · {badge}")
            with top2:
                st.metric("score", f"{m.total:.2f}")
            comps = m.components
            st.markdown(
                f"causal `{comps['causal_chain_similarity']:.2f}` · "
                f"dep `{comps['dependency_match']:.2f}` · "
                f"trigger `{comps['trigger_match']:.2f}` · "
                f"recency `{comps['recency']:.2f}` · "
                f"severity `{comps['severity_relevance']:.2f}`"
            )
            st.progress(min(m.total, 1.0))
            if m.judge_rationale:
                st.caption(f"Judge: {m.judge_rationale}")
            if m.difference_note and m.match_type == "partial_match":
                st.caption(f"⚠ {m.difference_note}")


def _render_fix_evidence(matching: MatchingOutput) -> None:
    ranked = rank_fixes(matching.matches)
    left, right = st.columns(2)
    with left:
        st.markdown("**✅ Proven fixes**")
        if ranked.proven_fixes:
            for fix in ranked.proven_fixes:
                st.markdown(
                    f"- {fix.action} · {fix.incident_count} incident(s) "
                    f"`{', '.join(fix.supporting_incident_ids)}`",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("None in current matches.")
    with right:
        st.markdown("**⛔ Failed before**")
        if ranked.failed_fixes:
            for fix in ranked.failed_fixes:
                st.markdown(
                    f"- ~~{fix.action}~~ `{', '.join(fix.supporting_incident_ids)}`",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("None in current matches.")
    if ranked.conflicting_fixes:
        with st.expander("⚔ Conflicting evidence (both sides shown)"):
            for fix in ranked.conflicting_fixes:
                st.markdown(
                    f"- **{fix.action}** — worked in `{', '.join(fix.worked_in)}` "
                    f"but failed in `{', '.join(fix.failed_in)}`"
                )


def _run_analysis(raw_text: str, incident_id: str, mode: str) -> None:
    if not raw_text.strip():
        st.warning("Paste some incident text first.")
        return
    if not _has_llm():
        st.error("No LLM key configured (LLM_API_KEY in .env).")
        return
    assert llm is not None
    try:
        with st.status("Analyzing incident", expanded=True) as status:
            st.write("Extracting failure fingerprint…")
            extraction = FingerprintExtractor(llm).extract(raw_text, incident_id=incident_id)
            fp = extraction.fingerprint
            st.write("Recalling similar incidents…")
            matching = (
                find_similar_incidents(fp, memory=memory, storage=storage, llm=llm)
                if mode in ("memory", "both")
                else MatchingOutput(cold_start=True, reason="Stateless mode: memory not consulted.")
            )
            st.write("Generating recommendations…")
            stateless = (
                recommend_stateless(fp, raw_text, llm, incident_id=fp.incident_id, storage=storage)
                if mode in ("stateless", "both")
                else None
            )
            memory_rec = (
                recommend_with_memory(
                    fp, raw_text, EngineDeps(llm=llm, memory=memory, storage=storage),
                    incident_id=fp.incident_id, matching=matching,
                )
                if mode in ("memory", "both")
                else None
            )
            status.update(label="Analysis complete", state="complete")
    except Exception as exc:  # rate limits, malformed LLM output, network...
        st.error(
            "Analysis failed. The LLM may be rate-limited or unreachable; check the "
            "terminal log and try again in a moment."
        )
        st.caption(f"Details: {exc}")
        return

    sess.extraction = extraction
    sess.matching = matching
    sess.stateless = stateless
    sess.memory_rec = memory_rec
    sess.raw_text = raw_text
    sess.incident_id = fp.incident_id
    sess.mode = mode


# ============================================================ tab: analyze

with tab_analyze:
    st.markdown("## 🚨 Analyze a new incident")

    dataset = load_demo_dataset()
    picker_col, _, btn_col = st.columns([4, 0.2, 1.4])
    with picker_col:
        choice = st.selectbox(
            "Load a demo incident (or write your own below)",
            options=["(custom)"] + [f"#{d.demo_number} · {d.title}" for d in dataset.incidents],
            key="demo_picker",
        )
    with btn_col:
        st.write("")
        if st.button("⚡ Quick run: analyze demo", type="primary", use_container_width=True):
            if choice != "(custom)":
                idx = [f"#{d.demo_number} · {d.title}" for d in dataset.incidents].index(choice)
                demo = dataset.incidents[idx]
                _run_analysis(demo.text, demo.incident_id, "both")

    default_text = ""
    default_id = "INC-NEW"
    if choice != "(custom)":
        idx = [f"#{d.demo_number} · {d.title}" for d in dataset.incidents].index(choice)
        demo = dataset.incidents[idx]
        default_text = demo.text
        default_id = demo.incident_id
        if st.button("↩ Reset text to this demo", type="secondary"):
            st.session_state.incident_text = default_text
            st.session_state.incident_id_value = default_id
            st.rerun()

    c1, c2 = st.columns([4, 1])
    with c1:
        raw_text = st.text_area(
            "Incident text (alert, Slack thread, log dump…)",
            height=150,
            key="incident_text",
        )
    with c2:
        incident_id = st.text_input(
            "Incident ID", value=st.session_state.get("incident_id_value", default_id),
            key="incident_id_value",
        )
        mode = st.radio("Mode", ["both", "stateless", "memory"], horizontal=True)
        run = st.button("🔎 Analyze", type="primary", use_container_width=True)

    if run:
        _run_analysis(raw_text, incident_id, mode)
        st.session_state.last_text = raw_text

    # ---------------- results
    if sess.extraction is not None:
        st.divider()
        fp = sess.extraction.fingerprint

        head_left, head_right = st.columns([3, 1])
        with head_left:
            st.markdown(f"## 🧬 Fingerprint · `{fp.incident_id}`")
        with head_right:
            st.metric("Chain steps", len(fp.failure_chain))

        f1, f2, f3 = st.columns([1.2, 1.2, 1])
        with f1:
            with st.container(border=True):
                st.markdown("**What we know**")
                st.markdown(f"- **Service:** {fp.service or '—'}")
                st.markdown(f"- **Symptom:** {fp.symptom or '—'}")
                st.markdown(f"- **Dependency:** {fp.dependency or '—'}")
                st.markdown(f"- **Trigger:** {fp.triggering_change or '—'}")
                st.markdown(f"- **Severity:** {fp.severity or '—'}")
                st.markdown(
                    f"- **Root cause:** {fp.root_cause or '_unknown (live incident)_'}"
                )
        with f2:
            with st.container(border=True):
                st.markdown("**Failure chain** (the matching signal)")
                st.markdown(_chain_html(fp.failure_chain), unsafe_allow_html=True)
        with f3:
            with st.container(border=True):
                st.markdown("**Extraction confidence**")
                for field_name, conf in sess.extraction.field_confidence.items():
                    st.metric(field_name, f"{conf:.2f}")
                if sess.extraction.extraction_notes:
                    with st.expander("Notes"):
                        for note in sess.extraction.extraction_notes:
                            st.markdown(f"- {note}")

        st.divider()
        st.markdown("## 📊 Pattern matches & evidence")
        if sess.mode in ("memory", "both") and sess.matching is not None:
            _render_match_cards(sess.matching)
            _render_fix_evidence(sess.matching)

            if fp.failure_chain:
                with st.expander("🧩 Side-by-side: new incident vs top match"):
                    if sess.matching.matches:
                        top = sess.matching.matches[0]
                        if top.fingerprint is not None:
                            left_c, right_c = st.columns(2)
                            with left_c:
                                st.markdown(f"**New incident** `{fp.incident_id}`")
                                st.markdown(_chain_html(fp.failure_chain), unsafe_allow_html=True)
                            with right_c:
                                st.markdown(f"**{top.incident_id}** (score {top.total:.2f})")
                                st.markdown(
                                    _chain_html(top.fingerprint.failure_chain),
                                    unsafe_allow_html=True,
                                )

        st.divider()
        st.markdown("## 💡 Recommendations")

        if sess.mode == "both" and sess.stateless and sess.memory_rec:
            before_col, after_col = st.columns(2, gap="medium")
            with before_col:
                st.markdown("### 👻 Before memory")
                _render_recommendation(
                    sess.stateless.recommendation, sess.stateless, "yellow"
                )
            with after_col:
                st.markdown("### 🧠 After memory")
                _render_recommendation(sess.memory_rec.recommendation, sess.memory_rec, "cyan")
            s = sess.stateless.recommendation
            m = sess.memory_rec.recommendation
            delta = m.confidence - s.confidence
            k1, k2, k3 = st.columns(3)
            k1.metric(
                "Evidence added",
                f"{len(m.matched_incidents)}",
                None if m.matched_incidents else "none",
            )
            k2.metric("Fixes to avoid", len(m.fixes_to_avoid))
            k3.metric(
                "Confidence shift",
                f"{delta:+.2f}",
                "memory knows more" if delta > 0 else ("new pattern?" if delta < 0 else ""),
            )
        else:
            out = sess.stateless or sess.memory_rec
            if out is not None:
                _render_recommendation(out.recommendation, out, "cyan")

        # ---------------- export (demo-friendly)
        if sess.stateless or sess.memory_rec:
            export = {
                "incident_id": sess.incident_id,
                "raw_text": sess.raw_text,
                "fingerprint": sess.extraction.fingerprint.model_dump(),
                "extraction_notes": sess.extraction.extraction_notes,
                "field_confidence": sess.extraction.field_confidence,
                "matches": [
                    {
                        "incident_id": m.incident_id,
                        "total": m.total,
                        "components": m.components,
                        "match_type": m.match_type,
                        "judge_rationale": m.judge_rationale,
                    }
                    for m in (sess.matching.matches if sess.matching else [])
                ],
                "recommendations": {
                    mode: out.model_dump()
                    for mode, out in (
                        ("stateless", sess.stateless),
                        ("memory", sess.memory_rec),
                    )
                    if out is not None
                },
            }
            st.download_button(
                "⬇ Export full analysis as JSON",
                data=json.dumps(export, indent=2, ensure_ascii=False),
                file_name=f"failureloop_{sess.incident_id or 'analysis'}.json",
                mime="application/json",
            )

# ============================================================ tab: walkthrough

WALKTHROUGH = [
    {
        "title": "1 · Cold start: submit an incident with NO history",
        "action": "stateless_only",
        "blurb": (
            "Empty memory bank. The LLM can only give generic on-call advice - it has "
            "no idea your team solved this exact failure three times before."
        ),
    },
    {
        "title": "2 · Load 8 historical postmortems into memory",
        "action": "load_history",
        "blurb": (
            "Postmortems are extracted into failure fingerprints and retained "
            "(SQLite now; Hindsight Cloud when configured)."
        ),
    },
    {
        "title": "3 · Same incident again, now WITH memory",
        "action": "memory_only",
        "blurb": (
            "Recall matches by causal chain (not error text), surfaces proven fixes "
            "and warns about fixes that failed before."
        ),
    },
    {
        "title": "4 · See what memory added",
        "action": "compare",
        "blurb": "Side-by-side: evidence, citations, confidence shift.",
    },
]

with tab_walkthrough:
    st.markdown("## 🎯 Guided demo: *\"What should we check?\" → \"We've seen this failure.\"*")
    demo1 = dataset.incidents[0]

    progress_cols = st.columns(len(WALKTHROUGH))
    for i, (col, step_def) in enumerate(zip(progress_cols, WALKTHROUGH), 1):
        done = sess.walkthrough_step >= i
        col.markdown(
            f"{'✅' if done else '⬜'} **Step {i}**",
            help=step_def["title"],
        )
    st.progress(sess.walkthrough_step / len(WALKTHROUGH))

    current = WALKTHROUGH[min(sess.walkthrough_step, len(WALKTHROUGH) - 1)]
    st.markdown(f"### {current['title']}")
    st.markdown(current["blurb"])

    btn_label = {
        "stateless_only": "▶ Run incident WITHOUT memory",
        "load_history": "📥 Load 8 postmortems",
        "memory_only": "▶ Run incident WITH memory",
        "compare": "📊 Show before/after comparison",
    }[current["action"]]

    if st.button(btn_label, type="primary"):
        action = current["action"]
        if action == "stateless_only":
            _run_analysis(demo1.text, demo1.incident_id, "stateless")
        elif action == "load_history":
            agent = FailureLoopAgent(config)
            agent.load_historical()
            memory.seed_local_from_storage(storage)
            st.toast("8 postmortems retained", icon="✅")
        elif action in ("memory_only", "compare"):
            _run_analysis(demo1.text, demo1.incident_id, "memory" if action == "memory_only" else "both")
        sess.walkthrough_step += 1
        st.rerun()

    if sess.walkthrough_step > 1 and st.button("↺ Restart walkthrough"):
        sess.walkthrough_step = 0
        st.rerun()

    if sess.extraction is not None and sess.walkthrough_step >= 2:
        st.divider()
        if sess.stateless and sess.memory_rec:
            s, m = sess.stateless.recommendation, sess.memory_rec.recommendation
            k1, k2, k3 = st.columns(3)
            k1.metric("Matched incidents cited", len(m.matched_incidents))
            k2.metric("Failed-fix warnings", len(m.fixes_to_avoid))
            k3.metric(
                "Confidence",
                f"{s.confidence:.2f} → {m.confidence:.2f}",
                f"{m.confidence - s.confidence:+.2f}",
            )

# ============================================================ tab: memory bank

with tab_bank:
    st.markdown("## 🗄 Memory bank")
    m1, m2, m3 = st.columns(3)
    m1.metric("Incidents stored", storage.incident_count())
    m2.metric("Memory layer", mode_label)
    m3.metric("LLM", config.llm_model.split("/")[-1])

    fps = storage.get_fingerprint_map()
    if not fps:
        st.info("Empty. Click **Load history** in the sidebar.")
    else:
        st.markdown("### Retained incidents")
        rows = []
        for iid in sorted(fps):
            fp = fps[iid]
            rows.append(
                {
                    "ID": iid,
                    "Service": fp.service,
                    "Dependency": fp.dependency,
                    "Severity": fp.severity,
                    "Root cause": (fp.root_cause or "—")[:60],
                    "Chain steps": len(fp.failure_chain),
                    "Fixes ✓": len(fp.successful_fixes),
                    "Fixes ✗": len(fp.failed_fixes),
                }
            )
        # Lightweight table (no pandas dependency needed).
        header = st.columns([1, 1.4, 1.4, 0.7, 2.2, 0.9, 0.7, 0.7])
        for col, name in zip(header, ["ID", "Service", "Dependency", "SEV", "Root cause", "Steps", "✓", "✗"]):
            col.markdown(f"**{name}**")
        for row in rows:
            cols = st.columns([1, 1.4, 1.4, 0.7, 2.2, 0.9, 0.7, 0.7])
            for col, val in zip(cols, row.values()):
                col.markdown(str(val))

        st.markdown("### Failure chains")
        pick = st.selectbox("Inspect a chain", options=sorted(fps), key="chain_pick")
        fp = fps[pick]
        st.markdown(_chain_html(fp.failure_chain), unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**✅ Successful fixes**")
            for fix in fp.successful_fixes or []:
                st.markdown(f"- {fix}")
        with c2:
            st.markdown("**⛔ Failed fixes**")
            for fix in fp.failed_fixes or []:
                st.markdown(f"- {fix}")

st.divider()
st.caption(
    "FailureLoop assists on-call engineers; it never executes production changes. "
    "Evidence cited as incident IDs; uncited claims are general reasoning."
)
