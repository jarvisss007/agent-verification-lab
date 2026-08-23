from policy_gate import PolicyGate, RiskTier

GATE = PolicyGate(
    allow_list={
        "draft_reply": RiskTier.AUTO,
        "send_email": RiskTier.REQUIRES_APPROVAL,
        "delete_load": RiskTier.BLOCKED,
    }
)


def test_unlisted_action_defaults_to_blocked():
    decision = GATE.evaluate("wire_transfer")  # never declared anywhere
    assert decision.tier is RiskTier.BLOCKED
    assert decision.allowed_to_execute is False
    assert "default-deny" in decision.reason


def test_auto_tier_is_allowed_to_execute():
    decision = GATE.evaluate("draft_reply")
    assert decision.allowed_to_execute is True


def test_requires_approval_tier_is_not_auto_executed():
    decision = GATE.evaluate("send_email")
    assert decision.tier is RiskTier.REQUIRES_APPROVAL
    assert decision.allowed_to_execute is False


def test_explicitly_blocked_action_stays_blocked():
    decision = GATE.evaluate("delete_load")
    assert decision.tier is RiskTier.BLOCKED
    assert decision.allowed_to_execute is False
