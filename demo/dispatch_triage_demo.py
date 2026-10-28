"""End-to-end toy scenario: an inbound broker email gets triaged.

This mirrors the shape of a real freight-dispatch inbox-to-TMS pipeline
(classify the email, match it to a load, propose a reply or a TMS write)
without any real classifier, TMS, or network call — the point is the
runtime discipline around the decision, not the decision itself. A real
deployment swaps `classify()` for an LLM call and the two `execute_*`
stubs for real API calls; nothing else in this file would need to change.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from event_log import EventLog  # noqa: E402
from fsm import FSM, IllegalTransition  # noqa: E402
from idempotency import IdempotentExecutor, idempotency_key  # noqa: E402
from policy_gate import PolicyGate, RiskTier  # noqa: E402

TRANSITIONS = {
    ("RECEIVED", "classified"): "CLASSIFIED",
    ("CLASSIFIED", "action_proposed"): "PROPOSED",
    ("PROPOSED", "gated"): "GATED",
    ("GATED", "executed"): "DONE",
}

POLICY = PolicyGate(
    allow_list={
        "draft_reply": RiskTier.AUTO,  # writing a draft is reversible, safe to automate
        "match_dispatch_board": RiskTier.AUTO,  # read-only lookup
        "send_email": RiskTier.REQUIRES_APPROVAL,  # leaves the building — human confirms
        "write_tms": RiskTier.REQUIRES_APPROVAL,  # financial record — human confirms
    }
)

# rule-based stand-in for "classify inbound broker email by question type"
KEYWORDS_TO_ACTION = {
    "rate confirmation": "write_tms",
    "where is my load": "draft_reply",
    "detention": "send_email",
}


def classify(email_body: str) -> str:
    body = email_body.lower()
    for keyword, action in KEYWORDS_TO_ACTION.items():
        if keyword in body:
            return action
    return "draft_reply"  # unknown → safest default, never the risky ones


def execute_auto(action: str, payload: dict) -> dict:
    return {"status": "executed_automatically", "action": action, **payload}


def execute_with_human(action: str, payload: dict) -> dict:
    return {"status": "queued_for_human_approval", "action": action, **payload}


def triage_one_email(
    log: EventLog, executor: IdempotentExecutor, email_id: str, body: str
) -> dict:
    fsm = FSM(state="RECEIVED", transitions=TRANSITIONS)

    action = classify(body)
    fsm.fire("classified")
    log.append("classified", {"email_id": email_id, "action": action})

    fsm.fire("action_proposed")
    log.append("action_proposed", {"email_id": email_id, "action": action})

    decision = POLICY.evaluate(action)
    fsm.fire("gated")
    log.append(
        "gated",
        {"email_id": email_id, "action": action, "tier": decision.tier.value},
    )

    key = idempotency_key(action, {"email_id": email_id})
    runner = execute_auto if decision.allowed_to_execute else execute_with_human
    result, was_replayed = executor.run(key, lambda: runner(action, {"email_id": email_id}))

    fsm.fire("executed")
    log.append(
        "executed",
        {
            "email_id": email_id,
            "action": action,
            "key": key,
            "result": result,
            "replayed": was_replayed,
        },
    )
    return result


def completed_effects(state: dict, entry: dict) -> dict:
    """Every side effect the log says already ran, by idempotency key."""
    if entry["event_type"] == "executed":
        state[entry["payload"]["key"]] = entry["payload"]["result"]
    return state


def boot(log_path: Path) -> tuple[EventLog, IdempotentExecutor]:
    """What a freshly started process has: the log on disk, nothing in memory.
    The executor's record of what already ran is rebuilt from that log."""
    log = EventLog(path=log_path)
    return log, IdempotentExecutor.restore(log.replay(completed_effects, initial_state={}))


def reducer(state: dict, entry: dict) -> dict:
    """Rebuilds final-status-per-email purely from the log, no other memory."""
    payload = entry["payload"]
    email_id = payload.get("email_id")
    if entry["event_type"] == "executed":
        state[email_id] = payload["result"]["status"]
    return state


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "events.jsonl"
        log, executor = boot(log_path)

        emails = {
            "e1": "Rate confirmation attached for load 4471.",
            "e2": "Detention claim for yesterday's delivery.",
            "e3": "Where is my load, ETA please?",
        }
        for email_id, body in emails.items():
            result = triage_one_email(log, executor, email_id, body)
            print(f"{email_id}: {result['status']} ({result['action']})")

        # Simulate a retry after a crash: same email arrives again (e.g. Gmail
        # push notification fired twice). It must NOT execute twice.
        retried = triage_one_email(log, executor, "e1", emails["e1"])
        print(f"e1 retried: replayed={executor.call_count(idempotency_key('write_tms', {'email_id': 'e1'})) == 1}")

        # Simulate the process dying and coming back: rebuild state from the
        # log alone, with zero in-memory carryover.
        fresh_log, _ = boot(log_path)
        rebuilt = fresh_log.replay(reducer, initial_state={})
        print("rebuilt state from log replay:", rebuilt)
