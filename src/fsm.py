"""A finite state machine that refuses to run outside its declared graph.

The LLM proposes an event ("classified", "drafted", "approved"...). This guard
is the only thing that decides whether the proposed transition is legal. An
agent that tries to skip a state (e.g. jump straight from RECEIVED to SENT)
is rejected here, deterministically, regardless of what the model said.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class IllegalTransition(Exception):
    pass


@dataclass
class FSM:
    state: str
    transitions: dict[tuple[str, str], str]
    history: list[str] = field(default_factory=list)

    def fire(self, event: str) -> str:
        key = (self.state, event)
        if key not in self.transitions:
            raise IllegalTransition(
                f"no transition for event {event!r} from state {self.state!r}"
            )
        self.history.append(event)
        self.state = self.transitions[key]
        return self.state
