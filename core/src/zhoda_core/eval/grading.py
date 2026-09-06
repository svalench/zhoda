"""Пилот-грейдер v2: keyword heuristic + LLM action grade.

Промпт судьи не содержит имя arm и не содержит
«Recommended (majority at cap...)». Protocol prompts не трогаем.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping, Protocol, Sequence

from pydantic import field_validator

from zhoda_core.benchmarks.judge import map_to_closed_label, pick_matches_gold
from zhoda_core.benchmarks.quality import (
    decision_abstains,
    extract_chosen_action,
    judge_visible_decision,
    quote_in_visible,
)
from zhoda_core.benchmarks.spy import usage_from_report
from zhoda_core.eval.gold import GoldRow
from zhoda_core.eval.pilot import PublicCase
from zhoda_core.providers.openrouter import make_cache_key
from zhoda_core.stage_dtos import ActionGradeVote, parse_stage

ABSTAIN_ID = "ABSTAIN"
GRADER_VERSION = "pilot-grader.v2"
FAILED_COVERAGE = frozenset({"failed", "skipped", "infeasible"})


class GradeProvider(Protocol):
    """ask_json судьи. question_report опционален."""

    async def ask_json(
        self,
        model: str,
        prompt: str,
        cache_key: str | None = None,
        **kwargs: object,
    ) -> object: ...


# Не входит в hash_prompts: это грейдер, не Oxford / EVIDENCE_CRITIQUE.
ACTION_GRADE_PROMPT = """Score the system's committed action against the closed label list.

Question:
{question}

Source:
{source}

Closed labels (pick exactly one):
{labels}

System answer (the producing system is hidden):
{decision}

Rules:
- picked_id MUST be exactly one closed label.
- committed=true only if the answer commits to that label as the action.
- The system answer is the recommendation only; dissent is already removed.
- If the answer abstains or treats the source as insufficient to decide,
  picked_id=ABSTAIN or the closed abstain label.
- quote: at most 200 characters copied from the system answer that justify the pick.
- Do not invent a label. Do not name the producing system.

