"""Append-only `events.jsonl` audit log."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, stage: str, event: str, **data: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "stage": stage,
            "event": event,
            **data,
        }
        self.events.append(record)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return record

    def of(self, stage: str | None = None, event: str | None = None) -> list[dict[str, Any]]:
        return [
            e for e in self.events
            if (stage is None or e["stage"] == stage) and (event is None or e["event"] == event)
        ]
