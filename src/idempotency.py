"""Retry-safe execution: the same side effect never runs twice.

Every automated system that retries on failure (network blip, rate limit,
process restart) will eventually retry a call whose side effect already
landed. Without this, a retried "send message" or "write to TMS" duplicates
the action. The fix is boring on purpose: hash the action's inputs into a
key, and refuse to re-run a key that already has a recorded result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable


def idempotency_key(action: str, payload: dict) -> str:
    blob = json.dumps({"action": action, "payload": payload}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class IdempotentExecutor:
    _results: dict[str, Any] = field(default_factory=dict)
    _call_counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def restore(cls, results: dict[str, Any]) -> IdempotentExecutor:
        """After a restart, rebuild from results recorded durably elsewhere
        (the event log, in the demo). Memory alone dies with the process."""
        return cls(_results=dict(results))

    def run(self, key: str, fn: Callable[[], Any]) -> tuple[Any, bool]:
        """Returns (result, was_replayed). was_replayed=True means fn did NOT run."""
        if key in self._results:
            return self._results[key], True
        result = fn()
        self._call_counts[key] = self._call_counts.get(key, 0) + 1
        self._results[key] = result
        return result, False

    def call_count(self, key: str) -> int:
        return self._call_counts.get(key, 0)
