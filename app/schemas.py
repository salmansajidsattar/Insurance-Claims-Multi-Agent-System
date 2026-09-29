"""Every piece of data passed between agents is one of these Pydantic models.

extra="forbid" means: if a model (LLM) returns a field we did not ask for, it fails
validation instead of sneaking free text into the next agent.
"""
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---- allowed values -------------------------------------------------------------
IncidentType = Literal["collision", "vandalism", "weather", "flood", "fire", "theft", "other"]
Zone = Literal["front", "rear", "left_side", "right_side", "roof", "hood", "windshield",
               "wheels", "undercarriage", "interior", "unknown"]
Severity = Literal["none", "minor", "moderate", "severe", "total_loss"]
DamageType = Literal["dent", "scratch", "crack", "shattered_glass", "crush", "fire_damage",
                     "water_damage", "no_visible_damage", "other"]
Circumstance = Literal["intentional_act", "damage_before_policy", "racing_or_track_use",
                       "unlicensed_driver", "driver_impaired", "commercial_use", "gradual_wear"]
Outcome = Literal["auto_approve", "human_review", "reject"]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- input ------------------------------------------------------------------------
class Submission(Strict):
    """What the user sends in."""
    claimant_id: str
    policy_id: str
    description: str
    incident_date: date
    submitted_at: datetime = Field(default_factory=now)
    image_path: str
    vehicle: str | None = None
    police_report_filed: bool = False

    @field_validator("description")
    @classmethod
    def max_100_words(cls, text: str) -> str:
        words = len(text.split())
        if words == 0 or words > 100:
            raise ValueError(f"description must be 1-100 words, got {words}")
        return text


# ---- agent outputs ---------------------------------------------------------------
class Claim(Strict):
    """Agent 1 (Intake): the facts pulled out of the description."""
    claim_id: str
    claimant_id: str
    policy_id: str
    incident_type: IncidentType
    incident_date: date
    reported_date: date
    zones: list[Zone] = Field(min_length=1)
    severity: Severity
    third_party: bool
    police_report: bool
    circumstances: list[Circumstance] = []
    description: str

    @property
    def delay_days(self) -> int:
        return (self.reported_date - self.incident_date).days


class Damage(Strict):
    """Agent 2 (Damage Evidence): what the photo shows + does it match the story."""
    image_usable: bool
    damage_type: DamageType
    severity: Severity
    zones: list[Zone] = []
    confidence: float = Field(ge=0, le=1)
    matches_story: bool
    mismatch_reasons: list[str] = []


class PolicyDecision(Strict):
    """Agent 3 (Policy rules): covered or not, and which clauses say so."""
    covered: bool
    reasons: list[str] = []            # policy_inactive, peril_not_covered, exclusion_hit, condition_failed
    exclusions: list[str] = []         # clause ids
    limit: float = 0
    deductible: float = 0
    cited: list[str] = Field(min_length=1)


class Quote(Strict):
    clause_id: str
    quote: str = Field(min_length=10)


class RagDecision(Strict):
    """Agent 4 (Policy RAG): second opinion from the policy wording."""
    retrieved: list[str]
    covered: bool
    exclusions: list[str] = []
    cited: list[str] = Field(min_length=1)
    quotes: list[Quote] = Field(min_length=1)
    agrees: bool                        # same answer as the rules?


class Risk(Strict):
    """Agent 5 (Risk)."""
    score: float = Field(ge=0, le=1)
    flags: list[str] = []               # e.g. "fraud_flag: H-008-2"


class Payout(Strict):
    """Agent 6 (Payout)."""
    estimate: float
    eligible: float                     # min(estimate, limit)
    deductible: float
    net: float                          # eligible - deductible, never below 0
    capped: bool                        # estimate was above the limit


class Decision(Strict):
    """Router: the final answer."""
    outcome: Outcome
    reasons: list[str] = Field(min_length=1)
    cited: list[str] = []


class AuditEntry(Strict):
    """One line in the audit trail. Written for every agent attempt."""
    agent: str
    status: Literal["ok", "retry", "failed", "skipped"]
    time: datetime = Field(default_factory=now)
    input: dict = {}
    output: dict | None = None
    note: str = ""
    error: str | None = None


# ---- the state that flows through the graph -------------------------------------
class ClaimState(Strict):
    claim_id: str
    submission: Submission
    claim: Claim | None = None
    damage: Damage | None = None
    policy: PolicyDecision | None = None
    rag: RagDecision | None = None
    risk: Risk | None = None
    payout: Payout | None = None
    decision: Decision | None = None
    audit: list[AuditEntry] = []
    failures: list[str] = []            # agents that failed twice


# ---- stored data ------------------------------------------------------------------
class Clause(Strict):
    """One section of a product's policy wording (data/policy_docs/*.md)."""
    id: str
    type: Literal["coverage", "exclusion", "deductible", "limit", "condition", "general"]
    title: str
    text: str
    perils: list[IncidentType] = []     # coverage clauses
    exclusion: Circumstance | None = None  # exclusion clauses: which circumstance triggers it
    window_days: int | None = None      # reporting condition


class Cover(Strict):
    """One line of the customer's package."""
    peril: IncidentType
    limit: float
    deductible: float


class Policy(Strict):
    policy_id: str
    holder_id: str
    product_id: str
    product_name: str
    start: date
    end: date
    covers: list[Cover]
    clauses: list[Clause] = []          # filled from the product wording

    def cover_for(self, peril: str) -> Cover | None:
        return next((c for c in self.covers if c.peril == peril), None)

    def cover_id(self, peril: str) -> str:
        return f"{self.policy_id}-SCH-{peril.upper()}"

    def clause(self, clause_id: str) -> Clause | None:
        return next((c for c in self.clauses if c.id == clause_id), None)

    def all_ids(self) -> set[str]:
        return {c.id for c in self.clauses} | {self.cover_id(c.peril) for c in self.covers}
