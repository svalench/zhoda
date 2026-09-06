"""Gold-aware grader пилота: sidecar + optional blind vote + abstain.

Не импортируется из eval.pilot. Protocol prompts не трогаем.
Парафраз без allowed-label — ungraded, не incorrect.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from zhoda_core.benchmarks.judge import (
    GradeResult,
    GradeStatus,
    map_to_closed_label,
    pick_matches_gold,
    resolve_picked_id,
)
from zhoda_core.benchmarks.quality import decision_abstains, extract_chosen_action
from zhoda_core.eval.gold import GoldRow

FAILED_COVERAGE = frozenset({"failed", "skipped", "infeasible"})


@dataclass(frozen=True)
class PilotGrade:
    """Оси пилота. ungraded не равен incorrect."""

    chosen_action: str | None
    action_correct: bool | None
    ungraded: bool
    grade_status: str
    grade_error: str | None
    committed: bool | None
    judge_picked: str | None
    abstain: bool
    appropriate_abstention: bool | None
    unacceptable_hit: bool
    source: str
    reason: str

    def as_overlay(self) -> dict[str, object]:
        return asdict(self)


def allowed_labels(gold: GoldRow, options: Sequence[str]) -> tuple[str, ...]:
    """answer_options + expected + alternatives, без дублей по fold."""
    out: list[str] = []
    seen: set[str] = set()
    for item in (*options, gold.expected_action, *gold.allowed_alternatives):
        key = (item or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return tuple(out)


def is_abstain_text(text: str) -> bool:
    return decision_abstains(text)


def is_abstain_label(label: str) -> bool:
    folded = (label or "").strip().casefold()
    if not folded:
        return False
    if folded.startswith("abstain"):
        return True
    return is_abstain_text(label)


def map_to_label(expected: str, labels: Sequence[str]) -> str:
    """Префикс до ';' побеждает полную форму gold в closed labels."""
    return map_to_closed_label(expected, labels)


def pick_is_credited(picked: str, gold: GoldRow, labels: Sequence[str]) -> bool:
    gold_id = map_to_label(gold.expected_action, labels)
    if pick_matches_gold(picked, gold_id, labels):
        return True
    for alt in gold.allowed_alternatives:
        alt_id = map_to_label(alt, labels)
        if pick_matches_gold(picked, alt_id, labels):
            return True
    return False


def claim_hit(decision: str, gold: GoldRow) -> bool:
    body = (decision or "").casefold()
    return any(claim.casefold() in body for claim in gold.unacceptable_claims if claim)


def detect_abstain(decision: str, gold: GoldRow, vote: GradeResult | None) -> bool:
    if vote is not None and vote.picked_id and is_abstain_label(vote.picked_id):
        return True
    if is_abstain_label(gold.expected_action) and is_abstain_text(decision):
        return True
    return is_abstain_text(decision)


def _grade(
    *,
    chosen: str | None,
    action_correct: bool | None,
    ungraded: bool,
    gold: GoldRow,
    vote: GradeResult | None,
    abstain: bool,
    bad: bool,
    source: str,
    reason: str,
    error: str | None = None,
) -> PilotGrade:
    appropriate: bool | None
    if gold.abstain_policy == "required":
        appropriate = bool(abstain)
    elif gold.abstain_policy == "forbidden":
        appropriate = not abstain
    else:
        appropriate = True if abstain else None
    status = "ungraded" if ungraded else "graded"
    return PilotGrade(
        chosen_action=chosen,
        action_correct=action_correct,
        ungraded=ungraded,
        grade_status=status,
        grade_error=error,
        committed=vote.committed if vote is not None else None,
        judge_picked=vote.picked_id if vote is not None else None,
        abstain=abstain,
        appropriate_abstention=appropriate,
        unacceptable_hit=bad,
        source=source,
        reason=reason,
    )


def apply_gold(
    *,
    decision: str,
    coverage: str,
    gold: GoldRow,
    options: Sequence[str] = (),
    vote: GradeResult | None = None,
) -> PilotGrade:
    """Sidecar gold после attempts. Judge optional. Ungraded остаётся ungraded."""
    labels = allowed_labels(gold, options)
    failed = coverage in FAILED_COVERAGE or not (decision or "").strip()
    bad = claim_hit(decision, gold)
    source = "llm_judge" if vote is not None else "executable"

    if failed:
        return _grade(
            chosen=None,
            action_correct=False,
            ungraded=False,
            gold=gold,
            vote=vote,
            abstain=False,
            bad=bad,
            source=source,
            reason="failed_or_empty",
        )

    if vote is not None and vote.status is GradeStatus.UNGRADED:
        return _grade(
            chosen=None,
            action_correct=None,
            ungraded=True,
            gold=gold,
            vote=vote,
            abstain=detect_abstain(decision, gold, vote),
            bad=bad,
            source="llm_judge",
            reason=vote.error or "ungraded_judge",
            error=vote.error,
        )

    abstain = detect_abstain(decision, gold, vote)
    if gold.abstain_policy == "required":
        return _grade(
            chosen=None,
            action_correct=bool(abstain) and not bad,
            ungraded=False,
            gold=gold,
            vote=vote,
            abstain=abstain,
            bad=bad,
            source=source,
            reason="required_abstain",
        )

    if abstain:
        credited = gold.abstain_policy == "allowed" and not bad
        return _grade(
            chosen=None,
            action_correct=credited,
            ungraded=False,
            gold=gold,
            vote=vote,
            abstain=True,
            bad=bad,
            source=source,
            reason="abstain",
        )

    if vote is not None and vote.status is GradeStatus.GRADED:
        picked = vote.picked_id
        if vote.committed is True and picked:
            ok = pick_is_credited(picked, gold, labels) and not bad
            return _grade(
                chosen=picked,
                action_correct=ok,
                ungraded=False,
                gold=gold,
                vote=vote,
                abstain=False,
                bad=bad,
                source="llm_judge",
                reason="committed_pick",
            )
        return _grade(
            chosen=picked,
            action_correct=False,
            ungraded=False,
            gold=gold,
            vote=vote,
            abstain=False,
            bad=bad,
            source="llm_judge",
            reason="uncommitted",
        )

    extracted = extract_chosen_action(decision, labels)
    label = resolve_picked_id(extracted, labels) if extracted else None
    if label:
        ok = pick_is_credited(label, gold, labels) and not bad
        return _grade(
            chosen=label,
            action_correct=ok,
            ungraded=False,
            gold=gold,
            vote=None,
            abstain=False,
            bad=bad,
            source="executable",
            reason="exact_label",
        )
    return _grade(
        chosen=extracted,
        action_correct=None,
        ungraded=True,
        gold=gold,
        vote=None,
        abstain=False,
        bad=bad,
        source="executable",
        reason="paraphrase_not_a_label",
        error="unresolved_pick",
    )
