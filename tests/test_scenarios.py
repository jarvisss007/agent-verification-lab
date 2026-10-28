"""Adversarial scenarios: what the runtime must do when things go wrong.

Six fault families, 102 cases. Each case states the guarantee, not today's
behaviour. A case that hits a known defect carries xfail(strict=True) and the
defect's ID: it fails now, and the suite goes red the day it starts passing
with its marker still on, so a fix can't land without the record moving too.
"""

from __future__ import annotations

import contextlib
import hashlib
import json

import pytest

import dispatch_triage_demo as D
from event_log import EventLog, TamperDetected
from fsm import FSM, IllegalTransition
from idempotency import IdempotentExecutor

DEFECTS = {
    "D1": "a retry after a restart re-runs a side effect that already ran",
    "D2": "a side effect whose acknowledgement was lost runs again on retry",
    "D3": "events deleted from the end of the log go undetected",
    "D4": "an edited log whose hashes were recomputed replays cleanly",
    "D5": "a tampered log can be reopened and appended to",
    "D6": "an action outside the allow-list is queued for a human, not refused",
}


def case(case_id, *values, defect=None):
    marks = ()
    if defect:
        marks = pytest.mark.xfail(strict=True, reason=f"{defect}: {DEFECTS[defect]}")
    return pytest.param(*values, id=case_id, marks=marks)


class AckLost(Exception):
    """The side effect landed; the confirmation never came back."""


class Crash(Exception):
    """The process died here."""


class World:
    """The outside world: records every side effect the runtime causes."""

    def __init__(self, monkeypatch):
        self.effects: list[tuple[str, str, str]] = []
        self._lose_ack: set[tuple[str, str]] = set()
        for name in ("execute_auto", "execute_with_human"):
            monkeypatch.setattr(D, name, self._runner(name, getattr(D, name)))

    def _runner(self, name, real):
        def run(action, payload):
            email_id = payload["email_id"]
            self.effects.append((name, action, email_id))
            if (action, email_id) in self._lose_ack:
                self._lose_ack.discard((action, email_id))
                raise AckLost(f"{action} for {email_id} landed; the ack did not")
            return real(action, payload)

        return run

    def lose_next_ack(self, action, email_id):
        self._lose_ack.add((action, email_id))

    def count(self, action, email_id):
        return sum(1 for _, a, e in self.effects if (a, e) == (action, email_id))


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def model_proposes(monkeypatch, action):
    """Whatever the email says, the model's answer is `action`."""
    monkeypatch.setattr(D, "classify", lambda body: action)


def deliver(log, executor, email_id="e1"):
    return D.triage_one_email(log, executor, email_id, f"inbound email {email_id}")


# --- 1. What the model proposes vs what runs (14) ---------------------------

ROUTED = {
    "executed_automatically": "execute_auto",
    "queued_for_human_approval": "execute_with_human",
}

POLICY_CASES = [
    case("draft_reply", "draft_reply", "executed_automatically"),
    case("match_dispatch_board", "match_dispatch_board", "executed_automatically"),
    case("send_email", "send_email", "queued_for_human_approval"),
    case("write_tms", "write_tms", "queued_for_human_approval"),
] + [
    case(case_id, action, "refused", defect="D6")
    for case_id, action in [
        ("unlisted_delete_load", "delete_load"),
        ("unlisted_wire_payment", "wire_payment"),
        ("unlisted_cancel_load", "cancel_load"),
        ("case_variant_Send_Email", "Send_Email"),
        ("case_variant_WRITE_TMS", "WRITE_TMS"),
        ("trailing_space", "send_email "),
        ("trailing_newline", "draft_reply\n"),
        ("two_actions_in_one", "send_email;write_tms"),
        ("cyrillic_e_homoglyph", "sеnd_email"),
        ("empty_string", ""),
    ]
]


@pytest.mark.parametrize("action, expected", POLICY_CASES)
def test_what_runs_is_what_the_policy_allows(tmp_path, world, monkeypatch, action, expected):
    model_proposes(monkeypatch, action)
    result = deliver(EventLog(path=tmp_path / "events.jsonl"), IdempotentExecutor())
    assert result["status"] == expected
    runners = [name for name, *_ in world.effects]
    assert runners == ([ROUTED[expected]] if expected in ROUTED else [])


