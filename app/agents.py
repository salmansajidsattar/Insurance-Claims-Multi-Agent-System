"""The 6 agents. Each agent reads the ClaimState, fills ONE field, and returns it.

    1. intake      description  -> state.claim    (LLM)
    2. damage      photo        -> state.damage   (vision LLM + simple rules)
    3. policy      package      -> state.policy   (plain rules, no LLM)
    4. policy_rag  wording      -> state.rag      (RAG + LLM, must agree with step 3)
    5. risk        history      -> state.risk     (plain rules)
    6. payout      estimate     -> state.payout   (plain maths)

Every agent does its work inside run_agent(), which retries once, writes an audit
entry for every try, and records a failure if both tries fail.
"""
import re
from datetime import date
from typing import get_args

from pydantic import Field

from app import config, data, llm, rag
from app.schemas import (
    AuditEntry,
    Circumstance,
    Claim,
    ClaimState,
    Damage,
    DamageType,
    IncidentType,
    Payout,
    PolicyDecision,
    Quote,
    RagDecision,
    Risk,
    Severity,
    Strict,
    Zone,
)


# ================================================================ shared helper
def run_agent(state: ClaimState, name: str, inputs: dict, work):
    """Run work() up to 2 times. work() returns (result, note). Returns result or None."""
    for attempt in (1, 2):
        try:
            result, note = work()
            state.audit.append(AuditEntry(agent=name, status="ok", input=inputs,
                                          output=result.model_dump(mode="json"), note=note))
            return result
        except Exception as e:   # bad JSON, wrong field, invented clause, model down ...
            error = f"{type(e).__name__}: {e}"[:1000]
            if attempt == 1:
                state.audit.append(AuditEntry(agent=name, status="retry", input=inputs,
                                              error=error, note="bad output, trying again"))
            else:
                state.audit.append(AuditEntry(agent=name, status="failed", input=inputs,
                                              error=error, note="failed twice -> human review"))
    state.failures.append(name)
    return None


def skip(state: ClaimState, name: str, note: str) -> ClaimState:
    state.audit.append(AuditEntry(agent=name, status="skipped", note=note))
    return state


def options(literal) -> str:
    return ", ".join(get_args(literal))


# ================================================================ 1. intake
class IntakeAnswer(Strict):
    incident_type: IncidentType
    zones: list[Zone] = Field(min_length=1)
    severity: Severity
    third_party: bool
    circumstances: list[Circumstance] = []


INTAKE_PROMPT = f"""You read a car insurance claim and pull out facts. Reply with JSON only:
- "incident_type": one of [{options(IncidentType)}]
- "zones": list of damaged areas, each one of [{options(Zone)}]
- "severity": one of [{options(Severity)}]
- "third_party": true if another person, driver or vehicle was involved
- "circumstances": list (can be empty), each one of [{options(Circumstance)}]
  Only if the text clearly says it: drinking -> driver_impaired, deliveries or ride-hailing ->
  commercial_use, race track -> racing_or_track_use, no licence -> unlicensed_driver,
  on purpose -> intentional_act.
Do not guess. No other keys."""


def intake(state: ClaimState) -> ClaimState:
    sub = state.submission

    def work():
        answer = IntakeAnswer.model_validate(
            llm.ask_json(INTAKE_PROMPT, f"Claim description:\n{sub.description}"))
        claim = Claim(
            claim_id=state.claim_id, claimant_id=sub.claimant_id, policy_id=sub.policy_id,
            incident_date=sub.incident_date, reported_date=sub.submitted_at.date(),
            police_report=sub.police_report_filed, description=sub.description,
            **answer.model_dump(),
        )
        note = f"{answer.incident_type}, {answer.zones}, {answer.severity}, circumstances={answer.circumstances}"
        return claim, note

    state.claim = run_agent(state, "intake", {"description": sub.description}, work)
    return state


# ================================================================ 2. damage
class PhotoAnswer(Strict):
    image_usable: bool
    damage_type: DamageType
    severity: Severity
    zones: list[Zone] = []
    confidence: float = Field(ge=0, le=1)
    looks_old: bool = False


