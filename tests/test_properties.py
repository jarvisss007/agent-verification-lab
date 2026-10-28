"""Properties that must hold for every generated input, not only the ones written down."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

import dispatch_triage_demo as D
from event_log import EventLog, TamperDetected
from fsm import FSM, IllegalTransition
from idempotency import IdempotentExecutor, idempotency_key

EVENTS = st.sampled_from(["classified", "action_proposed", "gated", "executed", "sent"])
PAYLOADS = st.dictionaries(
    st.text(min_size=1, max_size=5), st.integers() | st.text(max_size=5), max_size=3
)


@given(st.lists(EVENTS, max_size=12))
def test_fsm_never_leaves_its_graph_and_illegal_events_change_nothing(events):
    fsm = FSM(state="RECEIVED", transitions=D.TRANSITIONS)
    states = {"RECEIVED", *D.TRANSITIONS.values()}
    for event in events:
        before = (fsm.state, list(fsm.history))
        try:
            fsm.fire(event)
        except IllegalTransition:
            assert (fsm.state, fsm.history) == before
        assert fsm.state in states


@settings(deadline=None)
@given(st.lists(st.tuples(EVENTS, PAYLOADS), max_size=10))
def test_replay_from_disk_returns_exactly_what_was_appended(entries):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        log = EventLog(path=path)
        for event_type, payload in entries:
            log.append(event_type, payload)
        replayed = EventLog(path=path).replay(
            lambda state, e: state + [(e["event_type"], e["payload"])], []
        )
        assert replayed == entries


@settings(deadline=None)
@given(st.lists(PAYLOADS, min_size=1, max_size=8), st.data())
def test_any_edited_payload_is_detected_on_replay(payloads, data):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        log = EventLog(path=path)
        for payload in payloads:
            log.append("executed", payload)
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        i = data.draw(st.integers(0, len(entries) - 1))
        original = entries[i]["payload"]
        entries[i]["payload"] = data.draw(PAYLOADS.filter(lambda p: p != original))
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        with pytest.raises(TamperDetected):
            EventLog(path=path).replay(lambda state, e: state, None)


@given(
    st.lists(st.sampled_from(["e1", "e2", "e3"]), max_size=12),
    st.sampled_from(["draft_reply", "match_dispatch_board", "send_email", "write_tms"]),
)
def test_duplicate_deliveries_in_one_process_run_each_effect_once(email_ids, action):
    executor = IdempotentExecutor()
    runs = []
    for email_id in email_ids:
        key = idempotency_key(action, {"email_id": email_id})
        executor.run(key, lambda e=email_id: runs.append(e))
    assert sorted(runs) == sorted(set(email_ids))
