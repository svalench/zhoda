"""Pilot grader: gold sidecar + abstain + fixture judge. Без live HTTP."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from zhoda_core.benchmarks.judge import (
    BlindLlmJudge,
    GradeResult,
    GradeStatus,
    gold_label,
)
from zhoda_core.eval.gold import GoldRow, load_gold
from zhoda_core.eval.grader import apply_gold
from zhoda_core.eval.pilot import (
    FROZEN_MANIFEST,
    GOLD_JSONL,
    PUBLIC_JSONL,
    load_public_cases,
    public_to_benchmark,
)
from zhoda_core.eval.rescore import LIVE_G_V2, decide_rule, rescore_report

# Контрпример live G evd-001: парафраз keep, не exact label.
KEEP_INDEX_PARAPHRASE = (
    "Recommended (majority at cap, not zhoda): The index `idx_orders_created_at` "
    "should be retained because while it's unused in the current query plan due to "
    "the specific `id=1` filter, its potential value for broader query patterns "
    "and the negligible cost of retention on a small table outweigh the immediate "
    "lack of use.\n"
    "Dissent:\nIndex Droppers [synthetic opposition — no council model held this "
    "position]: The index idx_orders_created_at can be dropped because the table's "
    "small size minimizes the performance benefit of maintaining the index."
)

UNC_DELAY = (
    "Recommended (majority at cap, not zhoda): Migration of the repository to the "
    "new layout should not proceed this week until the owner names the repository."
)


def _gold(case_id: str) -> GoldRow:
    return load_gold(GOLD_JSONL)[case_id]


def _case(case_id: str):
    for item in load_public_cases(PUBLIC_JSONL):
        if item.id == case_id:
            return public_to_benchmark(item)
    raise AssertionError(case_id)


def _vote(*, committed: bool, picked: str, correct: bool | None = None) -> GradeResult:
    return GradeResult(
        status=GradeStatus.GRADED,
        committed=committed,
        picked_id=picked or None,
        correct=correct,
        reason="fixture",
    )


def test_pilot_ground_truth_is_not_gold_label() -> None:
    case = _case("evd-001")
    assert case.ground_truth == ""
    assert gold_label(case) == ""
    gold = _gold("evd-001")
    assert gold.expected_action == "Keep the index"


def test_paraphrase_keep_index_is_ungraded_without_judge() -> None:
    """Live G засчитал парафраз как incorrect — это баг грейдера, не miss модели."""
    gold = _gold("evd-001")
    case = _case("evd-001")
    grade = apply_gold(
        decision=KEEP_INDEX_PARAPHRASE,
        coverage="ok",
        gold=gold,
        options=case.answer_options,
    )
    assert grade.ungraded is True
    assert grade.action_correct is None
    assert grade.action_correct is not False
    assert grade.reason == "paraphrase_not_a_label"
    assert grade.source == "executable"


def test_paraphrase_keep_index_credits_with_judge_fixture() -> None:
    gold = _gold("evd-001")
    case = _case("evd-001")
    grade = apply_gold(
        decision=KEEP_INDEX_PARAPHRASE,
        coverage="ok",
        gold=gold,
        options=case.answer_options,
        vote=_vote(committed=True, picked="Keep the index", correct=True),
    )
    assert grade.ungraded is False
    assert grade.action_correct is True
    assert grade.chosen_action == "Keep the index"
    assert grade.source == "llm_judge"


def test_exact_label_credits_without_judge() -> None:
    gold = _gold("evd-001")
    case = _case("evd-001")
    grade = apply_gold(
        decision="Recommended (majority at cap, not zhoda): Keep the index.\nDissent:",
        coverage="ok",
        gold=gold,
        options=case.answer_options,
    )
    assert grade.ungraded is False
    assert grade.action_correct is True
    assert grade.chosen_action == "Keep the index"
    assert grade.source == "executable"


def test_drop_index_is_incorrect() -> None:
    gold = _gold("evd-001")
    case = _case("evd-001")
    grade = apply_gold(
        decision="Drop the index — the table is small.",
        coverage="ok",
        gold=gold,
        options=case.answer_options,
    )
    assert grade.ungraded is False
    assert grade.action_correct is False
    assert grade.chosen_action == "Drop the index"


def test_required_abstain_regex_is_correct() -> None:
    gold = _gold("unc-001")
    case = _case("unc-001")
    grade = apply_gold(
        decision="INSUFFICIENT_CONTEXT: the object of migration is unnamed.",
        coverage="ok",
        gold=gold,
        options=case.answer_options,
    )
    assert grade.abstain is True
    assert grade.action_correct is True
    assert grade.appropriate_abstention is True
    assert grade.ungraded is False


def test_required_abstain_delay_is_not_abstain() -> None:
    """«Не мигрировать на этой неделе» — действие, не abstain. Regex его не ловит."""
    gold = _gold("unc-001")
    case = _case("unc-001")
    grade = apply_gold(
        decision=UNC_DELAY,
        coverage="ok",
        gold=gold,
        options=case.answer_options,
    )
    assert grade.abstain is False
    assert grade.action_correct is False
    assert grade.appropriate_abstention is False


def test_required_abstain_credits_judge_pick() -> None:
    gold = _gold("unc-001")
    case = _case("unc-001")
    grade = apply_gold(
        decision=UNC_DELAY,
        coverage="ok",
        gold=gold,
        options=case.answer_options,
        vote=_vote(committed=True, picked="Abstain; insufficient context"),
    )
    assert grade.abstain is True
    assert grade.action_correct is True


def test_forbidden_ic_is_not_appropriate() -> None:
    gold = _gold("evd-001")
    case = _case("evd-001")
    grade = apply_gold(
        decision="INSUFFICIENT_CONTEXT: cannot decide whether to keep the index.",
        coverage="ok",
        gold=gold,
        options=case.answer_options,
    )
    assert grade.abstain is True
    assert grade.action_correct is False
    assert grade.appropriate_abstention is False


def test_ungraded_judge_stays_ungraded() -> None:
    gold = _gold("cfc-001")
    case = _case("cfc-001")
    vote = GradeResult(status=GradeStatus.UNGRADED, error="bool_type")
    grade = apply_gold(
        decision="Use PostgreSQL.",
        coverage="ok",
        gold=gold,
        options=case.answer_options,
        vote=vote,
    )
    assert grade.ungraded is True
    assert grade.action_correct is None
    assert grade.grade_error == "bool_type"


def test_unacceptable_claim_blocks_credit() -> None:
    gold = _gold("evd-001")
    case = _case("evd-001")
    grade = apply_gold(
        decision="Keep the index. the index is unused so drop it",
        coverage="ok",
        gold=gold,
        options=case.answer_options,
    )
    assert grade.unacceptable_hit is True
    assert grade.action_correct is False
    assert grade.ungraded is False


def test_allowed_alternative_credits() -> None:
    gold = GoldRow(
        id="x",
        expected_action="Keep the index",
        allowed_alternatives=("Retain idx_orders_created_at",),
        gold_conditions=(),
        unacceptable_claims=(),
        abstain_policy="forbidden",
        label_status="provisional",
        annotator="fixture",
    )
    grade = apply_gold(
        decision="Retain idx_orders_created_at",
        coverage="ok",
        gold=gold,
        options=("Keep the index", "Drop the index", "Retain idx_orders_created_at"),
    )
    assert grade.action_correct is True
    assert grade.chosen_action == "Retain idx_orders_created_at"


def test_failed_coverage_is_incorrect_not_ungraded() -> None:
    gold = _gold("evd-001")
    grade = apply_gold(
        decision="",
        coverage="failed",
        gold=gold,
        options=("Keep the index", "Drop the index"),
    )
    assert grade.ungraded is False
    assert grade.action_correct is False
    assert grade.reason == "failed_or_empty"


def test_uncommitted_judge_is_incorrect_not_ungraded() -> None:
    gold = _gold("cfc-001")
    case = _case("cfc-001")
    grade = apply_gold(
        decision="No zhoda. PostgreSQL Advocates: … Kafka: …",
        coverage="ok",
        gold=gold,
        options=case.answer_options,
        vote=_vote(committed=False, picked=""),
    )
    assert grade.ungraded is False
    assert grade.action_correct is False
    assert grade.reason == "uncommitted"


def test_sidecar_judge_prompt_uses_gold_not_empty_gt() -> None:
    import asyncio

    prompts: list[str] = []

    class Provider:
        async def ask_json(self, model: str, prompt: str, cache_key: str | None = None) -> dict:
            del model, cache_key
            prompts.append(prompt)
            return {"committed": True, "picked": "Keep the index", "reason": "retain"}

    case = _case("evd-001")
    gold = _gold("evd-001")
    judge = BlindLlmJudge(Provider(), "judge-model")  # type: ignore[arg-type]
    result = asyncio.run(
        judge.score_with_labels(
            case,
            KEEP_INDEX_PARAPHRASE,
            gold=gold.expected_action,
            allowed=case.answer_options,
        )
    )
    assert result.status is GradeStatus.GRADED
    assert result.correct is True
    assert result.picked_id == "Keep the index"
    assert "Keep the index" in prompts[0]
    assert "mode=" not in prompts[0]
    assert "producing system is hidden" in prompts[0]


def test_string_false_committed_is_ungraded() -> None:
    import asyncio

    class Provider:
        async def ask_json(self, model: str, prompt: str, cache_key: str | None = None) -> dict:
            del model, prompt, cache_key
            return {"committed": "false", "picked": "Keep the index", "reason": "hedge"}

    case = _case("evd-001")
    gold = _gold("evd-001")
    judge = BlindLlmJudge(Provider(), "j")  # type: ignore[arg-type]
    result = asyncio.run(
        judge.score_with_labels(
            case,
            "It depends.",
            gold=gold.expected_action,
            allowed=case.answer_options,
        )
    )
    assert result.status is GradeStatus.UNGRADED
    grade = apply_gold(
        decision="It depends.",
        coverage="ok",
        gold=gold,
        options=case.answer_options,
        vote=result,
    )
    assert grade.ungraded is True
    assert grade.action_correct is None


def test_xor_fixture_credits_postgres() -> None:
    gold = _gold("cfc-001")
    case = _case("cfc-001")
    decision = (
        "Recommended (majority at cap, not zhoda): With a small engineering team "
        "of 4, PostgreSQL is the more practical choice than Kafka."
    )
    grade = apply_gold(
        decision=decision,
        coverage="ok",
        gold=gold,
        options=case.answer_options,
    )
    assert grade.action_correct is True
    assert grade.chosen_action == "PostgreSQL"


def test_prompt_and_rubric_hashes_frozen() -> None:
    from zhoda_core.benchmarks.spec import hash_prompts, hash_rubric

    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    assert hash_prompts() == frozen["prompt_hash"]
    # Abstain regex v2 меняет rubric identity; freeze v1 не переписываем.
    assert hash_rubric() != frozen["rubric_hash"]


def test_pilot_py_still_does_not_import_grader_gold() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "zhoda_core" / "eval" / "pilot.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    assert not any("gold" in name.split(".")[-1] for name in imported)
    assert not any(name.endswith(".grader") for name in imported)
    assert not any(name.endswith(".grading") for name in imported)


def test_rescore_report_marks_paraphrase_ungraded_and_does_not_mutate_source(
    tmp_path,
) -> None:
    gold_map = load_gold(GOLD_JSONL)
    cases = {c.id: public_to_benchmark(c) for c in load_public_cases()}
    src_payload = {
        "schema": "zhoda.eval.live_g.v1",
        "n_cases": 1,
        "independent_validation": False,
        "results": [
            {
                "case_id": "evd-001",
                "mode": "zhoda",
                "kind": "evidence_required",
                "decision": KEEP_INDEX_PARAPHRASE,
                "coverage_status": "ok",
                "match": "request",
                "usd": 0.0,
                "action_correct": False,
                "ungraded": False,
            },
            {
                "case_id": "evd-001",
                "mode": "short_review",
                "kind": "evidence_required",
                "decision": KEEP_INDEX_PARAPHRASE,
                "coverage_status": "ok",
                "match": "request",
                "usd": 0.0,
                "action_correct": False,
                "ungraded": False,
            },
            {
                "case_id": "evd-001",
                "mode": "majority",
                "kind": "evidence_required",
                "decision": "Keep the index",
                "coverage_status": "ok",
                "match": "request",
                "usd": 0.0,
                "action_correct": True,
                "ungraded": False,
            },
        ],
    }
    src = tmp_path / "report.json"
    src.write_text(json.dumps(src_payload), encoding="utf-8")
    before = src.read_bytes()
    out = rescore_report(src_payload, gold_map, cases, judge_name="none")
    assert src.read_bytes() == before
    assert out["schema"] == LIVE_G_V2
    assert out["rescored_from"] == "zhoda.eval.live_g.v1"
    assert out["independent_validation"] is False
    by_mode = {r["mode"]: r for r in out["results"]}
    assert by_mode["zhoda"]["ungraded"] is True
    assert by_mode["zhoda"]["action_correct"] is None
    assert by_mode["zhoda"]["action_correct_heuristic"] is False
    assert by_mode["majority"]["action_correct"] is True
    assert by_mode["majority"]["ungraded"] is False


def test_rescore_cli_refuses_overwrite_and_writes_v2(tmp_path) -> None:
    from zhoda_core.eval.__main__ import main

    src = tmp_path / "report.json"
    src.write_text(
        json.dumps(
            {
                "schema": "zhoda.eval.live_g.v1",
                "n_cases": 1,
                "results": [
                    {
                        "case_id": "cfc-001",
                        "mode": "zhoda",
                        "kind": "counterfactual",
                        "decision": "PostgreSQL",
                        "coverage_status": "ok",
                        "match": "request",
                        "usd": 0.01,
                        "action_correct": False,
                    },
                    {
                        "case_id": "cfc-001",
                        "mode": "short_review",
                        "kind": "counterfactual",
                        "decision": "PostgreSQL",
                        "coverage_status": "ok",
                        "match": "request",
                        "usd": 0.005,
                        "action_correct": False,
                    },
                    {
                        "case_id": "cfc-001",
                        "mode": "majority",
                        "kind": "counterfactual",
                        "decision": "Kafka",
                        "coverage_status": "ok",
                        "match": "request",
                        "usd": 0.002,
                        "action_correct": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    before = src.read_bytes()
    assert main(["rescore-report", "--report", str(src), "--out", str(src)]) == 2
    assert src.read_bytes() == before
    dest = tmp_path / "report-v2.json"
    assert main(["rescore-report", "--report", str(src), "--out", str(dest)]) == 0
    assert src.read_bytes() == before
    v2 = json.loads(dest.read_text(encoding="utf-8"))
    assert v2["schema"] == LIVE_G_V2
    by_mode = {r["mode"]: r for r in v2["results"]}
    assert by_mode["zhoda"]["action_correct"] is True
    assert by_mode["majority"]["action_correct"] is False


def test_rule1_ungraded_share_is_inconclusive() -> None:
    rule = decide_rule(
        {"delta": 0.0, "mean_usd_short_review": 0.001, "mean_usd_oxford": 0.003},
        n_complete=31,
        bad_share=0.21,
    )
    assert rule["verdict"] == "inconclusive"
    assert rule["product_default"] == "debate"