PHOTO_PROMPT = f"""You are a car damage inspector. Look ONLY at the photo. Reply with JSON only:
- "image_usable": false if the photo is too dark, blurry or not a car
- "damage_type": one of [{options(DamageType)}]
- "severity": one of [{options(Severity)}]
- "zones": list of damaged areas, each one of [{options(Zone)}]
- "confidence": 0 to 1, how sure you are
- "looks_old": true if the damage looks old (rust, faded paint)"""

# Photos can't show left vs right or bumper vs hood reliably, so compare rough areas.
AREA = {"front": "front", "hood": "front", "windshield": "front", "rear": "rear",
        "left_side": "side", "right_side": "side", "wheels": "side", "roof": "top",
        "undercarriage": "bottom", "interior": "inside", "unknown": "unknown"}
SEVERITY_LEVEL = {"none": 0, "minor": 1, "moderate": 2, "severe": 3, "total_loss": 4}
DAMAGE_FOR_INCIDENT = {
    "collision": {"dent", "scratch", "crack", "crush", "shattered_glass", "other"},
    "vandalism": {"dent", "scratch", "crack", "shattered_glass", "other"},
    "weather": {"dent", "crack", "shattered_glass", "other"},
    "flood": {"water_damage", "other"},
    "fire": {"fire_damage", "other"},
    "theft": set(get_args(DamageType)),
    "other": set(get_args(DamageType)),
}


def compare_photo_to_story(claim: Claim, photo: PhotoAnswer) -> list[str]:
    """Empty list = the photo supports the story."""
    if not photo.image_usable:
        return ["image_unusable"]
    if photo.damage_type == "no_visible_damage":
        return ["no_damage_visible"]
    problems = []
    if not {AREA[z] for z in claim.zones} & {AREA[z] for z in photo.zones}:
        problems.append("wrong_area")
    if abs(SEVERITY_LEVEL[claim.severity] - SEVERITY_LEVEL[photo.severity]) >= 2:
        problems.append("wrong_severity")
    if photo.damage_type not in DAMAGE_FOR_INCIDENT[claim.incident_type]:
        problems.append("wrong_damage_type")
    if photo.looks_old:
        problems.append("looks_old")
    return problems


def damage(state: ClaimState) -> ClaimState:
    claim = state.claim
    if claim is None:
        return skip(state, "damage", "no claim facts (intake failed)")
    image = str(data.ROOT / state.submission.image_path)

    def work():
        photo = PhotoAnswer.model_validate(llm.ask_json(
            PHOTO_PROMPT, "Describe the damage in this photo.", image_path=image, model=config.VISION_MODEL))
        problems = compare_photo_to_story(claim, photo)
        result = Damage(image_usable=photo.image_usable, damage_type=photo.damage_type,
                        severity=photo.severity, zones=photo.zones, confidence=photo.confidence,
                        matches_story=not problems, mismatch_reasons=problems)
        note = f"photo: {photo.damage_type}/{photo.severity} at {photo.zones}; problems={problems or 'none'}"
        return result, note

    state.damage = run_agent(state, "damage", {"image": state.submission.image_path}, work)
    return state


# ================================================================ 3. policy (rules)
def check_policy(claim: Claim, policy) -> PolicyDecision:
    reasons, exclusions, cited = [], [], []

    # a) incident inside the policy period?
    if not (policy.start <= claim.incident_date <= policy.end):
        reasons.append("policy_inactive")
        cited += [c.id for c in policy.clauses if c.type == "condition" and not c.window_days]

    # b) is this peril in the customer's package?
    cover = policy.cover_for(claim.incident_type)
    if cover:
        cited.append(policy.cover_id(cover.peril))
        cited += [c.id for c in policy.clauses if claim.incident_type in c.perils]
    else:
        reasons.append("peril_not_covered")
        cited += [policy.cover_id(c.peril) for c in policy.covers]   # show what IS covered

    # c) does an exclusion apply?
    exclusions = [c.id for c in policy.clauses
                  if c.type == "exclusion" and c.exclusion in claim.circumstances]
    if exclusions:
        reasons.append("exclusion_hit")
        cited += exclusions

    # d) reported in time?
    for c in policy.clauses:
        if c.window_days and claim.delay_days > c.window_days:
            reasons.append("condition_failed")
            cited.append(c.id)

    covered = not reasons
    if covered:
        cited += [c.id for c in policy.clauses if c.type in ("deductible", "limit")]
    return PolicyDecision(covered=covered, reasons=reasons, exclusions=exclusions,
                          limit=cover.limit if cover else 0,
                          deductible=cover.deductible if cover else 0,
                          cited=list(dict.fromkeys(cited)))


