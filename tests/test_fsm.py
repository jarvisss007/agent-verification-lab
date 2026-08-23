import pytest
from fsm import FSM, IllegalTransition

TRANSITIONS = {
    ("A", "go"): "B",
    ("B", "go"): "C",
}


def test_legal_sequence_advances_state():
    fsm = FSM(state="A", transitions=TRANSITIONS)
    fsm.fire("go")
    assert fsm.state == "B"
    fsm.fire("go")
    assert fsm.state == "C"
    assert fsm.history == ["go", "go"]


def test_skipping_a_state_is_rejected():
    fsm = FSM(state="A", transitions=TRANSITIONS)
    with pytest.raises(IllegalTransition):
        fsm.fire("finish")  # no such event from A at all


def test_illegal_transition_does_not_mutate_state():
    fsm = FSM(state="C", transitions=TRANSITIONS)
    with pytest.raises(IllegalTransition):
        fsm.fire("go")  # C is terminal, no transition defined
    assert fsm.state == "C"
    assert fsm.history == []
