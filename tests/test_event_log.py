import json

import pytest
from event_log import EventLog, TamperDetected


def counting_reducer(state, entry):
    return state + entry["payload"].get("amount", 0)


def test_replay_reconstructs_state_with_no_other_memory(tmp_path):
    log = EventLog(path=tmp_path / "events.jsonl")
    log.append("credit", {"amount": 10})
    log.append("credit", {"amount": 5})

    # simulate the process dying: open a brand new EventLog over the same file
    fresh = EventLog(path=tmp_path / "events.jsonl")
    total = fresh.replay(counting_reducer, initial_state=0)
    assert total == 15


def test_reopening_an_existing_log_continues_the_hash_chain(tmp_path):
    path = tmp_path / "events.jsonl"
    log1 = EventLog(path=path)
    log1.append("credit", {"amount": 10})

    log2 = EventLog(path=path)  # "restart"
    log2.append("credit", {"amount": 5})

    total = EventLog(path=path).replay(counting_reducer, initial_state=0)
    assert total == 15


def test_tampering_with_a_past_entry_is_detected(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path=path)
    log.append("credit", {"amount": 10})
    log.append("credit", {"amount": 5})

    # tamper: rewrite the first entry's payload without recomputing hashes
    lines = path.read_text().splitlines()
    first = json.loads(lines[0])
    first["payload"]["amount"] = 999
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n")

    tampered_log = EventLog(path=path)
    with pytest.raises(TamperDetected):
        tampered_log.replay(counting_reducer, initial_state=0)