ONLY JSON:
{{"picked_id": "<label>", "committed": true, "quote": "<span>"}}"""


@dataclass(frozen=True)
class HeuristicGrade:
    """Keyword-путь. Парафраз без exact label → action_correct=False."""

    chosen_action: str | None
    action_correct: bool | None
    abstain: bool
    appropriate_abstention: bool | None


@dataclass(frozen=True)
class ActionGrade:
    """LLM-путь. ungraded не равен incorrect."""

    picked_id: str | None
    committed: bool | None
    quote: str
    action_correct: bool | None
    appropriate_abstention: bool | None
    abstain: bool
    grade_status: str
    grade_error: str | None
    raw: object | None
    judge_model: str
    judge_overlap: tuple[str, ...]
    evaluator_usage: dict[str, object] = field(default_factory=dict)
    grader_version: str = GRADER_VERSION


def closed_labels(gold: GoldRow, options: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for item in (gold.expected_action, *gold.allowed_alternatives, *options, ABSTAIN_ID):
        key = (item or "").strip()
        fold = key.casefold()
        if not key or fold in seen:
            continue
        seen.add(fold)
        out.append(key)
    return tuple(out)


def _map_to_label(expected: str, labels: Sequence[str]) -> str:
    """Префикс до ';' побеждает полную форму gold в closed labels."""
    return map_to_closed_label(expected, labels)


def _credited(picked: str, gold: GoldRow, labels: Sequence[str]) -> bool:
    gold_id = _map_to_label(gold.expected_action, labels)
    if pick_matches_gold(picked, gold_id, labels):
        return True
    for alt in gold.allowed_alternatives:
        if pick_matches_gold(picked, _map_to_label(alt, labels), labels):
            return True
    return False


def _is_abstain_label(text: str) -> bool:
    folded = (text or "").strip().casefold()
    return folded == ABSTAIN_ID.casefold() or folded.startswith("abstain")


def _is_abstain_pick(picked: str, gold: GoldRow, labels: Sequence[str]) -> bool:
    """ABSTAIN или золотая/альтернативная метка-abstain (required gold)."""
    if picked == ABSTAIN_ID or _is_abstain_label(picked):
        return True
    gold_id = _map_to_label(gold.expected_action, labels)
    if gold.abstain_policy == "required" and pick_matches_gold(picked, gold_id, labels):
        return True
    for alt in gold.allowed_alternatives:
        mapped = _map_to_label(alt, labels)
        if _is_abstain_label(mapped) and pick_matches_gold(picked, mapped, labels):
            return True
    return False


def _appropriate(policy: str, abstain: bool, failed: bool) -> bool | None:
    if failed:
        return False
    if policy == "required":
        return abstain
    if policy == "forbidden":
        return not abstain
    return True if abstain else None


def score_action_heuristic(
    decision: str,
    coverage: str,
    gold: GoldRow,
    options: Sequence[str] = (),
) -> HeuristicGrade:
    """Старый keyword-путь: extract_chosen_action + pick_matches_gold."""
    failed = coverage in FAILED_COVERAGE or not (decision or "").strip()
    labels = closed_labels(gold, options)
    action_labels = tuple(item for item in labels if item != ABSTAIN_ID)
    abstain = decision_abstains(decision)
    appropriate = _appropriate(gold.abstain_policy, abstain, failed)
    if gold.abstain_policy == "required":
        return HeuristicGrade(
            chosen_action=None,
            action_correct=False if failed else bool(abstain),
            abstain=abstain,
            appropriate_abstention=appropriate,
        )
    if failed:
        return HeuristicGrade(None, False, False, False)
    if abstain:
        return HeuristicGrade(
            chosen_action=None,
            action_correct=False,
            abstain=True,
            appropriate_abstention=appropriate,
        )
    chosen = extract_chosen_action(decision, action_labels)
    if not chosen:
        return HeuristicGrade(None, False, False, appropriate)
    ok = _credited(chosen, gold, action_labels)
    return HeuristicGrade(chosen, ok, False, appropriate)


def _vote_cls(labels: tuple[str, ...]) -> type[ActionGradeVote]:
    allowed = labels

    class BoundVote(ActionGradeVote):
        @field_validator("picked_id")
        @classmethod
        def picked_closed(cls, value: str) -> str:
            if value not in allowed:
                raise ValueError("unknown_pick")
            return value

    BoundVote.__name__ = "ActionGradeVote"
    BoundVote.__qualname__ = "ActionGradeVote"
    return BoundVote


def _credit_llm(
    gold: GoldRow, picked: str, committed: bool, labels: Sequence[str]
) -> tuple[bool, bool, bool | None]:
    abstain = _is_abstain_pick(picked, gold, labels)
    if gold.abstain_policy == "required":
        return abstain, abstain, _appropriate(gold.abstain_policy, abstain, False)
    if abstain:
        appropriate = False if gold.abstain_policy == "forbidden" else True
        return False, True, appropriate
    credited = committed is True and _credited(picked, gold, labels)
    return credited, False, _appropriate(gold.abstain_policy, False, False)


def _snapshot_usage(provider: object) -> dict[str, object]:
    if not hasattr(provider, "question_report"):
        return {}
    report_fn = provider.question_report
    if not callable(report_fn):
        return {}
    return usage_from_report(report_fn(), role="evaluator")


def _num(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _usage_delta(before: dict[str, object], after: dict[str, object]) -> dict[str, object]:
    if not after:
        return {}
    if not before:
        return after
    out = dict(after)
    for key in ("requests", "cache_hits", "tokens_in", "tokens_out", "usd", "attempts"):
        delta = _num(after.get(key)) - _num(before.get(key))
        out[key] = delta if key == "usd" else int(delta)
    out["latency_s"] = max(0.0, _num(after.get("latency_s")) - _num(before.get("latency_s")))
    out["role"] = "evaluator"
    return out


async def score_action_llm(
    provider: GradeProvider,
    model: str,
    case_public: PublicCase,
    gold: GoldRow,
    decision: str,
    *,
    coverage: str = "ok",
    judge_overlap: Sequence[str] = (),
) -> ActionGrade:
    """Слепой action-grade. Arm name в промпт не попадает."""
    overlap = tuple(judge_overlap)
    failed = coverage in FAILED_COVERAGE or not (decision or "").strip()
    if failed:
        return ActionGrade(
            picked_id=None,
            committed=None,
            quote="",
            action_correct=False,
            appropriate_abstention=False,
            abstain=False,
            grade_status="graded",
            grade_error=None,
            raw=None,
            judge_model=model,
            judge_overlap=overlap,
            evaluator_usage={},
        )
    labels = closed_labels(gold, case_public.answer_options)
    visible = judge_visible_decision(decision)
    prompt = ACTION_GRADE_PROMPT.format(
        question=case_public.question,
        source=str(case_public.source_bundle.get("text") or ""),
        labels="\n".join(f"- {item}" for item in labels),
        decision=visible,
    )
    before = _snapshot_usage(provider)
    obj = await provider.ask_json(
        model,
        prompt,
        cache_key=make_cache_key("pilot-grade-v2", case_public.id, gold.expected_action, decision),
    )
    usage = _usage_delta(before, _snapshot_usage(provider))
    parsed = parse_stage(_vote_cls(labels), obj, stage="action_grade", prompt=prompt)
    if parsed.value is None:
        error = parsed.error.error if parsed.error else "invalid_grade"
        return ActionGrade(
            picked_id=None,
            committed=None,
            quote="",
            action_correct=None,
            appropriate_abstention=None,
            abstain=False,
            grade_status="ungraded",
            grade_error=error,
            raw=obj,
            judge_model=model,
            judge_overlap=overlap,
            evaluator_usage=usage,
        )
    vote = parsed.value
    quote = (vote.quote or "")[:200]
    if not quote_in_visible(quote, visible):
        return ActionGrade(
            picked_id=None,
            committed=None,
            quote=quote,
            action_correct=None,
            appropriate_abstention=None,
            abstain=False,
            grade_status="ungraded",
            grade_error="quote_not_in_decision",
            raw=obj,
            judge_model=model,
            judge_overlap=overlap,
            evaluator_usage=usage,
        )
    correct, abstain, appropriate = _credit_llm(gold, vote.picked_id, vote.committed, labels)
    return ActionGrade(
        picked_id=vote.picked_id,
        committed=vote.committed,
        quote=quote,
        action_correct=correct,
        appropriate_abstention=appropriate,
        abstain=abstain,
        grade_status="graded",
        grade_error=None,
        raw=obj,
        judge_model=model,
        judge_overlap=overlap,
        evaluator_usage=usage,
    )


def _grade_payload(heuristic: HeuristicGrade, grade: ActionGrade) -> dict[str, Any]:
    disagree = heuristic.action_correct != grade.action_correct
    picked = grade.picked_id
    return {
        "chosen_action": None if grade.abstain else picked,
        "action_correct": grade.action_correct,
        "action_correct_heuristic": heuristic.action_correct,
        "ungraded": grade.grade_status == "ungraded",
        "grade_status": grade.grade_status,
        "grade_error": grade.grade_error,
        "committed": grade.committed,
        "judge_picked": picked,
        "quote": grade.quote,
        "abstain": grade.abstain,
        "appropriate_abstention": grade.appropriate_abstention,
        "grader_version": GRADER_VERSION,
        "judge_model": grade.judge_model,
        "judge_overlap": list(grade.judge_overlap),
        "evaluator_usage": grade.evaluator_usage,
        "heuristic_llm_disagree": disagree,
    }


async def score_with_gold(
    decision: str,
    coverage: str,
    gold: GoldRow,
    case_public: PublicCase,
    *,
    provider: GradeProvider,
    model: str,
    judge_overlap: Sequence[str] = (),
) -> dict[str, Any]:
    """LLM action_correct + keyword heuristic. grader_version=pilot-grader.v2."""
    heuristic = score_action_heuristic(decision, coverage, gold, case_public.answer_options)
    grade = await score_action_llm(
        provider,
        model,
        case_public,
        gold,
        decision,
        coverage=coverage,
        judge_overlap=judge_overlap,
    )
    payload = _grade_payload(heuristic, grade)
    payload["label_status"] = gold.label_status
    payload["annotator"] = gold.annotator
    return payload


def _row_payload(row: object) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if is_dataclass(row) and not isinstance(row, type):
        return asdict(row)
    raise TypeError("row must be a mapping or dataclass")


async def overlay_scored_rows(
    rows: Sequence[object],
    gold_map: Mapping[str, GoldRow],
    public_by_id: Mapping[str, PublicCase],
    *,
    provider: GradeProvider,
    model: str,
    judge_overlap: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Наложить v2-грейд. Расхождения heuristic≠llm — отдельный список."""
    scored: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    if hasattr(provider, "begin_question") and callable(provider.begin_question):
        provider.begin_question()
    try:
        for row in rows:
            payload = _row_payload(row)
            case_id = str(payload["case_id"])
            gold = gold_map[case_id]
            public = public_by_id[case_id]
            overlay = await score_with_gold(
                decision=str(payload.get("decision") or ""),
                coverage=str(payload.get("coverage_status") or "ok"),
                gold=gold,
                case_public=public,
                provider=provider,
                model=model,
                judge_overlap=judge_overlap,
            )
            payload.update(overlay)
            payload["gold_after_attempts"] = True
            if overlay["heuristic_llm_disagree"]:
                disagreements.append(
                    {
                        "case_id": case_id,
                        "mode": payload.get("mode"),
                        "heuristic": overlay["action_correct_heuristic"],
                        "llm": overlay["action_correct"],
                        "picked_id": overlay.get("judge_picked"),
                        "quote": overlay.get("quote") or "",
                    }
                )
            scored.append(payload)
    finally:
        if hasattr(provider, "end_question") and callable(provider.end_question):
            provider.end_question()
    return scored, disagreements
