"""
Lab 11 — Part 4: Human-in-the-Loop Design
  TODO 11: Confidence Router
  TODO 12: Design 3 HITL decision points
"""
from dataclasses import dataclass


# ============================================================
# TODO 11: Implement ConfidenceRouter
#
# Route agent responses based on confidence scores:
#   - HIGH (>= 0.9): Auto-send to user
#   - MEDIUM (0.7 - 0.9): Queue for human review
#   - LOW (< 0.7): Escalate to human immediately
#
# Special case: if the action is HIGH_RISK (e.g., money transfer,
# account deletion), ALWAYS escalate regardless of confidence.
#
# Implement the route() method.
# ============================================================

HIGH_RISK_ACTIONS = [
    "transfer_money",
    "close_account",
    "change_password",
    "delete_data",
    "update_personal_info",
]


@dataclass
class RoutingDecision:
    """Result of the confidence router."""
    action: str          # "auto_send", "queue_review", "escalate"
    confidence: float
    reason: str
    priority: str        # "low", "normal", "high"
    requires_human: bool


class ConfidenceRouter:
    """Route agent responses based on confidence and risk level.

    Thresholds:
        HIGH:   confidence >= 0.9 -> auto-send
        MEDIUM: 0.7 <= confidence < 0.9 -> queue for review
        LOW:    confidence < 0.7 -> escalate to human

    High-risk actions always escalate regardless of confidence.
    """

    HIGH_THRESHOLD = 0.9
    MEDIUM_THRESHOLD = 0.7

    def route(self, response: str, confidence: float,
              action_type: str = "general") -> RoutingDecision:
        """Route a response based on confidence score and action type.

        Args:
            response: The agent's response text
            confidence: Confidence score between 0.0 and 1.0
            action_type: Type of action (e.g., "general", "transfer_money")

        Returns:
            RoutingDecision with routing action and metadata
        """
        # TODO 11: Implement routing logic
        #
        # 1. Check if action_type is in HIGH_RISK_ACTIONS
        #    -> If yes: always escalate (action="escalate", priority="high",
        #       requires_human=True, reason="High-risk action: {action_type}")
        #
        # 2. Check confidence thresholds:
        #    - confidence >= 0.9:
        #      action="auto_send", priority="low",
        #      requires_human=False, reason="High confidence"
        #
        #    - 0.7 <= confidence < 0.9:
        #      action="queue_review", priority="normal",
        #      requires_human=True, reason="Medium confidence — needs review"
        #
        #    - confidence < 0.7:
        #      action="escalate", priority="high",
        #      requires_human=True, reason="Low confidence — escalating"

        normalized_action = (action_type or "general").strip().lower()
        bounded_confidence = max(0.0, min(1.0, confidence))

        if normalized_action in HIGH_RISK_ACTIONS:
            return RoutingDecision(
                action="escalate",
                confidence=bounded_confidence,
                reason=f"High-risk action: {normalized_action}",
                priority="high",
                requires_human=True,
            )

        if bounded_confidence >= self.HIGH_THRESHOLD:
            return RoutingDecision(
                action="auto_send",
                confidence=bounded_confidence,
                reason="High confidence",
                priority="low",
                requires_human=False,
            )

        if bounded_confidence >= self.MEDIUM_THRESHOLD:
            return RoutingDecision(
                action="queue_review",
                confidence=bounded_confidence,
                reason="Medium confidence - needs review",
                priority="normal",
                requires_human=True,
            )

        return RoutingDecision(
            action="escalate",
            confidence=bounded_confidence,
            reason="Low confidence - escalating",
            priority="high",
            requires_human=True,
        )


# ============================================================
# TODO 12: Design 3 HITL decision points + a review lifecycle
#
# For each decision point, define:
# - trigger: What condition activates this HITL check?
# - hitl_model: Which model? (human-in-the-loop, human-on-the-loop,
#   human-as-tiebreaker)
# - context_needed: What info does the human reviewer need?
# - example: A concrete scenario
# - approval_path: What approve/reject/timeout decision is recorded?
# - audit_fields: Which correlation ID, intent and proposed action/diff are logged?
#
# Think about real banking scenarios where human judgment is critical.
# ============================================================

