"""Tests for the data, the agents, the router and the pipeline (fake models, no Ollama)."""
import pytest
from pydantic import ValidationError

from app import agents, data, llm, pipeline, rag, router
from app.pipeline import process_claim
from app.schemas import Claim, Risk, Submission
from tests.fakes import SAMPLES, fake_ask

IDS = list(SAMPLES)
ORDER = ["intake", "damage", "policy", "policy_rag", "risk", "payout", "router"]


def run(claim_id: str):
    return process_claim(Submission(**SAMPLES[claim_id]["submission"]), claim_id)


# ---------------------------------------------------------------- data
def test_sample_set_is_balanced():
    outcomes = [c["expected"]["outcome"] for c in SAMPLES.values()]
    assert len(outcomes) == 30
    assert (outcomes.count("auto_approve"), outcomes.count("human_review"), outcomes.count("reject")) == (10, 11, 9)


@pytest.mark.parametrize("claim_id", IDS)
def test_sample_data_is_consistent(claim_id):
    sub, e = SAMPLES[claim_id]["submission"], SAMPLES[claim_id]["expected"]
    pol = data.get_policy(sub["policy_id"])
    assert pol.holder_id == sub["claimant_id"]
    assert data.get_history(sub["claimant_id"]) is not None
    assert (data.ROOT / sub["image_path"]).exists()
    cover = pol.cover_for(e["incident_type"])
    if e["net_payout"] is not None:               # expected payout = the payout formula
        p = agents.make_payout(data.get_estimate_total(claim_id), cover.limit, cover.deductible)
        assert (p.net, p.capped) == (e["net_payout"], e["capped"])


def test_every_policy_has_wording():
    for pol in data.policies().values():
        types = {c.type for c in pol.clauses}
        assert {"coverage", "exclusion", "deductible", "limit", "condition"} <= types
        for cover in pol.covers:                  # every peril in the package has a coverage clause
            assert any(cover.peril in c.perils for c in pol.clauses)


# ---------------------------------------------------------------- input checks
def test_description_over_100_words_is_rejected():
    sub = dict(SAMPLES["CLM-EVAL-001"]["submission"], description="word " * 101)
    with pytest.raises(ValidationError, match="1-100 words"):
        Submission(**sub)


def test_llm_cannot_add_extra_fields():
    with pytest.raises(ValidationError, match="Extra inputs"):
        agents.IntakeAnswer(incident_type="collision", zones=["rear"], severity="minor",
                            third_party=False, notes="free text")


# ---------------------------------------------------------------- the full pipeline, all 30 claims
@pytest.mark.parametrize("claim_id", IDS)
def test_claim_gets_the_expected_result(claim_id):
    s, e = run(claim_id), SAMPLES[claim_id]["expected"]
    assert s.decision.outcome == e["outcome"]
    assert set(e["reasons"]) <= set(s.decision.reasons)
    assert (s.payout.net if s.payout else None) == e["net_payout"]
    assert s.damage.matches_story == e["matches_story"]
    assert s.rag.agrees
    assert set(s.policy.cited) <= data.get_policy(s.submission.policy_id).all_ids()
    assert [a.agent for a in s.audit] == ORDER
    assert not s.failures


@pytest.mark.parametrize("claim_id", IDS)
def test_rag_finds_the_deciding_clauses(claim_id):
    """The top-6 search results must include every clause the decision depends on."""
    s, e = run(claim_id), SAMPLES[claim_id]["expected"]
    pol = data.get_policy(s.submission.policy_id)
    needed = {c.id for c in pol.clauses
              if e["incident_type"] in c.perils
              or c.type == "condition"
              or (c.exclusion and c.exclusion in e["circumstances"])}
    assert needed <= set(s.rag.retrieved)


def test_search_stays_inside_the_product():
    found = rag.search("driver under the influence of alcohol", "BASIC")
    assert all(c.id.startswith("BASIC-") for c in found)


def test_saved_to_disk_after_run():
    s = run("CLM-EVAL-001")
    assert data.load_state("CLM-EVAL-001") == s
    with pytest.raises(ValueError):
        data.load_state("../secret")


# ---------------------------------------------------------------- router rules
@pytest.fixture
def approved():
    s = run("CLM-EVAL-001")
    assert s.decision.outcome == "auto_approve"
    return s


