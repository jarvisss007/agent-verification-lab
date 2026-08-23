from idempotency import IdempotentExecutor, idempotency_key


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


def test_different_inputs_get_different_keys():
    key_a = idempotency_key("send_email", {"to": "a@example.com"})
    key_b = idempotency_key("send_email", {"to": "b@example.com"})
    assert key_a != key_b


def test_key_is_stable_regardless_of_dict_order():
    key1 = idempotency_key("write_tms", {"load_id": "4471", "amount": 500})
    key2 = idempotency_key("write_tms", {"amount": 500, "load_id": "4471"})
    assert key1 == key2