hitl_decision_points = [
    {
        "id": 1,
        "name": "High-value transfer approval",
        "trigger": "Any transfer_money action above the daily low-risk limit, to a new beneficiary, or flagged by fraud/rate-limit signals.",
        "hitl_model": "human-in-the-loop",
        "context_needed": "Reviewer sees correlation ID, authenticated user, account status, amount, currency, source account, beneficiary, risk signals, model rationale and exact proposed transfer payload.",
        "example": "Customer asks to transfer 50,000,000 VND to a newly added beneficiary after several failed OTP attempts.",
        "approval_path": "Approve sends the action to the egress gateway with reviewer_id and approval_id; reject returns a safe refusal; timeout fails closed and no transfer is sent.",
        "audit_fields": "request_id, correlation_id, user_id, intent=transfer_money, action_type, before_after_balance_diff, beneficiary_diff, risk_score, reviewer_id, decision, decision_at, timeout_at, approval_id",
    },
    {
        "id": 2,
        "name": "Sensitive profile change",
        "trigger": "Any change_password or update_personal_info action, especially phone, email, address, KYC fields or device binding changes.",
        "hitl_model": "human-in-the-loop",
        "context_needed": "Reviewer sees verified identity evidence, OTP/MFA status, old vs new values, recent login/device history, customer message and the exact diff the agent proposes.",
        "example": "Customer requests changing the registered phone number and password in the same session from a new device.",
        "approval_path": "Approve applies only the reviewed diff; reject keeps current profile and asks for branch or stronger verification; timeout fails closed and records no state change.",
        "audit_fields": "request_id, correlation_id, user_id, intent=update_personal_info, field_diff, mfa_status, device_id, ip_region, reviewer_id, decision, reason_code, decision_at",
    },
    {
        "id": 3,
        "name": "Unsafe output or egress tiebreaker",
        "trigger": "Output guardrail, egress policy or LLM judge flags possible PII/secret leak, hallucinated financial advice, or conflicting safe/unsafe classifications.",
        "hitl_model": "human-as-tiebreaker",
        "context_needed": "Reviewer sees original user request, retrieved source snippets with provenance, draft answer before/after redaction, detected PII/secret patterns, judge verdict and proposed destination if egress is involved.",
        "example": "Agent drafts a response containing a customer phone number and an unsupported savings rate while summarizing an external email.",
        "approval_path": "Approve only a redacted/corrected answer; reject blocks the response and opens an incident; timeout returns a generic safe message and preserves all evidence for replay.",
        "audit_fields": "request_id, correlation_id, user_id, source_ids, output_diff, detected_entities, judge_verdict, egress_destination, reviewer_id, decision, incident_id, decision_at",
    },
]


# ============================================================
# Quick tests
# ============================================================

def test_confidence_router():
    """Test ConfidenceRouter with sample scenarios."""
    router = ConfidenceRouter()

    test_cases = [
        ("Balance inquiry", 0.95, "general"),
        ("Interest rate question", 0.82, "general"),
        ("Ambiguous request", 0.55, "general"),
        ("Transfer $50,000", 0.98, "transfer_money"),
        ("Close my account", 0.91, "close_account"),
    ]

    print("Testing ConfidenceRouter:")
    print("=" * 80)
    print(f"{'Scenario':<25} {'Conf':<6} {'Action Type':<18} {'Decision':<15} {'Priority':<10} {'Human?'}")
    print("-" * 80)

    for scenario, conf, action_type in test_cases:
        decision = router.route(scenario, conf, action_type)
        print(
            f"{scenario:<25} {conf:<6.2f} {action_type:<18} "
            f"{decision.action:<15} {decision.priority:<10} "
            f"{'Yes' if decision.requires_human else 'No'}"
        )

    print("=" * 80)


def test_hitl_points():
    """Display HITL decision points."""
    print("\nHITL Decision Points:")
    print("=" * 60)
    for point in hitl_decision_points:
        print(f"\n  Decision Point #{point['id']}: {point['name']}")
        print(f"    Trigger:  {point['trigger']}")
        print(f"    Model:    {point['hitl_model']}")
        print(f"    Context:  {point['context_needed']}")
        print(f"    Example:  {point['example']}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    test_confidence_router()
    test_hitl_points()
