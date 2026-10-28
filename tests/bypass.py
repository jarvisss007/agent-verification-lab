"""Pass-through stand-ins for the four guardrails: the "off" side of the baseline.

Test-only. Nothing under src/ imports this. tests/conftest.py applies it when
AVL_BYPASS is set, and leaves every guardrail in place when it is not:

    AVL_BYPASS=all                   every guardrail off
    AVL_BYPASS=policy,idempotency    just those two
"""

from __future__ import annotations

import os

from event_log import EventLog
from fsm import FSM
from idempotency import IdempotentExecutor
from policy_gate import Decision, PolicyGate, RiskTier

GUARDRAILS = ("fsm", "idempotency", "policy", "log")


def fire_anything(self, event):
    """FSM off: whatever event the agent reports, the state follows it."""
    self.history.append(event)
    self.state = self.transitions.get((self.state, event), str(event).upper())
    return self.state


def run_every_time(self, key, fn):
    """Idempotency off: every call runs the side effect again."""
    return fn(), False


def allow_everything(self, action):
    """Policy off: every proposed action executes with no human."""
    return Decision(action=action, tier=RiskTier.AUTO, reason="guardrail bypassed")


def replay_unchecked(self, reducer, initial_state):
    """Log off: fold every entry without checking the hash chain."""
    state = initial_state
    for entry in self._read_all():
        state = reducer(state, entry)
    return state


STAND_INS = {
    "fsm": (FSM, "fire", fire_anything),
    "idempotency": (IdempotentExecutor, "run", run_every_time),
    "policy": (PolicyGate, "evaluate", allow_everything),
    "log": (EventLog, "replay", replay_unchecked),
}


def parse(value: str) -> set[str]:
    names = {name.strip() for name in value.split(",") if name.strip()}
    if "all" in names:
        return set(GUARDRAILS)
    unknown = names - set(GUARDRAILS)
    if unknown:
        raise ValueError(
            f"AVL_BYPASS: unknown guardrail(s) {sorted(unknown)}; "
            f"choose from {', '.join(GUARDRAILS)} or 'all'"
        )
    return names


def apply_from_env() -> set[str]:
    names = parse(os.environ.get("AVL_BYPASS", ""))
    for name in names:
        cls, method, stand_in = STAND_INS[name]
        setattr(cls, method, stand_in)
    return names
