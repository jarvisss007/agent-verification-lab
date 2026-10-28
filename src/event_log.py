"""Append-only, hash-chained event log with deterministic replay.

Two guarantees this buys:
1. Crash recovery — if the process dies mid-task, replaying the log from
   scratch reconstructs the exact same final state. Nothing is "remembered"
   anywhere except this log.
2. Tamper evidence — each entry commits to a hash of the previous entry, so a
   silently edited or deleted past event breaks the chain on replay.

A chain cannot see its own end: delete the newest entries and every entry
left still points correctly at the one before it. So the log keeps a head
file beside it (`events.jsonl.head`) holding how many entries it has ever
held, updated after each append. A log shorter than its head has lost its
tail, and replay refuses it. The head only ever grows: reopening a cut log
does not reset it, and a log with entries but no head is refused too.
"""

from __future__ import annotations

import hashlib
import json
import os
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
    _count: int = field(default=0)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entries = self._read_all()
        for entry in entries:
            self._last_hash = entry["hash"]
        self._count = max(len(entries), self._head_count() or 0)

    @property
    def head_path(self) -> Path:
        return self.path.with_name(self.path.name + ".head")

    def _head_count(self) -> int | None:
        """How many entries the log has ever held, or None if it has no head."""
        try:
            return int(json.loads(self.head_path.read_text())["count"])
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError) as e:
            raise TamperDetected(f"the log's head file is unreadable: {e}") from None

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
        self._count += 1
        # entry first, head second: a crash between them leaves the log one
        # longer than its head, which is harmless; the other order would not be
        tmp = self.head_path.with_name(self.head_path.name + ".tmp")
        tmp.write_text(json.dumps({"count": self._count}) + "\n")
        os.replace(tmp, self.head_path)
        return entry_hash

    def _read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open() as f:
            return [json.loads(line) for line in f if line.strip()]

    def replay(self, reducer: Callable[[Any, dict], Any], initial_state: Any) -> Any:
        """Rebuild state by folding every event through `reducer`, verifying
        the hash chain as it goes and the log's length against its head.
        Raises TamperDetected on any break."""
        state = initial_state
        prev_hash = GENESIS_HASH
        entries = self._read_all()
        head = self._head_count()
        if head is None and entries:
            raise TamperDetected("the log has entries but no head file, so its end cannot be checked")
        if head is not None and len(entries) < head:
            raise TamperDetected(
                f"the log holds {len(entries)} events but its head counts {head}: "
                "events were removed from the end"
            )
        for entry in entries:
            expected = _hash_entry(prev_hash, entry["event_type"], entry["payload"])
            if expected != entry["hash"] or entry["prev_hash"] != prev_hash:
                raise TamperDetected(
                    f"hash chain broken at event {entry['event_type']!r}"
                )
            state = reducer(state, entry)
            prev_hash = entry["hash"]
        return state
