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
    taken without asking (smart elicitation, docs/01-core.md §2)."""

    goal: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    anti_goals: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_ambiguities: list[str] = Field(default_factory=list)


class Position(BaseModel):
    """A model's stance. `model` holds the ANONYMIZED alias during debate
    (protocol invariant: critics never see real model names)."""

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
    CLOSED = "closed"


class Critique(BaseModel):
    """A concrete charge with a typed flaw. The protocol tracks whether the
    objection was closed — switches are judged on UNCLOSED OBJECTIONS,
    never on persuasiveness (critique response §2)."""

    target_faction: str
    flaw_type: FlawType
    claim: str
    rebuttal: str = ""
    status: ObjectionStatus = ObjectionStatus.OPEN


class FactionSwitch(BaseModel):
    """Public faction change. Valid only with a cited convincing argument AND
    a failed rebuttal against an open objection (anti-capitulation)."""

    model: str
    from_faction: str
    to_faction: str
    convinced_by: str
    failed_rebuttal: str


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
    """Final output. `router_confidence` is exposed on purpose: a silent
    decision->vote misroute must be visible to the user (critique §1)."""

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
