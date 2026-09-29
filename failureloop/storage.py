"""SQLite storage for incidents, recommendations, and feedback events."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from failureloop.fingerprint import FailureFingerprint, FingerprintMatch, Recommendation
from failureloop.matcher import _token_overlap

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT UNIQUE NOT NULL,
    raw_text TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    severity TEXT,
    service TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    recommendation_json TEXT NOT NULL,
    confidence TEXT,
    mode TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    recommendation_id INTEGER,
    outcome TEXT NOT NULL,
    feedback_text TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents(service);
CREATE INDEX IF NOT EXISTS idx_feedback_incident ON feedback(incident_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # Streamlit runs each script run/session in its own thread, so the
        # connection must be usable across threads. A lock serializes access.
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            # Stage 2: additive columns for recommendation payloads.
            self._ensure_column("recommendations", "raw_text", "TEXT DEFAULT ''")
            self._ensure_column("recommendations", "payload_json", "TEXT DEFAULT ''")
            self.conn.commit()

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        """Additively add a column if it does not exist (SQLite)."""
        cols = [r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ------------------------------------------------------------- incidents

    def save_incident(self, fingerprint: FailureFingerprint, raw_text: str, status: str = "open") -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO incidents (incident_id, raw_text, fingerprint_json, severity, service, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(incident_id) DO UPDATE SET
                       raw_text=excluded.raw_text,
                       fingerprint_json=excluded.fingerprint_json,
                       severity=excluded.severity,
                       service=excluded.service,
                       status=excluded.status""",
                (
                    fingerprint.incident_id,
                    raw_text,
                    fingerprint.model_dump_json(),
                    fingerprint.severity,
                    fingerprint.service,
                    status,
                    _now(),
                ),
            )
            self.conn.commit()

    def load_incident(self, incident_id: str) -> tuple[FailureFingerprint, str] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        if row is None:
            return None
        fp = FailureFingerprint.model_validate_json(row["fingerprint_json"])
        return fp, row["raw_text"]

    def all_fingerprints(self) -> list[FailureFingerprint]:
        with self._lock:
            rows = self.conn.execute("SELECT fingerprint_json FROM incidents").fetchall()
        return [FailureFingerprint.model_validate_json(r["fingerprint_json"]) for r in rows]

    def get_fingerprint_map(self) -> dict[str, FailureFingerprint]:
        """Map of incident_id -> fingerprint for all stored incidents."""
        with self._lock:
            rows = self.conn.execute("SELECT incident_id, fingerprint_json FROM incidents").fetchall()
        result: dict[str, FailureFingerprint] = {}
        for row in rows:
            try:
                result[row["incident_id"]] = FailureFingerprint.model_validate_json(
                    row["fingerprint_json"]
                )
            except Exception:
                continue
        return result

    def get_incident_created_map(self) -> dict[str, str]:
        """Map of incident_id -> created_at ISO string (for recency scoring)."""
        with self._lock:
            rows = self.conn.execute("SELECT incident_id, created_at FROM incidents").fetchall()
        return {row["incident_id"]: row["created_at"] for row in rows}

    def incident_count(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"]

    def next_incident_number(self) -> int:
        """Next free numeric suffix for auto-assigned incident IDs."""
        with self._lock:
            count = self.conn.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"]
        return 200 + count + 1

    # ------------------------------------------------------- recommendations

    def save_recommendation(
        self,
        incident_id: str,
        rec: Recommendation,
        mode: str,
        raw_text: str = "",
        payload_json: str = "",
    ) -> int:
        with self._lock:
            cursor = self.conn.execute(
                """INSERT INTO recommendations
                       (incident_id, recommendation_json, confidence, mode, created_at,
                        raw_text, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident_id,
                    rec.model_dump_json(),
                    rec.confidence,
                    mode,
                    _now(),
                    raw_text,
                    payload_json or rec.model_dump_json(),
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def save_recommendation_payload(
        self,
        incident_id: str,
        mode: str,
        confidence: float,
        raw_text: str,
        payload_json: str,
    ) -> int:
        """Stage 2: persist a recommendation payload (new schema) without
        needing the Stage 1 Recommendation model."""
        with self._lock:
            cursor = self.conn.execute(
                """INSERT INTO recommendations
                       (incident_id, recommendation_json, confidence, mode, created_at,
                        raw_text, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (incident_id, payload_json, str(confidence), mode, _now(), raw_text, payload_json),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    # -------------------------------------------------------------- feedback

    def save_feedback(
        self, incident_id: str, recommendation_id: int | None, outcome: str, feedback_text: str
    ) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO feedback (incident_id, recommendation_id, outcome, feedback_text, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (incident_id, recommendation_id, outcome, feedback_text, _now()),
            )
            self.conn.commit()

    def feedback_outcome_quality(self) -> dict[str, float]:
        """Map incident_id -> outcome quality in [0, 1] for match boosting.

        Direct feedback boosts the incident it was recorded on; the boost also
        propagates (at half strength) to stored incidents that share a
        successful fix, i.e. the same causal chain.
        """
        with self._lock:
            rows = self.conn.execute(
                """SELECT incident_id, outcome,
                          COUNT(*) AS n,
                          SUM(CASE WHEN outcome = 'worked' THEN 1 ELSE 0 END) AS good
                   FROM feedback GROUP BY incident_id, outcome"""
            ).fetchall()
        quality: dict[str, float] = {}
        for row in rows:
            base = {
                "worked": 1.0,
                "partially_worked": 0.5,
                "failed": 0.0,
                "root_cause_different": 0.0,
                "not_verified": 0.25,
            }.get(row["outcome"], 0.25)
            # More confirmations of the same outcome strengthen the signal.
            quality[row["incident_id"]] = base * min(1.0 + 0.1 * (row["n"] - 1), 1.2)
        if not quality:
            return quality

        # Propagate proven-fix credit to incidents sharing the same fix.
        all_fps = {fp.incident_id: fp for fp in self.all_fingerprints()}
        for boosted_id, q in list(quality.items()):
            source = all_fps.get(boosted_id)
            if source is None or q <= 0.0:
                continue
            for inc_id, fp in all_fps.items():
                if inc_id == boosted_id or inc_id in quality:
                    continue
                if _shares_successful_fix(source, fp):
                    quality[inc_id] = max(quality.get(inc_id, 0.0), 0.5 * q)
        return quality

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def _shares_successful_fix(a: FailureFingerprint, b: FailureFingerprint, threshold: float = 0.6) -> bool:
    """True if any successful fix of `a` token-overlaps a successful fix of `b`."""
    for fix_a in a.successful_fixes:
        for fix_b in b.successful_fixes:
            if _token_overlap(fix_a, fix_b) >= threshold:
                return True
    return False
