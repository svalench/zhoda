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
    DEADLOCK = "deadlock"  # rounds cap exhausted while split (round-7 §8)


class ValueMap(BaseModel):
    """What the answer is checked against. `assumptions` are marked guesses
    taken without asking; `open_ambiguities` are questions that were raised
    but never answered (round-6 §4, round-7 §4)."""

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
    SUPERSEDED = "superseded"  # addressed by a platform revision


class Critique(BaseModel):
    """A concrete charge in the objection ledger. `author_faction` is assigned
    by the engine at registration (round-7 §3): a switch may only move TOWARD
    the faction that authored the convincing objection."""

    id: str = ""                              # assigned by DebateEngine.register_critique
    author_faction: str = ""                  # assigned by DebateEngine.run_round
    target_faction: str
    flaw_type: FlawType
    claim: str
    specifics: str = ""                       # mandatory for scope/values_mismatch
    rebuttal: str = ""
    status: ObjectionStatus = ObjectionStatus.OPEN


class FactionSwitch(BaseModel):
    """Public faction change. Valid only with BOTH halves (open objection by ID
    targeting the current faction + non-empty citation) AND a target equal to
    the objection's author faction (round-7 §3)."""

    model: str
    from_faction: str
    to_faction: str                           # must equal the objection's author_faction
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
    breakdown: dict[str, int] = Field(default_factory=dict)  # requests per stage


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
