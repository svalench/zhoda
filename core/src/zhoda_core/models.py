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
    """What the answer is checked against. `assumptions` are marked guesses
    taken without asking; `open_ambiguities` are questions that were raised
    but never answered (smart mode without a callback — round-6 §4)."""

    goal: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    anti_goals: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_ambiguities: list[str] = Field(default_factory=list)


class Position(BaseModel):
    """A model's stance. `model` holds the ANONYMIZED alias during debate
    (protocol invariant: critics never see real model names; see anonymize.py)."""

    model: str
    thesis: str
    answer: str
    arguments: list[str] = Field(default_factory=list)
    falsifiability: str = ""
    confidence: float = 0.5


class FlawType(StrEnum):
    FACTUAL = "factual"
    LOGICAL = "logical"
    SCOPE = "scope"
    VALUES_MISMATCH = "values_mismatch"


class ObjectionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"          # rebutted to the judges' satisfaction
    SUPERSEDED = "superseded"  # addressed by a platform revision (round-6 §1)


class Critique(BaseModel):
    """A concrete charge with a typed flaw, registered in the objection ledger
    with an ID. Quality gate: factual/logical need a concrete `claim`;
    scope/values_mismatch need `specifics`. Leaves the ledger via CLOSED
    (rebuttal) or SUPERSEDED (platform revision) — never lingers as a ghost."""

    id: str = ""                              # assigned by DebateEngine.register_critique
    target_faction: str
    flaw_type: FlawType
    claim: str
    specifics: str = ""                       # mandatory for scope/values_mismatch
    rebuttal: str = ""
    status: ObjectionStatus = ObjectionStatus.OPEN


class FactionSwitch(BaseModel):
    """Public faction change. Valid only against an OPEN objection referenced
    by `objection_id` AND a non-empty cited argument (both halves, round-4 §6).
    Decided AFTER the platform revision — against the updated platform."""

    model: str
    from_faction: str
    to_faction: str
    convinced_by: str                         # cited convincing argument
    objection_id: str                         # the unclosed objection behind it


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


class Verdict(BaseModel):
    """Final output. `router_confidence` is inter-model agreement of the two
    classifiers, exposed so a misroute is visible to the user."""

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
