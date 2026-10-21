"""Human-in-the-loop gates: requirement sign-off, requirement updates, merge and deploy approval."""

from __future__ import annotations

from collections.abc import Callable

from .config import Config
from .state import Requirement


class HITL:
    def __init__(self, cfg: Config, input_fn: Callable[[str], str] = input):
        self.mode = cfg.get("hitl.mode", "auto")
        self.auto_approve = set(cfg.get("hitl.auto_approve", []))
        self._input = input_fn

    def approve(self, gate: str, details: str = "") -> bool:
        if self.mode == "interactive":
            answer = self._input(f"[HITL] Approve {gate}? {details}\n[y/N] > ")
            return answer.strip().lower() in ("y", "yes")
        return gate.split(":")[0] in self.auto_approve

    def ask(self, prompt: str) -> str | None:
        if self.mode != "interactive":
            return None
        answer = self._input(f"[HITL] {prompt}\n> ").strip()
        return answer or None

    def requirement_update(self, req: Requirement, reason: str) -> Requirement:
        """Record a human clarification on a requirement flagged as incorrect by triage."""
        note = self.ask(f"{req.id} looks incorrect ({reason}). Enter a clarification:")
        req.notes.append(note or f"Flagged by failure triage: {reason}")
        return req
