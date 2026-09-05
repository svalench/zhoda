"""Protocol data models — mirror docs/01-core.md.

Schema changes require updating docs/01-core.md in the same commit
(Cursor rule 10-python-core).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

_NULLISH = {"null", "none", "", "undefined"}


def _none_if_nullish(value: object) -> str | None:
    """Модель часто пишет строку \"null\" вместо JSON null."""
    if value is None:
        return None
    text = str(value).strip()
    if text.casefold() in _NULLISH:
        return None
    return text


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

    def as_prompt_block(self) -> str:
        """Общий блок ответов пользователя для всех промптов рассуждения."""

        def _join(items: list[str]) -> str:
            return "; ".join(items) if items else "(none)"

        return (
            "User context (elicited — constraints ARE given facts, not hypotheses):\n"
            f"Goal: {self.goal or '(unspecified)'}\n"
            f"Success criteria: {_join(self.success_criteria)}\n"
            f"Constraints: {_join(self.constraints)}\n"
            f"Anti-goals: {_join(self.anti_goals)}\n"
            f"Unresolved ambiguities (NOT facts): {_join(self.open_ambiguities)}"
        )


def bind_user_context(prompt: str, user_context: str) -> str:
    """Приклеить user context в начало промпта — cache key должен покрывать блок."""
    block = user_context.strip()
    if not block:
        return prompt
    return f"{block}\n\n{prompt}"


class StatementKind(StrEnum):
    """Kind of a statement — orthogonal to whether it is verified."""

    USER_CONSTRAINT = "user_constraint"
    EMPIRICAL_CLAIM = "empirical_claim"
    PREFERENCE = "preference"
    ASSUMPTION = "assumption"


class VerificationStatus(StrEnum):
    UNKNOWN = "unknown"
    SUPPORTED = "supported"
    REFUTED = "refuted"


class PremiseRole(StrEnum):
    """How a trigger word sits in the question — not its truth value."""

    NONE = "none"
    BACKGROUND = "background"  # given that / since …, the asked action is elsewhere
    ASKED_PROPOSITION = "asked_proposition"  # why is / always-claim *is* the question


class ActionRelation(StrEnum):
    SAME = "same"
    REFINED = "refined"
    CHANGED = "changed"
    UNKNOWN = "unknown"


class Condition(BaseModel):
    """Run-scoped condition attached to an action. Minority keeps attribution."""

    condition_id: str
    text: str
    kind: StatementKind = StatementKind.ASSUMPTION
    verification: VerificationStatus = VerificationStatus.UNKNOWN
    attributed_to: str = ""
    material: bool = True


class ActionContract(BaseModel):
    """Engine-validatable recommended action. IDs come from the option list,
    never from word order or a hash of the whole prose."""

    action_id: str = "unresolved"
    label: str = ""
    conditions: list[Condition] = Field(default_factory=list)
    relation: ActionRelation = ActionRelation.UNKNOWN
    provenance: str = "unresolved"


class PremiseProbe(BaseModel):
    """Trigger words request a check; they do not mint truth."""

    role: PremiseRole = PremiseRole.NONE
    kind: StatementKind = StatementKind.ASSUMPTION
    verification: VerificationStatus = VerificationStatus.UNKNOWN
    trigger: str = ""
    text: str = ""


class Claim(BaseModel):
    """An argument with evidence discipline (values №1, fixed in round-10 §1):
    THREE labels, not two. A URL a model names from its head is an
    UNVERIFIED_CLAIM — visually closer to an assumption than to a source.
    `sourced` is reserved for user-provided or actually fetched+verified
    sources (`verified=True`). Otherwise the protocol would lend
    hallucinated links institutional weight — the exact false confidence
    this project is built against."""

    claim: str
    evidence_url: str | None = None
    confidence: float = 0.5
    verified: bool = False  # engine-owned: verifier or user source, never model JSON

    @field_validator("evidence_url", mode="before")
    @classmethod
    def _nullish_url(cls, value: object) -> str | None:
        return _none_if_nullish(value)

    @property
    def label(self) -> str:
        if self.evidence_url is None:
            return "assumption"
        return "sourced" if self.verified else "unverified_claim"


