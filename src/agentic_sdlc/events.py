"""Append-only `events.jsonl` audit log."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class EventLog:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, stage: str, event: str, **data: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event_id": uuid4().hex,
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


def summarize(path: Path) -> dict:
    """Read progress and observed timings without synthesizing missing measurements."""
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        raise ValueError("Event log is empty")
    run_id = records[0]["run_id"]
    stages = {}
    for record in records:
        if record["run_id"] != run_id:
            raise ValueError("Event log contains more than one run")
        if record["event"] in {"enter", "exit"}:
            key = (record["stage"], record.get("requirement"))
            summary = stages.setdefault(key, {
                "stage": key[0], "requirement": key[1], "attempts": 0,
                "timed_attempts": 0, "duration_ms": None, "status": None,
            })
            if record["event"] == "enter":
                summary["attempts"] += 1
                summary["status"] = "STARTED"
            else:
                summary["status"] = record.get("status")
                duration = record.get("duration_ms")
                if duration is not None:
                    summary["duration_ms"] = (summary["duration_ms"] or 0) + duration
                    summary["timed_attempts"] += 1
    end = next((record for record in reversed(records) if record["event"] == "finish"), None)
    return {
        "run_id": run_id,
        "outcome": end["stage"] if end else "NO_TERMINAL_EVENT",
        "stages": list(stages.values()),
    }