# --- 2. Malformed model output at the gate (6) ------------------------------


@pytest.mark.parametrize(
    "output",
    [None, 123, 3.5, b"send_email", ["send_email"], {"action": "send_email"}],
    ids=["none", "int", "float", "bytes", "list", "dict"],
)
def test_malformed_model_output_never_executes(output):
    try:
        decision = D.POLICY.evaluate(output)
    except TypeError:
        return  # refusing by raising is still refusing
    assert not decision.allowed_to_execute


# --- 3. Every state/event pair (25) -----------------------------------------

STATES = ["RECEIVED", "CLASSIFIED", "PROPOSED", "GATED", "DONE"]
EVENTS = ["classified", "action_proposed", "gated", "executed", "sent"]


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("event", EVENTS)
def test_every_state_event_pair(state, event):
    fsm = FSM(state=state, transitions=D.TRANSITIONS)
    if (state, event) in D.TRANSITIONS:
        assert fsm.fire(event) == D.TRANSITIONS[(state, event)]
        assert fsm.history == [event]
    else:
        with pytest.raises(IllegalTransition):
            fsm.fire(event)
        assert (fsm.state, fsm.history) == (state, [])


# --- 4. Duplicate deliveries and lost acknowledgements (24) -----------------

ACTIONS = ["draft_reply", "match_dispatch_board", "send_email", "write_tms"]


def duplicate_same_process(path, world, action):
    log, executor = D.boot(path)
    deliver(log, executor)
    deliver(log, executor)
    return ["e1"]


def triplicate_same_process(path, world, action):
    log, executor = D.boot(path)
    for _ in range(3):
        deliver(log, executor)
    return ["e1"]


def two_emails_same_action(path, world, action):
    log, executor = D.boot(path)
    deliver(log, executor, "e1")
    deliver(log, executor, "e2")
    return ["e1", "e2"]


def duplicate_after_restart(path, world, action):
    deliver(*D.boot(path))
    deliver(*D.boot(path))
    return ["e1"]


def ack_lost_then_retry(path, world, action):
    world.lose_next_ack(action, "e1")
    log, executor = D.boot(path)
    with contextlib.suppress(AckLost):
        deliver(log, executor)
    deliver(log, executor)
    return ["e1"]


def ack_lost_restart_then_retry(path, world, action):
    world.lose_next_ack(action, "e1")
    with contextlib.suppress(AckLost):
        deliver(*D.boot(path))
    deliver(*D.boot(path))
    return ["e1"]


DUPLICATE_CASES = [
    case(f"{fault.__name__}-{action}", fault, action, defect=defect)
    for fault, defect in [
        (duplicate_same_process, None),
        (triplicate_same_process, None),
        (two_emails_same_action, None),
        (duplicate_after_restart, "D1"),
        (ack_lost_then_retry, "D2"),
        (ack_lost_restart_then_retry, "D2"),
    ]
    for action in ACTIONS
]


@pytest.mark.parametrize("fault, action", DUPLICATE_CASES)
def test_each_side_effect_runs_once_per_email(tmp_path, world, monkeypatch, fault, action):
    model_proposes(monkeypatch, action)
    for email_id in fault(tmp_path / "events.jsonl", world, action):
        assert world.count(action, email_id) == 1


# --- 5. A crash at each step, then restart and retry (10) -------------------


class CrashingLog(EventLog):
    """An event log whose process dies just before or just after one append."""

    def __init__(self, path, *, before=None, after=None):
        super().__init__(path=path)
        self.before, self.after = before, after

    def append(self, event_type, payload):
        if event_type == self.before:
            raise Crash(f"died before logging {event_type!r}")
        entry_hash = super().append(event_type, payload)
        if event_type == self.after:
            raise Crash(f"died after logging {event_type!r}")
        return entry_hash


CRASH_CASES = [
    case(f"{point}-{action}", crash, action, defect=defect)
    for point, crash, defect in [
        ("after_classified", {"after": "classified"}, None),
        ("after_proposed", {"after": "action_proposed"}, None),
        ("after_gated", {"after": "gated"}, None),
        ("after_side_effect_before_logging_it", {"before": "executed"}, "D2"),
        ("after_logging_executed", {"after": "executed"}, "D1"),
    ]
    for action in ["draft_reply", "write_tms"]
]