class Position(BaseModel):
    """A model's stance. `model` holds the ANONYMIZED alias during debate."""

    model: str
    thesis: str
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    falsifiability: str = ""
    confidence: float = 0.5
    action: ActionContract | None = None


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
    evidence_verified: bool = False  # engine-owned; model JSON cannot set this
    rebuttal: str = ""
    rebuttal_evidence_url: str | None = None
    status: ObjectionStatus = ObjectionStatus.OPEN

    @field_validator("evidence_url", "rebuttal_evidence_url", mode="before")
    @classmethod
    def _nullish_url(cls, value: object) -> str | None:
        return _none_if_nullish(value)


class FactionSwitch(BaseModel):
    """Public faction change: open objection by ID + citation quoting the
    objection claim + target IS the objection's author faction."""

    model: str
    from_faction: str
    to_faction: str
    convinced_by: str
    objection_id: str
    action_id: str = ""
    relation: ActionRelation = ActionRelation.CHANGED


class Disagreement(BaseModel):
    topic: str
    factions: list[str]
    summary: str


class AccountingStatus(StrEnum):
    """How trustworthy the usd total is. Unknown is not a silent $0."""

    EXACT = "exact"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class CheckStatus(StrEnum):
    REQUESTED = "requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckRecord(BaseModel):
    """Один required check. Denominator — requested, не ответившие."""

    check_id: str
    kind: str
    target: str
    status: CheckStatus = CheckStatus.REQUESTED
    reason: str = ""
    attempts: int = 0


class RunCompleteness(BaseModel):
    """Support among responders ≠ run completeness. Trusted ⇒ все
    requested checks succeeded или skipped; leftover requested — fail."""

    policy: str = ""
    checks: list[CheckRecord] = Field(default_factory=list)

    def register(self, kind: str, target: str) -> CheckRecord:
        rec = CheckRecord(check_id=f"{kind}:{target}", kind=kind, target=target)
        self.checks.append(rec)
        return rec

    def _get(self, kind: str, target: str) -> CheckRecord | None:
        want = f"{kind}:{target}"
        for rec in self.checks:
            if rec.check_id == want:
                return rec
        return None

    def succeed(self, kind: str, target: str, *, reason: str = "", attempts: int = 1) -> None:
        rec = self._get(kind, target)
        if rec is None:
            rec = self.register(kind, target)
        rec.status = CheckStatus.SUCCEEDED
        rec.reason = reason
        rec.attempts += attempts

    def fail(self, kind: str, target: str, reason: str, *, attempts: int = 1) -> None:
        rec = self._get(kind, target)
        if rec is None:
            rec = self.register(kind, target)
        rec.status = CheckStatus.FAILED
        rec.reason = reason
        rec.attempts += attempts

    def skip(self, kind: str, target: str, reason: str) -> None:
        rec = self._get(kind, target)
        if rec is None:
            rec = self.register(kind, target)
        rec.status = CheckStatus.SKIPPED
        rec.reason = reason

    def get(self, kind: str, target: str) -> CheckRecord | None:
        return self._get(kind, target)

    def finalize(self) -> None:
        """Leftover requested — отсутствие ответа, не успех. Quorum не сужаем."""
        for rec in self.checks:
            if rec.status is CheckStatus.REQUESTED:
                rec.status = CheckStatus.FAILED
                rec.reason = rec.reason or "missing"

    @property
    def trusted(self) -> bool:
        """Полный обязательный набор. Не делим на число ответивших."""
        if not self.checks:
            return True
        return all(
            rec.status in (CheckStatus.SUCCEEDED, CheckStatus.SKIPPED)
            for rec in self.checks
        )

    @property
    def requested_count(self) -> int:
        return len(self.checks)

    @property
    def succeeded_count(self) -> int:
        return sum(1 for rec in self.checks if rec.status is CheckStatus.SUCCEEDED)

    @property
    def failed_count(self) -> int:
        return sum(1 for rec in self.checks if rec.status is CheckStatus.FAILED)

    @property
    def skipped_count(self) -> int:
        return sum(1 for rec in self.checks if rec.status is CheckStatus.SKIPPED)


