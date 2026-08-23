"""Protocol data models — mirror docs/01-core.md.

Schema changes require updating docs/01-core.md in the same commit
(Cursor rule 10-python-core).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class TaskClass(StrEnum):
    FACTUAL_LOOKUP = "factual_lookup"
    REASONING = "reasoning"
    DECISION = "decision"
    CODE_REVIEW = "code_review"
    CREATIVE = "creative"


class Protocol(StrEnum):
    VOTE = "vote"
    DEBATE = "debate"
    RED_TEAM = "red_team"


class ConsensusStrength(StrEnum):
    UNANIMOUS = "unanimous"
    MAJORITY = "majority"
    SPLIT = "split"
    DEADLOCK = "deadlock"


class ValueMap(BaseModel):
    """What the answer is checked against."""

    goal: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    anti_goals: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_ambiguities: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    """An argument with evidence discipline (values №1): without a source it
    is an OPINION, and every render must label it as such."""

    claim: str
    evidence_url: str | None = None  # None -> rendered as "assumption"
    confidence: float = 0.5

    @property
    def is_sourced(self) -> bool:
        return self.evidence_url is not None


class Position(BaseModel):
    """A model's stance. `model` holds the ANONYMIZED alias during debate.
    `claims` carry mandatory evidence fields (values №1)."""

    model: str
    thesis: str
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    falsifiability: str = ""
    confidence: float = 0.5


class FlawType(StrEnum):
    FACTUAL = "factual"
    LOGICAL = "logical"
    SCOPE = "scope"
    VALUES_MISMATCH = "values_mismatch"


class ObjectionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    SUPERSEDED = "superseded"


class Critique(BaseModel):
    """A concrete charge in the objection ledger."""

    id: str = ""
    author_faction: str = ""
    target_faction: str
    flaw_type: FlawType
    claim: str
    specifics: str = ""
    evidence_url: str | None = None
    rebuttal: str = ""
    rebuttal_evidence_url: str | None = None
    status: ObjectionStatus = ObjectionStatus.OPEN


class FactionSwitch(BaseModel):
    """Public faction change: open objection by ID + non-empty citation +
    target IS the objection's author faction."""

    model: str
    from_faction: str
    to_faction: str
    convinced_by: str
    objection_id: str


class Disagreement(BaseModel):
    topic: str
    factions: list[str]
    summary: str


class CostReport(BaseModel):
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_hits: int = 0
    usd: float = 0.0
    latency_s: float = 0.0
    breakdown: dict[str, int] = Field(default_factory=dict)


class PlanStep(BaseModel):
    """One ticket for the cheap executor: nothing left to be inferred."""

    step: str
    goal: str
    hard_constraints: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    acceptance: str = ""


class RejectedPath(BaseModel):
    """A path the council rejected — with WHO rejected it and WHY."""

    path: str
    rejected_by: str
    why: str


class PlanContract(BaseModel):
    """The second render (values №2): a spec for a CHEAPER executor model."""

    goal: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    rejected_paths: list[RejectedPath] = Field(default_factory=list)
    open_ambiguities: list[str] = Field(default_factory=list)


class DecisionNode(BaseModel):
    """Explainability view (values №1): a tree, not a linear chronicle."""

    kind: str
    label: str
    detail: dict = Field(default_factory=dict)
    children: list[DecisionNode] = Field(default_factory=list)


class Verdict(BaseModel):
    """Final output — renders twice: human report + plan contract."""

    decision: str
    zhoda_reached: bool
    consensus_strength: ConsensusStrength
    protocol: Protocol
    router_confidence: float = 1.0
    value_map: ValueMap = Field(default_factory=ValueMap)
    minority_report: str | None = None
    dissent_map: list[Disagreement] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)
    rounds_taken: int = 0
    cost: CostReport = Field(default_factory=CostReport)
    transcript_id: str = ""
    plan_contract: PlanContract | None = None
    dead_ends_prevented: int = 0
    decision_tree: dict = Field(default_factory=dict)
    escalated_to: str | None = None  # appellate model after deadlock (round-9 §4)