def policy(state: ClaimState) -> ClaimState:
    claim = state.claim
    if claim is None:
        return skip(state, "policy", "no claim facts (intake failed)")

    def work():
        pol = data.get_policy(claim.policy_id)
        if pol is None:
            raise ValueError(f"policy {claim.policy_id} not found")
        result = check_policy(claim, pol)
        unknown = set(result.cited) - pol.all_ids()
        if unknown:                                   # never cite a rule that doesn't exist
            raise ValueError(f"cited clauses not in policy: {unknown}")
        note = ("covered" if result.covered else f"not covered: {result.reasons}") + f"; cited {result.cited}"
        return result, note

    state.policy = run_agent(state, "policy", {"policy_id": claim.policy_id}, work)
    return state


# ================================================================ 4. policy RAG
INCIDENT_WORDS = {"collision": "collision impact with another vehicle or object",
                  "vandalism": "malicious damage keying scratching by someone else",
                  "weather": "storm hail windstorm", "flood": "flood water",
                  "fire": "fire lightning explosion", "theft": "theft attempted theft", "other": "damage"}
CIRCUMSTANCE_WORDS = {"intentional_act": "damage caused on purpose",
                      "damage_before_policy": "damage existed before the policy start date",
                      "racing_or_track_use": "racing track day race circuit",
                      "unlicensed_driver": "driver without a valid driving licence",
                      "driver_impaired": "driver under the influence of alcohol or drugs",
                      "commercial_use": "vehicle used for hire ride-hailing delivery",
                      "gradual_wear": "wear and tear rust breakdown"}


class RagAnswer(Strict):
    covered: bool
    exclusions: list[str] = []
    cited: list[str] = Field(min_length=1)
    quotes: list[Quote] = Field(min_length=1)


RAG_PROMPT = """You are an insurance claims analyst. Decide if the claim is covered using ONLY the
policy clauses and the customer's package below. Not covered if: incident outside the policy
period, peril not in the package, an exclusion applies, or reported later than allowed.
Reply with JSON only:
{"covered": true/false, "exclusions": [ids of exclusion clauses that apply],
 "cited": [clause or package ids you used],
 "quotes": [{"clause_id": "...", "quote": "exact sentence copied from that clause"}]}"""


def rag_query(claim: Claim) -> str:
    """Search text built from the extracted facts (not from the user's raw words)."""
    parts = [INCIDENT_WORDS[claim.incident_type]] + [CIRCUMSTANCE_WORDS[c] for c in claim.circumstances]
    parts += ["period of insurance start date", "report incident within days", "deductible", "limit of cover"]
    return ". ".join(parts)


def rag_prompt(claim_id: str, claim: Claim, pol, clauses) -> str:
    circ = ", ".join(CIRCUMSTANCE_WORDS[c] for c in claim.circumstances) or "none mentioned"
    lines = [f"Claim reference: {claim_id}", "CLAIM",
             f"- incident: {claim.incident_type}, areas: {', '.join(claim.zones)}",
             f"- incident date: {claim.incident_date}, reported {claim.delay_days} days later",
             f"- other party: {'yes' if claim.third_party else 'no'}, circumstances: {circ}",
             f"PACKAGE ({pol.product_name}, {pol.start} to {pol.end})"]
    lines += [f"- [{pol.cover_id(c.peril)}] {c.peril}: limit {c.limit:,.0f}, deductible {c.deductible:,.0f}"
              for c in pol.covers]
    lines += ["POLICY CLAUSES"] + [f"[{c.id}] {c.title}: {c.text}" for c in clauses]
    return "\n".join(lines)


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def check_rag_answer(answer: RagAnswer, clauses, pol) -> None:
    """The LLM may only use clauses it was given, and quotes must be copied exactly."""
    allowed = {c.id for c in clauses} | {pol.cover_id(c.peril) for c in pol.covers}
    for cid in answer.cited + answer.exclusions:
        if cid not in allowed:
            raise ValueError(f"{cid} was not retrieved")
    for cid in answer.exclusions:
        if pol.clause(cid).type != "exclusion":
            raise ValueError(f"{cid} is not an exclusion")
    text = {c.id: flat(c.text) for c in clauses}
    for q in answer.quotes:
        if q.clause_id not in text or flat(q.quote) not in text[q.clause_id]:
            raise ValueError(f"quote is not word-for-word from {q.clause_id}")


