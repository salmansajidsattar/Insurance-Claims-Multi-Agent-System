"""Wire the agents together with LangGraph and run one claim.

    intake -> damage -> policy -> policy_rag -> risk -> payout -> router -> END

After every step the whole ClaimState is saved to data/audit/<claim_id>.json, so the
UI can show progress and nothing is lost if something crashes.
"""
import uuid

from langgraph.graph import END, START, StateGraph

from app import agents, data, router
from app.schemas import AuditEntry, ClaimState, Decision, Submission

STEPS = [
    ("intake", agents.intake),
    ("damage", agents.damage),
    ("policy", agents.policy),
    ("policy_rag", agents.policy_rag),
    ("risk", agents.risk),
    ("payout", agents.payout),
    ("router", router.router),
]


def as_node(step):
    """Run the step, save to disk, hand the updated fields back to LangGraph."""
    def node(state: ClaimState) -> dict:
        state = step(state)
        data.save_state(state)
        return dict(state)
    return node


def build_graph():
    graph = StateGraph(ClaimState)
    for name, step in STEPS:
        graph.add_node(name, as_node(step))
    names = [name for name, _ in STEPS]
    graph.add_edge(START, names[0])
    for a, b in zip(names, names[1:]):
        graph.add_edge(a, b)
    graph.add_edge(names[-1], END)
    return graph.compile()


GRAPH = build_graph()


def new_claim_id() -> str:
    return f"CLM-{uuid.uuid4().hex[:10].upper()}"


def process_claim(submission: Submission, claim_id: str | None = None) -> ClaimState:
    """Run the whole pipeline. Never raises: if something crashes, the claim goes to review."""
    state = ClaimState(claim_id=claim_id or new_claim_id(), submission=submission)
    data.save_state(state)
    try:
        return ClaimState.model_validate(dict(GRAPH.invoke(state)))
    except Exception as e:
        state = data.load_state(state.claim_id) or state        # keep the finished steps
        state.failures.append("pipeline_crash")
        state.decision = Decision(outcome="human_review", reasons=["agent_failure"])
        state.audit.append(AuditEntry(agent="router", status="failed", error=f"{type(e).__name__}: {e}",
                                      note="pipeline crashed -> human review"))
        data.save_state(state)
        return state
