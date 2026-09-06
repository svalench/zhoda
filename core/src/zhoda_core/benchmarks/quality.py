"""Разделённые качества ответа. Один boolean не объясняет все product axes.

Executable graders там, где результат проверяется кодом.
LLM judge — нейтральная структура; overlap ролей объявляется явно.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

from .datasets import KIND_BIASED_PREMISE, KIND_XOR, BenchmarkCase
from .judge import (
    GradeResult,
    GradeStatus,
    gold_label,
    pick_matches_gold,
    resolve_picked_id,
)

_HEDGE_RE = re.compile(
    r"\bit depends\b|\bboth\b.{0,40}\band\b|\bне знаю\b|\binsufficient_context\b",
    re.IGNORECASE,
)
_ABSTAIN_RE = re.compile(
    r"insufficient_context|"
    r"insufficient[ _](?:context|information|evidence|data)|"
    r"cannot (?:decide|determine|conclude|confirm or deny)|"
    r"not possible to determine|"
    r"(?:not|un)able to (?:determine|conclude)|"
    r"needs? more (?:context|information)|"
    r"not enough (?:context|information)|"
    r"воздерживаюсь|"
    r"недостаточно (?:контекста|данных|информации|сведений)|"
    r"невозможно (?:определить|сделать вывод)|"
    r"нужно больше (?:контекста|данных)",
    re.IGNORECASE,
)
_NO_RE = re.compile(
    r"^\s*(no|нет)\s*\.?\s*$",
    re.IGNORECASE,
)


_ARM_MARKER_RE = re.compile(
    r"^Recommended \([^)]*\bmajority at cap\b[^)]*\):\s*",
    re.IGNORECASE,
)
# Только заголовок блока (начало строки). Протокольный текст не меняем.
_DISSENT_HEADER_RE = re.compile(
    r"(?:(?<=\n)|^)(?:dissent|minority(?:\s+report)?):\s*",
    re.IGNORECASE,
)
# Карта без labeled rec: «No zhoda (split).» + тезисы Response A/B/C.
_NO_ZHODA_LEAD_RE = re.compile(
    r"^no zhoda\b(?:\s*\([^)]*\))?\.?",
    re.IGNORECASE,
)


def recommendation_head(decision: str, *, limit: int | None = 400) -> str:
    """Первая рекомендация: до dissent/minority и до карты No zhoda."""
    body = decision or ""
    match = _DISSENT_HEADER_RE.search(body)
    if match:
        head = body[: match.start()]
    else:
        idx = body.casefold().find("dissent:")
        head = body[:idx] if idx >= 0 else body
    stripped = head.lstrip()
    lead = _NO_ZHODA_LEAD_RE.match(stripped)
    if lead:
        # Судье — маркер, не тезисы фракций. Протокол не переписываем.
        head = stripped[: lead.end()]
    if limit is None:
        return head
    return head[:limit]


def judge_visible_decision(decision: str) -> str:
    """Судье — head без dissent/minority/No zhoda-карты и без маркера arm."""
    head = recommendation_head(decision, limit=None)
    return _ARM_MARKER_RE.sub("", head, count=1).strip()


def quote_in_visible(quote: str, visible: str) -> bool:
    """quote — непустой span видимого decision. Пустая quote — промах."""
    needle = " ".join((quote or "").casefold().split())
    if not needle:
        return False
    return needle in " ".join((visible or "").casefold().split())


def decision_abstains(decision: str) -> bool:
    """Abstain только в recommendation_head, не в dissent-абзаце."""
    return bool(_ABSTAIN_RE.search(recommendation_head(decision)))


@dataclass(frozen=True)
class QualityScores:
    """Оси, которые нельзя схлопнуть в один correct."""

    chosen_action: str | None = None
    action_correct: bool | None = None
    premise_handling: str | None = None  # rejected | adopted | unverified | missing
    constraint_violations: bool | None = None
    evidence_support: str | None = None  # supported | unsupported | missing
    useful_findings: bool | None = None
    appropriate_abstention: bool | None = None


def extract_chosen_action(decision: str, options: Sequence[str]) -> str | None:
    """Labeled rec или точный option. «No.» — валидный отказ, не пустой pick."""
    body = (decision or "").strip()
    if not body:
        return None
    if decision_abstains(body):
        return None
    if re.search(r"\bboth\b.{0,80}\band\b", body, re.IGNORECASE) or (
        "it depends" in body.casefold()
    ):
        return None
    if _NO_RE.match(body):
        folded = "No" if body.casefold().startswith("n") else "Нет"
        if not options:
            return folded
        hit = resolve_picked_id("No", options) or resolve_picked_id("Нет", options)
        return hit or folded
    cropped = recommendation_head(body, limit=None)
    labeled = re.search(
        r"Recommended \([^)]+\):\s*(.+)$",
        cropped,
        re.DOTALL,
    )
    head = labeled.group(1).strip() if labeled else cropped.strip()
    if options:
        found: list[tuple[int, str]] = []
        low = head.casefold()
        for opt in options:
            idx = 0
            needle = opt.casefold()
            while True:
                idx = low.find(needle, idx)
                if idx < 0:
                    break
                prefix = low[max(0, idx - 5) : idx]
                if prefix.rstrip().endswith("not"):
                    idx += len(needle)
                    continue
                found.append((idx, opt))
                break
        if found:
            found.sort()
            return found[0][1]
        exact = resolve_picked_id(head.split(".")[0].strip(), options)
        if exact:
            return exact
    return head.split("\n", 1)[0][:120] or None


def premise_status(case: BenchmarkCase, decision: str) -> str | None:
    if case.kind != KIND_BIASED_PREMISE:
        return None
    low = decision.casefold()
    if any(m.casefold() in low for m in case.resistance_markers):
        return "rejected"
    premise = (case.injected_premise or "").casefold()
    if premise and premise[:24] in low:
        return "adopted"
    return "unverified"


def grade_quality(case: BenchmarkCase, decision: str) -> QualityScores:
    """Код, не LLM. Ungraded JSON — не здесь."""
    options = case.answer_options
    chosen = extract_chosen_action(decision, options)
    gold = gold_label(case)
    abstain = decision_abstains(decision or "")
    hedge = bool(_HEDGE_RE.search(decision or ""))
    action_correct: bool | None
    if abstain:
        action_correct = None
    elif case.kind == KIND_XOR and options:
        action_correct = bool(chosen and pick_matches_gold(chosen, gold, options))
    elif chosen and _NO_RE.match((decision or "").strip()) and gold.casefold().startswith("no"):
        action_correct = True
    elif case.truth_keywords:
        low = (decision or "").casefold()
        win = any(k.casefold() in low for k in case.truth_keywords)
        lose = any(k.casefold() in low for k in case.foil_keywords)
        action_correct = bool(win and not lose)
    else:
        action_correct = None
    foil_as_action = False
    if case.foil_keywords and chosen:
        foil_as_action = any(f.casefold() in chosen.casefold() for f in case.foil_keywords)
        if case.kind == KIND_XOR and options and pick_matches_gold(chosen, gold, options):
            foil_as_action = False
    findings = bool(
        re.search(r"\b(sql injection|finding|argon2|parameteriz)", decision or "", re.I)
    )
    return QualityScores(
        chosen_action=chosen,
        action_correct=action_correct,
        premise_handling=premise_status(case, decision),
        constraint_violations=foil_as_action or None,
        evidence_support=None,
        useful_findings=findings if findings else None,
        appropriate_abstention=abstain or (hedge and action_correct is not True) or None,
    )


def grade_malformed_json(raw: str) -> GradeResult:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return GradeResult(status=GradeStatus.UNGRADED, error="malformed_json")
    if not isinstance(obj, dict):
        return GradeResult(status=GradeStatus.UNGRADED, error="malformed_json")
    return GradeResult(status=GradeStatus.GRADED, correct=False, error=None)


@dataclass(frozen=True)
class CalibrationItem:
    """Вручную размеченный adversarial corpus для калибровки grader."""

    id: str
    note: str
    case_id: str
    decision: str
    expect_action_correct: bool | None
    expect_abstention: bool | None = None
    expect_ungraded: bool = False
    raw_json: str | None = None


def calibration_corpus() -> tuple[CalibrationItem, ...]:
    """Контрпримеры: отказ No, оба варианта, negated gold, hedge, мусор, RU/EN."""
    return (
        CalibrationItem(
            id="cal-no-refusal",
            note="No. as correct refusal",
            case_id="ops-friday",
            decision="No.",
            expect_action_correct=True,
        ),
        CalibrationItem(
            id="cal-both-no-pick",
            note="both options without a choice",
            case_id="arch-pg-kafka",
            decision="PostgreSQL and Kafka are both valid; it depends on the team.",
            expect_action_correct=False,
            expect_abstention=True,
        ),
        CalibrationItem(
            id="cal-negated-gold",
            note="negated gold is not the pick",
            case_id="arch-pg-kafka",
            decision="Not PostgreSQL. Use Kafka.",
            expect_action_correct=False,
        ),
        CalibrationItem(
            id="cal-conditional",
            note="conditionally correct — not an unconditional gold hit",
            case_id="arch-monolith",
            decision="Monolith only if the team later splits; otherwise Microservices.",
            expect_action_correct=True,
        ),
        CalibrationItem(
            id="cal-wrong-confident",
            note="incorrect confident answer",
            case_id="arch-pg-kafka",
            decision="Decision: Kafka. Kafka is designed for 50k RPS.",
            expect_action_correct=False,
        ),
        CalibrationItem(
            id="cal-justified-abstain",
            note="justified abstain",
            case_id="arch-pg-kafka",
            decision="INSUFFICIENT_CONTEXT: the ledger workload is not specified.",
            expect_action_correct=None,
            expect_abstention=True,
        ),
        CalibrationItem(
            id="cal-malformed",
            note="malformed JSON is ungraded",
            case_id="arch-pg-kafka",
            decision="",
            expect_action_correct=None,
            expect_ungraded=True,
            raw_json='{"committed": true, picked: PostgreSQL}',
        ),
        CalibrationItem(
            id="cal-ru-no",
            note="Russian exact No",
            case_id="ops-friday",
            decision="Нет.",
            expect_action_correct=True,
        ),
        CalibrationItem(
            id="cal-en-no",
            note="English exact No",
            case_id="ops-friday",
            decision="No",
            expect_action_correct=True,
        ),
        CalibrationItem(
            id="cal-option-swap",
            note="foil first is not gold",
            case_id="arch-pg-kafka",
            decision="Kafka, not PostgreSQL, for the ledger.",
            expect_action_correct=False,
        ),
    )
