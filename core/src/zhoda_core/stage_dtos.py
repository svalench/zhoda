"""Граница доверия: JSON модели не задаёт engine-owned state.

Инвентарь ask_json → DTO (ошибка парсинга ≠ переход):

| stage      | DTO              | обязательные поля                         | policy |
|------------|------------------|-------------------------------------------|--------|
| route      | ClassifyVote     | task_class ∈ TaskClass                    | invalid → не agreement, fallback debate |
| elicit     | ElicitVote       | ambiguities: list                         | skip модели |
| dedup      | DedupVote        | groups: list[list[int]]                   | skip, keep unique |
| pairwise   | SameVote         | same: StrictBool                          | invalid ≠ merge |
| position   | ModelPosition    | thesis, answer; claims без verified       | skip модели |
| synth/opp  | ModelPosition    | как position; action игнорируется         | keep prior / skip spawn |
| critique   | ModelCritique    | target_faction ∈ run IDs, flaw_type enum  | skip |
| closure    | ClosedVote       | closed: StrictBool                        | invalid ≠ CLOSED |
| revise     | ReviseVote       | changed: StrictBool                       | invalid ≠ revision |
| withdraw   | WithdrawVote     | withdraw: StrictBool                      | invalid ≠ withdraw |
| supersede  | AddressedVote    | addressed: StrictBool                     | invalid ≠ SUPERSEDED |
| switch     | SwitchVote       | switch: StrictBool                        | invalid ≠ move |
| agree      | AgreeVote        | all_agree: StrictBool                     | invalid ≠ unanimous |
| decision   | DecisionVote     | decision: str                             | fallback thesis |
| plan       | ModelPlan        | goal/steps; rejected_paths engine-owned   | empty plan + programmatic paths |
| appeal     | DecisionVote     | decision                                  | skip overwrite |
| bon        | PickBestVote     | index: StrictInt in range                 | no default-1 guess |
| blind      | BlindGradeVote   | committed: StrictBool; picked ∈ allowed   | ungraded, не incorrect |

Syntax repair в ask_json только чинит JSON, не committed/closed/verified.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError
from pydantic import field_validator

from .models import (
    Claim,
    Critique,
    FlawType,
    ObjectionStatus,
    PlanContract,
    PlanStep,
    Position,
    TaskClass,
    _none_if_nullish,
)

T = TypeVar("T", bound=BaseModel)

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+\S+|sk-[a-zA-Z0-9_-]{8,}|OPENROUTER_API_KEY\s*=\s*\S+)"
)

# Поля, которые модель не имеет права импортировать в engine state.
ENGINE_OWNED_FIELDS = frozenset(
    {
        "verified",
        "evidence_verified",
        "status",
        "action",
        "zhoda_reached",
        "consensus_strength",
        "decision_origin",
        "rebuttal",
        "id",
    }
)


class ParseFailure(BaseModel):
    """Структурная ошибка стадии. Превью без секретов; не success-transition."""

    stage: str
    error: str
    field: str = ""
    raw_preview: str = ""
    prompt_preview: str = ""


@dataclass(frozen=True)
class StageParse(Generic[T]):
    value: T | None = None
    error: ParseFailure | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None and self.error is None


class StageModel(BaseModel):
    """Untrusted input: extra keys drop; bool только JSON true/false."""

    model_config = ConfigDict(extra="ignore")


class ClassifyVote(StageModel):
    task_class: TaskClass


class ElicitItem(StageModel):
    ambiguity: str = ""
    why_it_matters: str = ""
    candidate_question: str = ""
    options: list[str] = Field(default_factory=list)


class ElicitVote(StageModel):
    ambiguities: list[ElicitItem]


class DedupVote(StageModel):
    groups: list[list[int]]


class SameVote(StageModel):
    same: StrictBool
    divergence: str = ""


class ModelClaim(StageModel):
    """Модель предлагает claim+URL+confidence. verified — только у движка."""

    claim: str
    evidence_url: str | None = None
    confidence: float = 0.5

    @field_validator("evidence_url", mode="before")
    @classmethod
    def _nullish_url(cls, value: object) -> str | None:
        return _none_if_nullish(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _finite_confidence(cls, value: object) -> float:
        if value is None:
            return 0.5
        return _strict_unit_float(value)


class ModelPosition(StageModel):
    thesis: str
    answer: str
    claims: list[ModelClaim] = Field(default_factory=list)
    falsifiability: str = ""
    confidence: float = 0.5

    @field_validator("confidence", mode="before")
    @classmethod
    def _finite_confidence(cls, value: object) -> float:
        if value is None:
            return 0.5
        return _strict_unit_float(value)


class ModelCritique(StageModel):
    target_faction: str
    flaw_type: FlawType
    claim: str
    specifics: str = ""
    evidence_url: str | None = None

    @field_validator("evidence_url", mode="before")
    @classmethod
    def _nullish_url(cls, value: object) -> str | None:
        return _none_if_nullish(value)


class ClosedVote(StageModel):
    closed: StrictBool


class WithdrawVote(StageModel):
    withdraw: StrictBool


class AddressedVote(StageModel):
    addressed: StrictBool


class SwitchVote(StageModel):
    switch: StrictBool
    convinced_by: str = ""


class AgreeVote(StageModel):
    all_agree: StrictBool


class ReviseVote(StageModel):
    thesis: str = ""
    answer: str = ""
    claims: list[ModelClaim] = Field(default_factory=list)
    falsifiability: str = ""
    confidence: float = 0.5
    changed: StrictBool
    change_note: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _finite_confidence(cls, value: object) -> float:
        if value is None:
            return 0.5
        return _strict_unit_float(value)


class DecisionVote(StageModel):
    decision: str


class ModelPlan(StageModel):
    goal: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_ambiguities: list[str] = Field(default_factory=list)


class PickBestVote(StageModel):
    index: StrictInt


class BlindGradeVote(StageModel):
    committed: StrictBool
    picked: str = ""
    reason: str = ""


def _strict_unit_float(value: object) -> float:
    """JSON number в [0, 1]; bool/NaN/Inf/стро — ошибка."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("numeric field must be a finite JSON number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("numeric field must be finite")
    if number < 0.0 or number > 1.0:
        raise ValueError("numeric field out of range")
    return number