def test_high_risk_goes_to_review(approved):
    approved.risk = Risk(score=0.6, flags=["fraud_flag: test"])
    assert "high_risk" in router.decide(approved).reasons


def test_photo_mismatch_goes_to_review(approved):
    approved.damage.matches_story = False
    assert router.decide(approved).outcome == "human_review"


def test_big_payout_goes_to_review(approved):
    approved.payout = agents.make_payout(10000, 25000, 500)          # net 9,500
    assert "payout_above_limit" in router.decide(approved).reasons


def test_payout_exactly_at_limit_is_approved(approved):
    approved.payout = agents.make_payout(5500, 25000, 500)           # net 5,000
    assert router.decide(approved).outcome == "auto_approve"


def test_review_beats_reject():
    s = run("CLM-EVAL-017")                                          # drunk driving -> reject
    s.damage.matches_story = False
    d = router.decide(s)
    assert d.outcome == "human_review" and "exclusion_hit" in d.reasons


# ---------------------------------------------------------------- fail closed
def test_junk_model_output_goes_to_review():
    llm.use_fake(ask=lambda *a: {"junk": True})
    s = run("CLM-EVAL-001")
    assert s.decision.outcome == "human_review" and "agent_failure" in s.decision.reasons
    assert [(a.agent, a.status) for a in s.audit][:2] == [("intake", "retry"), ("intake", "failed")]
    assert [a.status for a in s.audit if a.agent in ("damage", "policy", "risk")] == ["skipped"] * 3


def with_rag_change(change):
    def ask(system, user, image_path=None):
        answer = fake_ask(system, user, image_path)
        return change(answer) if "claims analyst" in system else answer
    llm.use_fake(ask=ask, embed=llm._fake_embed)


def test_rag_disagreeing_with_rules_goes_to_review():
    with_rag_change(lambda a: {**a, "covered": not a["covered"]})
    s = run("CLM-EVAL-001")
    assert "rules_rag_disagree" in s.decision.reasons


def test_rag_citing_an_unretrieved_clause_fails():
    with_rag_change(lambda a: {**a, "cited": a["cited"] + ["MADE-UP-CLAUSE"]})
    s = run("CLM-EVAL-001")
    assert "was not retrieved" in [a for a in s.audit if a.agent == "policy_rag"][-1].error
    assert s.decision.outcome == "human_review"


def test_rag_inventing_a_quote_fails():
    def bad_quote(a):
        a["quotes"][0]["quote"] = "We always pay for everything, no questions asked."
        return a
    with_rag_change(bad_quote)
    s = run("CLM-EVAL-001")
    assert "policy_rag" in s.failures and s.decision.outcome == "human_review"


def test_crash_goes_to_review(monkeypatch):
    def boom(state):
        raise RuntimeError("crash")
    monkeypatch.setattr(pipeline, "STEPS", [(n, boom if n == "risk" else f) for n, f in pipeline.STEPS])
    monkeypatch.setattr(pipeline, "GRAPH", pipeline.build_graph())
    s = run("CLM-EVAL-001")
    assert s.decision.outcome == "human_review"
    assert [a.agent for a in s.audit][:4] == ["intake", "damage", "policy", "policy_rag"]  # saved before crash


# ---------------------------------------------------------------- single agents
def test_photo_rules():
    claim = Claim(claim_id="X", claimant_id="C", policy_id="P", incident_type="collision",
                  incident_date="2026-09-01", reported_date="2026-09-02", zones=["rear"],
                  severity="minor", third_party=False, police_report=False, description="x")
    photo = agents.PhotoAnswer(image_usable=True, damage_type="crush", severity="severe",
                               zones=["front"], confidence=0.9)
    assert agents.compare_photo_to_story(claim, photo) == ["wrong_area", "wrong_severity"]


def test_payout_maths():
    assert agents.make_payout(2000, 10000, 500).net == 1500      # normal
    assert agents.make_payout(300, 10000, 500).net == 0          # below deductible
    p = agents.make_payout(15000, 8000, 1000)                    # capped at limit
    assert (p.net, p.capped) == (7000, True)


def test_eval_runner_fake_mode_is_perfect(monkeypatch, tmp_path):
    from tests import eval as runner
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    assert runner.main(["--fake"]) == 1.0
