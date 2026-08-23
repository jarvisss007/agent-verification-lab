from pathlib import Path

from dispatch_triage_demo import (
    classify,
    reducer,
    triage_one_email,
)
from event_log import EventLog
from idempotency import IdempotentExecutor, idempotency_key


def test_classification_routes_to_expected_risk_tiers():
    assert classify("Rate confirmation attached for load 4471.") == "write_tms"
    assert classify("Detention claim for yesterday's delivery.") == "send_email"
    assert classify("Where is my load, ETA please?") == "draft_reply"
    assert classify("totally unrecognized gibberish") == "draft_reply"  # safe default


def test_low_risk_action_executes_automatically(tmp_path):
    log = EventLog(path=tmp_path / "events.jsonl")
    executor = IdempotentExecutor()
    result = triage_one_email(log, executor, "e1", "Where is my load?")
    assert result["status"] == "executed_automatically"


def test_high_risk_action_is_queued_not_executed(tmp_path):
    log = EventLog(path=tmp_path / "events.jsonl")
    executor = IdempotentExecutor()
    result = triage_one_email(log, executor, "e2", "Rate confirmation for load 99.")
    assert result["status"] == "queued_for_human_approval"


def test_retried_email_does_not_double_execute(tmp_path):
    log = EventLog(path=tmp_path / "events.jsonl")
    executor = IdempotentExecutor()
    body = "Rate confirmation for load 99."

    triage_one_email(log, executor, "e2", body)
    triage_one_email(log, executor, "e2", body)  # simulated duplicate delivery

    key = idempotency_key("write_tms", {"email_id": "e2"})
    assert executor.call_count(key) == 1


def test_state_survives_a_simulated_crash_via_replay(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path=path)
    executor = IdempotentExecutor()
    triage_one_email(log, executor, "e1", "Where is my load?")
    triage_one_email(log, executor, "e2", "Detention claim.")

    # "crash": drop every in-memory object, reopen only the log file
    fresh_log = EventLog(path=path)
    rebuilt = fresh_log.replay(reducer, initial_state={})

    assert rebuilt["e1"] == "executed_automatically"
    assert rebuilt["e2"] == "queued_for_human_approval"
