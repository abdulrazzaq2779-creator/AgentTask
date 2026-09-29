"""Load historical incidents into SQLite + Hindsight (needed before memory mode works).

  python scripts/load_history.py
  python scripts/load_history.py --fresh   # wipe the SQLite DB first
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from failureloop.config import get_config
from failureloop.data import HISTORICAL_INCIDENTS
from failureloop.memory import MemoryLayer
from failureloop.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser(description="Load historical incidents into memory")
    parser.add_argument("--fresh", action="store_true", help="Delete the SQLite DB first")
    args = parser.parse_args()

    config = get_config()
    if args.fresh and Path(config.db_path).exists():
        Path(config.db_path).unlink()
        print(f"Removed {config.db_path}")

    storage = Storage(config.db_path)
    memory = MemoryLayer(config)

    loaded = 0
    for incident in HISTORICAL_INCIDENTS:
        fp = incident["fingerprint"].model_copy(deep=True)
        fp.incident_id = incident["incident_id"]
        raw = incident["raw_text"]
        storage.save_incident(fp, raw, status="resolved")
        memory.retain_incident(fp, raw_text=raw)
        loaded += 1
        print(f"  loaded {fp.incident_id}")

    print(f"\nLoaded {loaded} incidents. Storage: {storage.incident_count()} | Memory mode: {memory.mode}")
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
