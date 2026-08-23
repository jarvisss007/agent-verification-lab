"""Append-only, hash-chained event log with deterministic replay.

Two guarantees this buys:
1. Crash recovery — if the process dies mid-task, replaying the log from
   scratch reconstructs the exact same final state. Nothing is "remembered"
   anywhere except this log.
2. Tamper evidence — each entry commits to a hash of the previous entry, so a
   silently edited or deleted past event breaks the chain on replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


GENESIS_HASH = "0" * 64


def _hash_entry(prev_hash: str, event_type: str, payload: dict) -> str:
    blob = json.dumps(
        {"prev_hash": prev_hash, "event_type": event_type, "payload": payload},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


class TamperDetected(Exception):
    pass


@dataclass
class EventLog:
    path: Path
    _last_hash: str = field(default=GENESIS_HASH)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            for entry in self._read_all():
                self._last_hash = entry["hash"]

    def append(self, event_type: str, payload: dict) -> str:
        entry_hash = _hash_entry(self._last_hash, event_type, payload)
        entry = {
            "prev_hash": self._last_hash,
            "event_type": event_type,
            "payload": payload,
            "hash": entry_hash,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        self._last_hash = entry_hash
        return entry_hash

    def _read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open() as f:
            return [json.loads(line) for line in f if line.strip()]

    def replay(self, reducer: Callable[[Any, dict], Any], initial_state: Any) -> Any:
        """Rebuild state by folding every event through `reducer`, verifying
        the hash chain as it goes. Raises TamperDetected on any break."""
        state = initial_state
        prev_hash = GENESIS_HASH
        for entry in self._read_all():
            expected = _hash_entry(prev_hash, entry["event_type"], entry["payload"])
            if expected != entry["hash"] or entry["prev_hash"] != prev_hash:
                raise TamperDetected(
                    f"hash chain broken at event {entry['event_type']!r}"
                )
            state = reducer(state, entry)
            prev_hash = entry["hash"]
        return state