def policy_rag(state: ClaimState) -> ClaimState:
    claim = state.claim
    if claim is None:
        return skip(state, "policy_rag", "no claim facts (intake failed)")
    query = rag_query(claim)

    def work():
        pol = data.get_policy(claim.policy_id)
        if pol is None:
            raise ValueError(f"policy {claim.policy_id} not found")
        clauses = rag.search(query, pol.product_id)
        answer = RagAnswer.model_validate(llm.ask_json(RAG_PROMPT, rag_prompt(state.claim_id, claim, pol, clauses)))
        check_rag_answer(answer, clauses, pol)
        rules = state.policy
        agrees = (rules is not None and answer.covered == rules.covered
                  and set(answer.exclusions) == set(rules.exclusions))
        result = RagDecision(retrieved=[c.id for c in clauses], agrees=agrees, **answer.model_dump())
        note = f"RAG covered={answer.covered}, rules covered={rules.covered if rules else None} -> {'agree' if agrees else 'DISAGREE'}"
        return result, note

    state.rag = run_agent(state, "policy_rag", {"query": query}, work)
    return state


# ================================================================ 5. risk
def risk(state: ClaimState) -> ClaimState:
    claim, dmg = state.claim, state.damage
    if claim is None or dmg is None:
        return skip(state, "risk", "missing claim facts or photo check")

    def work():
        history = data.get_history(claim.claimant_id)
        if history is None:
            raise ValueError(f"no history for {claim.claimant_id}")
        pol = data.get_policy(claim.policy_id)
        flags = {}   # flag name -> evidence

        past = history["claims"]
        recent = [c for c in past if 0 <= (claim.incident_date - date.fromisoformat(c["date"])).days <= 365]
        if len(recent) >= 3:
            flags["frequent_claims"] = f"{len(recent)} claims in the last year"
        frauds = [c["date"] for c in past if c["fraud_flag"]]
        if frauds:
            flags["fraud_flag"] = f"fraud flag on claim of {frauds[0]}"
        if not dmg.matches_story:
            flags["photo_mismatch"] = ", ".join(dmg.mismatch_reasons)
        if dmg.confidence < config.MIN_CONFIDENCE:
            flags["low_confidence"] = f"photo confidence {dmg.confidence}"
        if claim.delay_days > 14:
            flags["late_report"] = f"reported after {claim.delay_days} days"
        if pol and 0 <= (claim.incident_date - pol.start).days < 30:
            flags["new_policy"] = f"incident {(claim.incident_date - pol.start).days} days after policy start"
        if claim.third_party and not claim.police_report:
            flags["no_police_report"] = "other party involved, no police report"

        score = round(min(1.0, sum(config.RISK_WEIGHTS[f] for f in flags)), 2)
        result = Risk(score=score, flags=[f"{name}: {why}" for name, why in flags.items()])
        return result, f"score {score}: {list(flags) or 'no flags'}"

    state.risk = run_agent(state, "risk", {"claimant_id": claim.claimant_id}, work)
    return state


# ================================================================ 6. payout
def make_payout(estimate: float, limit: float, deductible: float) -> Payout:
    eligible = min(estimate, limit)
    ded = min(deductible, eligible)
    return Payout(estimate=round(estimate, 2), eligible=round(eligible, 2), deductible=round(ded, 2),
                  net=round(eligible - ded, 2), capped=estimate > limit)


def payout(state: ClaimState) -> ClaimState:
    if state.policy is None:
        return skip(state, "payout", "no policy decision")
    if not state.policy.covered:
        return skip(state, "payout", "not covered, nothing to pay")

    def work():
        total = data.get_estimate_total(state.claim_id)
        if total is None:
            raise ValueError("no repair estimate for this claim")
        result = make_payout(total, state.policy.limit, state.policy.deductible)
        return result, f"min({total}, {state.policy.limit}) - {result.deductible} = {result.net}"

    state.payout = run_agent(state, "payout", {"limit": state.policy.limit,
                                               "deductible": state.policy.deductible}, work)
    return state
