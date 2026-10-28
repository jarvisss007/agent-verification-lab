import pytest

from idempotency import IdempotentExecutor, OutcomeUnknown, idempotency_key


def test_same_key_only_runs_side_effect_once():
    executor = IdempotentExecutor()
    calls = []

    def side_effect():
        calls.append(1)
        return "sent"

    key = idempotency_key("send_email", {"to": "broker@example.com"})
    result1, replayed1 = executor.run(key, side_effect)
    result2, replayed2 = executor.run(key, side_effect)

    assert result1 == result2 == "sent"
    assert replayed1 is False
    assert replayed2 is True
    assert len(calls) == 1  # the actual side effect ran exactly once
    assert executor.call_count(key) == 1


def test_restored_executor_does_not_rerun_a_recorded_key():
    key = idempotency_key("write_tms", {"load_id": "4471"})
    executor = IdempotentExecutor.restore({key: "written"})
    calls = []

    result, replayed = executor.run(key, lambda: calls.append(1))

    assert (result, replayed) == ("written", True)
    assert calls == []  # the restart did not re-run the side effect


def test_different_inputs_get_different_keys():
    key_a = idempotency_key("send_email", {"to": "a@example.com"})
    key_b = idempotency_key("send_email", {"to": "b@example.com"})
    assert key_a != key_b


def test_key_is_stable_regardless_of_dict_order():
    key1 = idempotency_key("write_tms", {"load_id": "4471", "amount": 500})
    key2 = idempotency_key("write_tms", {"amount": 500, "load_id": "4471"})
    assert key1 == key2


def test_a_lost_acknowledgement_is_unknown_not_retried():
    executor = IdempotentExecutor()
    calls = []
    key = idempotency_key("send_email", {"to": "broker@example.com"})

    def sends_then_times_out():
        calls.append(1)
        raise TimeoutError("sent; the ack never came back")

    with pytest.raises(TimeoutError):
        executor.run(key, sends_then_times_out)
    with pytest.raises(OutcomeUnknown):
        executor.run(key, sends_then_times_out)
    assert calls == [1]  # the retry did not send it again


def test_a_restart_keeps_an_unfinished_intent_unknown():
    key = idempotency_key("write_tms", {"load_id": "4471"})
    executor = IdempotentExecutor.restore({}, started={key})
    calls = []

    with pytest.raises(OutcomeUnknown):
        executor.run(key, lambda: calls.append(1))
    assert calls == []


def test_the_intent_is_recorded_before_the_side_effect():
    executor = IdempotentExecutor()
    order = []
    key = idempotency_key("draft_reply", {"email_id": "e1"})

    executor.run(key, lambda: order.append("side effect"), before=lambda: order.append("intent"))
    assert order == ["intent", "side effect"]


def test_if_the_intent_cannot_be_recorded_nothing_runs_and_a_retry_is_safe():
    executor = IdempotentExecutor()
    calls = []
    key = idempotency_key("draft_reply", {"email_id": "e1"})

    def disk_full():
        raise OSError("no space left on device")

    with pytest.raises(OSError):
        executor.run(key, lambda: calls.append(1), before=disk_full)
    assert calls == []  # never started, so not unknown
    assert executor.run(key, lambda: calls.append(1) or "drafted") == ("drafted", False)
