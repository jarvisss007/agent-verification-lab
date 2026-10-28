"""Retry-safe execution: the same side effect never runs twice.

Every automated system that retries on failure (network blip, rate limit,
process restart) will eventually retry a call whose side effect already
landed. Without this, a retried "send message" or "write to TMS" duplicates
the action. The fix is boring on purpose: hash the action's inputs into a
key, and refuse to re-run a key that already has a recorded result.

A recorded result is not enough on its own. If the side effect lands and the
process loses the acknowledgement (a timeout, a crash before the result is
written), there is no result to replay, and a plain retry would run it again.
So each run is an intent/done pair: the intent is recorded BEFORE the side
effect starts, the result after it finishes. A key with an intent and no
result has an unknown outcome. It is never run again automatically; the
executor raises OutcomeUnknown and the caller hands it to a person to check.
At most once, never twice: a crash between the intent and the side effect
also reads as unknown, and a person checks an effect that never ran.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable


def idempotency_key(action: str, payload: dict) -> str:
    blob = json.dumps({"action": action, "payload": payload}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class OutcomeUnknown(Exception):
    """The side effect for this key started and never reported back."""

    def __init__(self, key: str):
        super().__init__(f"{key}: the side effect started and never reported a result")
        self.key = key


@dataclass
class IdempotentExecutor:
    _results: dict[str, Any] = field(default_factory=dict)
    _call_counts: dict[str, int] = field(default_factory=dict)
    _started: set[str] = field(default_factory=set)

    @classmethod
    def restore(cls, results: dict[str, Any], started: set[str] = frozenset()) -> IdempotentExecutor:
        """After a restart, rebuild from what was recorded durably elsewhere
        (the event log, in the demo): the results, and the keys whose intent
        was recorded. Memory alone dies with the process."""
        return cls(_results=dict(results), _started=set(started) - set(results))

    def run(
        self, key: str, fn: Callable[[], Any], before: Callable[[], Any] | None = None
    ) -> tuple[Any, bool]:
        """Returns (result, was_replayed). was_replayed=True means fn did NOT run.
        `before` records the intent durably; it runs before fn, and if it
        fails, fn never starts. Raises OutcomeUnknown for a key that started
        and never finished: fn is not called again."""
        if key in self._results:
            return self._results[key], True
        if key in self._started:
            raise OutcomeUnknown(key)
        if before is not None:
            before()
        self._started.add(key)
        result = fn()
        self._call_counts[key] = self._call_counts.get(key, 0) + 1
        self._results[key] = result
        self._started.discard(key)
        return result, False

    def call_count(self, key: str) -> int:
        return self._call_counts.get(key, 0)
