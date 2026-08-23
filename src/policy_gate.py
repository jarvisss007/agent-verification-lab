"""Default-deny policy gate: an action runs only if explicitly allowed.

This is the "no auto-send scope anywhere in the code" rule made structural
instead of a habit. New action types are unreachable until someone deliberately
adds them to the allow-list below with a stated risk tier — there is no
implicit path from "the model proposed it" to "it executed."
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskTier(Enum):
    AUTO = "auto"  # safe to execute without a human in the loop
    REQUIRES_APPROVAL = "requires_approval"  # drafted, but a human must confirm
    BLOCKED = "blocked"  # not permitted at all in this deployment


@dataclass
class Decision:
    action: str
    tier: RiskTier
    reason: str

    @property
    def allowed_to_execute(self) -> bool:
        return self.tier == RiskTier.AUTO


@dataclass
class PolicyGate:
    allow_list: dict[str, RiskTier]

    def evaluate(self, action: str) -> Decision:
        tier = self.allow_list.get(action, RiskTier.BLOCKED)
        if action not in self.allow_list:
            reason = f"{action!r} is not in the allow-list — default-deny applies"
        elif tier is RiskTier.AUTO:
            reason = f"{action!r} is explicitly whitelisted as low-risk"
        elif tier is RiskTier.REQUIRES_APPROVAL:
            reason = f"{action!r} is whitelisted but requires human sign-off"
        else:
            reason = f"{action!r} is explicitly blocked in this deployment"
        return Decision(action=action, tier=tier, reason=reason)