def _preview(value: object, limit: int = 240) -> str:
    """Обрезка для аудита; ключи/токены вычищаются."""
    if value is None or value == "":
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = _SECRET_RE.sub("[redacted]", text)
    text = " ".join(text.split())
    return text[:limit]


def parse_stage(
    cls: type[T],
    data: object,
    *,
    stage: str,
    prompt: str = "",
) -> StageParse[T]:
    """Валидация untrusted dict. Невалидное не становится success-флагом."""
    prompt_preview = _preview(prompt)
    if not isinstance(data, dict):
        return StageParse(
            error=ParseFailure(
                stage=stage,
                error="not_object",
                raw_preview=_preview(data),
                prompt_preview=prompt_preview,
            )
        )
    try:
        return StageParse(value=cls.model_validate(data))
    except ValidationError as exc:
        errors = exc.errors()
        loc = ""
        err_type = "validation_error"
        if errors:
            loc = ".".join(str(part) for part in errors[0]["loc"])
            err_type = str(errors[0]["type"])
        return StageParse(
            error=ParseFailure(
                stage=stage,
                error=err_type,
                field=loc,
                raw_preview=_preview(data),
                prompt_preview=prompt_preview,
            )
        )


def engine_claims(items: list[ModelClaim]) -> list[Claim]:
    """URL без fetch = unverified_claim. self-asserted verified отбрасывается."""
    return [
        Claim(
            claim=item.claim,
            evidence_url=item.evidence_url,
            confidence=item.confidence,
            verified=False,
        )
        for item in items
        if item.claim.strip()
    ]


def position_from_model(
    data: object,
    *,
    alias: str,
    prompt: str = "",
) -> StageParse[Position]:
    parsed = parse_stage(ModelPosition, data, stage="position", prompt=prompt)
    raw = parsed.value
    if raw is None:
        return StageParse(error=parsed.error)
    if not raw.thesis.strip() or not raw.answer.strip():
        return StageParse(
            error=ParseFailure(
                stage="position",
                error="empty_position",
                field="thesis" if not raw.thesis.strip() else "answer",
                raw_preview=_preview(data),
                prompt_preview=_preview(prompt),
            )
        )
    return StageParse(
        value=Position(
            model=alias,
            thesis=raw.thesis,
            answer=raw.answer,
            claims=engine_claims(raw.claims),
            falsifiability=raw.falsifiability,
            confidence=raw.confidence,
            action=None,
        )
    )


def critique_from_model(
    data: object,
    *,
    author: str,
    allowed_factions: set[str],
    prompt: str = "",
) -> StageParse[Critique]:
    parsed = parse_stage(ModelCritique, data, stage="critique", prompt=prompt)
    raw = parsed.value
    if raw is None:
        return StageParse(error=parsed.error)
    if raw.target_faction not in allowed_factions:
        return StageParse(
            error=ParseFailure(
                stage="critique",
                error="unknown_faction_id",
                field="target_faction",
                raw_preview=_preview(raw.target_faction),
                prompt_preview=_preview(prompt),
            )
        )
    return StageParse(
        value=Critique(
            author_faction=author,
            target_faction=raw.target_faction,
            flaw_type=raw.flaw_type,
            claim=raw.claim,
            specifics=raw.specifics,
            evidence_url=raw.evidence_url,
            evidence_verified=False,
            status=ObjectionStatus.OPEN,
        )
    )


def plan_from_model(data: object, *, prompt: str = "") -> StageParse[PlanContract]:
    parsed = parse_stage(ModelPlan, data, stage="plan", prompt=prompt)
    raw = parsed.value
    if raw is None:
        return StageParse(error=parsed.error)
    return StageParse(
        value=PlanContract(
            goal=raw.goal,
            steps=raw.steps,
            constraints=raw.constraints,
            open_ambiguities=raw.open_ambiguities,
        )
    )
