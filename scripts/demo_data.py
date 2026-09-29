"""Demo data loader for CLI scripts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEMO_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "demo_incidents.json"


@dataclass
class DemoIncident:
    demo_number: int
    incident_id: str
    title: str
    text: str
    expected_pattern: str = ""
    expected_successful_fix: str = ""
    expected_avoid_fix: str = ""


@dataclass
class DemoDataset:
    incidents: list[DemoIncident]

    def by_number(self, number: int) -> DemoIncident:
        for incident in self.incidents:
            if incident.demo_number == number:
                return incident
        raise KeyError(f"No demo incident #{number}")


def load_demo_dataset() -> DemoDataset:
    raw = json.loads(DEMO_DATA_PATH.read_text(encoding="utf-8"))
    incidents = [DemoIncident(**item) for item in raw["demo_incidents"]]
    return DemoDataset(incidents=incidents)