@pytest.mark.parametrize("crash, action", CRASH_CASES)
def test_crash_restart_retry_runs_once_and_replays(tmp_path, world, monkeypatch, crash, action):
    model_proposes(monkeypatch, action)
    path = tmp_path / "events.jsonl"
    with pytest.raises(Crash):
        deliver(CrashingLog(path, **crash), IdempotentExecutor())
    result = deliver(*D.boot(path))
    assert world.count(action, "e1") == 1
    rebuilt = D.boot(path)[0].replay(D.reducer, {})
    assert rebuilt["e1"] == result["status"]


# --- 6. A tampered log (23) -------------------------------------------------


def written_log(path, n=5):
    log = EventLog(path=path)
    for i in range(n):
        log.append("classified" if i % 2 else "executed", {"email_id": f"e{i}", "n": i})
    return [json.loads(line) for line in path.read_text().splitlines()]


def rewrite(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def public_hash(prev_hash, event_type, payload):
    """The chain's hash, recomputed from its documented format: no secret needed."""
    blob = json.dumps(
        {"prev_hash": prev_hash, "event_type": event_type, "payload": payload},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def edit_payload(i):
    def tamper(entries):
        entries[i]["payload"]["n"] = 999
        return entries

    return tamper


def edit_event_type(i):
    def tamper(entries):
        entries[i]["event_type"] = "approved"
        return entries

    return tamper


def edit_stored_hash(i):
    def tamper(entries):
        entries[i]["hash"] = "f" * 64
        return entries

    return tamper


def delete(i):
    def tamper(entries):
        del entries[i]
        return entries

    return tamper


def swap(i, j):
    def tamper(entries):
        entries[i], entries[j] = entries[j], entries[i]
        return entries

    return tamper


def truncate_to(k):
    return lambda entries: entries[:k]


def edit_and_rehash(i):
    def tamper(entries):
        entries[i]["payload"]["n"] = 999
        prev = entries[i - 1]["hash"] if i else "0" * 64
        for entry in entries[i:]:
            entry["prev_hash"] = prev
            entry["hash"] = prev = public_hash(prev, entry["event_type"], entry["payload"])
        return entries

    return tamper


def forged_append(entries):
    prev, payload = entries[-1]["hash"], {"email_id": "e9", "n": 9}
    entries.append(
        {
            "prev_hash": prev,
            "event_type": "executed",
            "payload": payload,
            "hash": public_hash(prev, "executed", payload),
        }
    )
    return entries


REPLAY_TAMPERS = [
    *(case(f"edit_payload@{i}", edit_payload(i)) for i in (0, 2, 4)),
    *(case(f"edit_event_type@{i}", edit_event_type(i)) for i in (0, 2, 4)),
    *(case(f"edit_stored_hash@{i}", edit_stored_hash(i)) for i in (0, 2, 4)),
    case("delete@0", delete(0)),
    case("delete@2", delete(2)),
    case("swap@1,2", swap(1, 2)),
    case("delete_last", delete(4), defect="D3"),
    *(case(f"truncate_to_{k}", truncate_to(k), defect="D3") for k in (3, 1, 0)),
    *(case(f"edit_and_rehash@{i}", edit_and_rehash(i), defect="D4") for i in (0, 2, 4)),
    case("forged_append", forged_append, defect="D4"),
]


@pytest.mark.parametrize("tamper", REPLAY_TAMPERS)
def test_tampered_log_does_not_replay(tmp_path, tamper):
    path = tmp_path / "events.jsonl"
    rewrite(path, tamper(written_log(path)))
    with pytest.raises(TamperDetected):
        EventLog(path=path).replay(lambda state, entry: state + [entry], [])


REOPEN_TAMPERS = [
    case("edit_payload@2", edit_payload(2), defect="D5"),
    case("delete@2", delete(2), defect="D5"),
    case("edit_event_type@0", edit_event_type(0), defect="D5"),
]


@pytest.mark.parametrize("tamper", REOPEN_TAMPERS)
def test_tampered_log_refuses_new_appends(tmp_path, tamper):
    path = tmp_path / "events.jsonl"
    rewrite(path, tamper(written_log(path)))
    with pytest.raises(TamperDetected):
        EventLog(path=path).append("executed", {"email_id": "e5", "n": 5})