class RunContext(BaseModel):
    """Изоляция учёта одного run (F читает completeness; E — terminal)."""

    run_id: str
    completeness: RunCompleteness = Field(default_factory=RunCompleteness)
    reserved_usd: float = 0.0
    in_flight: int = 0
    attempts: int = 0
    overrun_usd: float = 0.0
    admissions_frozen: bool = False
    closed: bool = False
    usd_status: AccountingStatus = AccountingStatus.EXACT
    failures: list[str] = Field(default_factory=list)


class CostReport(BaseModel):
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_hits: int = 0
    usd: float = 0.0
    latency_s: float = 0.0
    breakdown: dict[str, int] = Field(default_factory=dict)
    cache_breakdown: dict[str, int] = Field(default_factory=dict)
    usd_status: AccountingStatus = AccountingStatus.EXACT
    overrun_usd: float = 0.0
    attempts: int = 0


class PlanStep(BaseModel):
    """One ticket for the cheap executor: nothing left to be inferred."""

    step: str
    goal: str
    hard_constraints: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    acceptance: str = ""


class RejectedPath(BaseModel):
    """A position rejected BY A REACHED CONSENSUS (round-10 §2): at split or
    deadlock nothing was rejected — there is an unresolved dispute, and it is
    not counted here."""

    path: str
    rejected_by: str
    why: str


class PlanContract(BaseModel):
    """The second render (values №2): a spec for a CHEAPER executor model.
    Rendered ONLY when zhoda was reached — a plan built on 'we did not
    decide' would hand the executor a spec founded on dissent (round-10 §2)."""

    goal: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    rejected_paths: list[RejectedPath] = Field(default_factory=list)
    open_ambiguities: list[str] = Field(default_factory=list)


class DecisionNode(BaseModel):
    """Explainability view (values №1): a tree, not a linear chronicle."""

    kind: str
    label: str
    detail: dict[str, object] = Field(default_factory=dict)
    children: list[DecisionNode] = Field(default_factory=list)


class Verdict(BaseModel):
    """Final output. `paths_rejected` is an honest programmatic count
    (round-10 §3) — the 'dead ends prevented' ROI metric waits for executor
    feedback, because we don't promise unmeasured numbers."""

    decision: str
    zhoda_reached: bool
    consensus_strength: ConsensusStrength
    protocol: Protocol
    # "council" | "appeal_without_consensus" | "majority_at_cap" | "degraded"
    decision_origin: str = "council"
    router_confidence: float = 1.0
    value_map: ValueMap = Field(default_factory=ValueMap)
    minority_report: str | None = None
    dissent_map: list[Disagreement] = Field(default_factory=list)
    switches: list[FactionSwitch] = Field(default_factory=list)
    rounds_taken: int = 0
    cost: CostReport = Field(default_factory=CostReport)
    transcript_id: str = ""
    plan_contract: PlanContract | None = None  # rendered ONLY on trusted zhoda
    paths_rejected: list[RejectedPath] = Field(default_factory=list)
    decision_tree: dict[str, object] = Field(default_factory=dict)
    escalated_to: str | None = None
    insufficient_context: bool = False  # объект оценки не задан — дебат не стартовал
    attributed_conditions: list[Condition] = Field(default_factory=list)
    run_id: str = ""
    completeness: RunCompleteness = Field(default_factory=RunCompleteness)
    degraded: bool = False  # advisory: не zhoda и не approved plan
