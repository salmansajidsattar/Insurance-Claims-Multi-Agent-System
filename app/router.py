"""The router: plain if-statements, no LLM. Checked in this order:

  1. Any reason for a person to look?   -> human_review
  2. Otherwise, not covered?            -> reject
  3. Otherwise                          -> auto_approve
"""
from app import config
from app.schemas import AuditEntry, ClaimState, Decision


def decide(state: ClaimState) -> Decision:
    review = []
    if state.failures:
        review.append("agent_failure")
    if None in (state.claim, state.damage, state.policy, state.rag, state.risk) or (
            state.policy and state.policy.covered and state.payout is None):
        review.append("missing_data")
    if state.damage and not state.damage.matches_story:
        review.append("photo_mismatch")
    if state.damage and state.damage.confidence < config.MIN_CONFIDENCE:
        review.append("low_confidence")
    if state.rag and not state.rag.agrees:
        review.append("rules_rag_disagree")
    if state.risk and state.risk.score >= config.RISK_LIMIT:
        review.append("high_risk")
    if state.payout and state.payout.net > config.PAYOUT_LIMIT:
        review.append("payout_above_limit")

    reject = state.policy.reasons if state.policy and not state.policy.covered else []
    cited = state.policy.cited if state.policy else []

    if review:   # reject reasons are shown too, so the reviewer sees everything
        return Decision(outcome="human_review", reasons=review + reject, cited=cited)
    if reject:
        return Decision(outcome="reject", reasons=reject, cited=cited)
    return Decision(outcome="auto_approve", reasons=["all_checks_passed"], cited=cited)


def router(state: ClaimState) -> ClaimState:
    state.decision = decide(state)
    state.audit.append(AuditEntry(agent="router", status="ok",
                                  output=state.decision.model_dump(mode="json"),
                                  note=f"{state.decision.outcome}: {state.decision.reasons}"))
    return state
