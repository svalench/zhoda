"""Пилот-грейдер v2: LLM action grade + keyword heuristic. Без сети."""

from __future__ import annotations

import asyncio
import json

from zhoda_core.benchmarks.quality import (
    decision_abstains,
    judge_visible_decision,
    quote_in_visible,
)
from zhoda_core.eval.gold import GoldRow, load_gold
from zhoda_core.eval.grading import (
    ACTION_GRADE_PROMPT,
    GRADER_VERSION,
    score_action_heuristic,
    score_action_llm,
    score_with_gold,
)
from zhoda_core.eval.pilot import GOLD_JSONL, PUBLIC_JSONL, load_public_cases
from zhoda_core.models import CostReport

# Реальные zhoda-decision из docs/live-runs/2026-09-06-g-pilot/report.json
DECISIONS: dict[str, str] = {
    "evd-001": (
        "Recommended (majority at cap, not zhoda): The index `idx_orders_created_at` "
        "should be retained because while it's unused in the current query plan due to "
        "the specific `id=1` filter, its potential value for broader query patterns "
        "and the negligible cost of retention on a small table outweigh the immediate "
        "lack of use.\n"
        "Dissent:\nIndex Droppers [synthetic opposition — no council model held this "
        "position]: The index idx_orders_created_at can be dropped because the table's "
        "small size minimizes the performance benefit of maintaining the index, and "
        "current usage patterns do not utilize it effectively. While this does not "
        "preclude future queries from benefiting from this index, its current lack of "
        "use combined with the small table size indicates minimal value and thus "
        "justifies removal to reduce maintenance overhead."
    ),
    "evd-003": (
        "The canonical object storage must remain in the S3 bucket as per ADR-014, "
        "with the NAS used solely as backup storage due to its lack of internet "
        "durability. This approach ensures data durability, availability, and "
        "accessibility, aligning with the established architecture and the explicit "
        "directives of ADR-014. Moving the object storage to the NAS share is not "
        "advised because the NAS does not provide the internet durability or "
        "reliability that the S3 bucket does. This decision stands unless the NAS is "
        "rearchitected to offer durability and availability comparable to S3 and "
        "ADR-014 is formally revised to reflect this change."
    ),
    "prm-001": (
        "Promotion of the replica should be deferred due to excessive replay lag. "
        "The current replica lag of 12 seconds exceeds the failover runbook's maximum "
        "allowed lag of 2 seconds for safe promotion, which poses risks of data "
        "inconsistency and loss. Promotion should only be considered once the "
        "replica's replay lag is reduced to 2 seconds or less, or if the failover "
        "runbook's threshold is updated to allow higher lag."
    ),
    "prm-002": (
        "Recommended (majority at cap, not zhoda): The replica should not be promoted "
        "due to excessive replication lag.\n"
        "Dissent:\nProactive Promoters: Given the evidence of a 12-second replica "
        "replay lag which exceeds the failover runbook's threshold of 2 seconds, "
        "promoting the replica now is not advisable despite the original thesis "
        "advocating immediate promotion at zero lag."
    ),
    "hol-min-002": (
        "Recommended (majority at cap, not zhoda): There is disagreement on whether "
        "to use a masked snapshot or to copy production data locally for analysis.\n"
        "Dissent:\nSecurity Maximalists [synthetic opposition — no council model held "
        "this position]: Copying the production database onto engineer laptops for "
        "debugging is not acceptable due to security risks; instead, engineers should "
        "debug using secure, centralized environments that limit data exposure."
    ),
    "unc-001": (
        "Recommended (majority at cap, not zhoda): Migration of the repository to the "
        "new layout should not proceed this week due to insufficient information and "
        "preparedness. However, we acknowledge that indefinite delay could forfeit "
        "early adoption benefits; therefore, we recommend using this week to conduct "
        "targeted information gathering and preparation activities so that migration "
        "can proceed in a subsequent week with reduced risk.\n"
        "Dissent:\nEarly Adopters [synthetic opposition — no council model held this "
        "position]: The repository migration to the new layout should proceed this "
        "week to leverage early benefits and accelerate adaptation."
    ),
    "unc-002": (
        "There is insufficient information to definitively confirm or deny that "
        "yesterday's deploy was the sole cause of the latency incident, due to missing "
        "critical correlating data such as timestamped deploy IDs, graphs, and service "
        "lists in the incident channel, which prevents a conclusive causal analysis. "
        "The absence of these elements obstructs correlation between deploy events and "
        "incident timing or impacted services. To overturn this conclusion, "
        "comprehensive incident data including timestamps, service lists, and deploy "
        "IDs would need to be obtained and demonstrate a clear causal correlation or "
        "lack thereof. No open objections currently challenge this determination."
    ),
}

# LLM-голоса, которые компетентный судья вернул бы по этим decision.
VOTES: dict[str, dict[str, object]] = {
    "evd-001": {
        "picked_id": "Keep the index",
        "committed": True,
        "quote": "The index `idx_orders_created_at` should be retained",
    },
    "evd-003": {
        "picked_id": "Keep S3",
        "committed": True,
        "quote": "must remain in the S3 bucket as per ADR-014",
    },
    "prm-001": {
        "picked_id": "Do not promote",
        "committed": True,
        "quote": "Promotion of the replica should be deferred",
    },
    "prm-002": {
        "picked_id": "Reject the premise; do not promote",
        "committed": True,
        "quote": "The replica should not be promoted due to excessive replication lag",
    },
    "hol-min-002": {
        "picked_id": "No; use a masked snapshot",
        "committed": False,
        "quote": "There is disagreement on whether to use a masked snapshot",
    },
    "unc-001": {
        "picked_id": "ABSTAIN",
        "committed": True,
        "quote": "due to insufficient information and preparedness",
    },
    "unc-002": {
        "picked_id": "ABSTAIN",
        "committed": True,
        "quote": "insufficient information to definitively confirm or deny",
    },
}

EXPECT_LLM_CORRECT = {
    "evd-001": True,
    "evd-003": True,
    "prm-001": True,
    "prm-002": True,
    "hol-min-002": False,
    "unc-001": True,
    "unc-002": True,
}

# Keyword-дефект: парафраз без exact label. unc-* после regex — True.
KEYWORD_FALSE_IDS = ("evd-001", "evd-003", "prm-001", "prm-002", "hol-min-002")
KEYWORD_TRUE_IDS = ("unc-001", "unc-002")


class FakeProvider:
    """Фикстурный судья. HTTP нет."""

    payload: dict[str, object]
    prompts: list[str]
    models: list[str]
    calls: int = 0

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = dict(payload)
        self.prompts = []
        self.models = []
        self.calls = 0

    async def ask_json(
        self, model: str, prompt: str, cache_key: str | None = None, **kwargs: object
    ) -> dict:
        del cache_key, kwargs
        self.models.append(model)
        self.prompts.append(prompt)
        self.calls += 1
        return dict(self.payload)

    def question_report(self) -> CostReport:
        return CostReport(
            requests=self.calls,
            tokens_in=self.calls * 12,
            tokens_out=9,
            usd=0.001 * self.calls,
            latency_s=0.02 * self.calls,
        )


def _gold(case_id: str) -> GoldRow:
    return load_gold(GOLD_JSONL)[case_id]


def _public(case_id: str):
    for item in load_public_cases(PUBLIC_JSONL):
        if item.id == case_id:
            return item
    raise AssertionError(case_id)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_seven_fixtures_llm_expected_keyword_still_false_on_paraphrase() -> None:
    for case_id, decision in DECISIONS.items():
        gold = _gold(case_id)
        public = _public(case_id)
        heuristic = score_action_heuristic(decision, "ok", gold, public.answer_options)
        provider = FakeProvider(VOTES[case_id])
        grade = _run(
            score_action_llm(
                provider,
                "openai/gpt-4o-mini",
                public,
                gold,
                decision,
                judge_overlap=("protocol_judge",),
            )
        )
        assert grade.action_correct is EXPECT_LLM_CORRECT[case_id], case_id
        assert grade.grade_status == "graded", case_id
        assert grade.grader_version == GRADER_VERSION
        assert grade.judge_overlap == ("protocol_judge",)
        if case_id == "hol-min-002":
            assert grade.committed is False
        if case_id in KEYWORD_FALSE_IDS:
            assert heuristic.action_correct is False, case_id
        if case_id in KEYWORD_TRUE_IDS:
            assert heuristic.action_correct is True, case_id
            assert heuristic.abstain is True, case_id


def test_score_with_gold_records_disagreement_and_evaluator_usage() -> None:
    case_id = "evd-001"
    gold = _gold(case_id)
    public = _public(case_id)
    provider = FakeProvider(VOTES[case_id])
    payload = _run(
        score_with_gold(
            DECISIONS[case_id],
            "ok",
            gold,
            public,
            provider=provider,
            model="openai/gpt-4o-mini",
            judge_overlap=("protocol_judge", "classifier"),
        )
    )
    assert payload["action_correct"] is True
    assert payload["action_correct_heuristic"] is False
    assert payload["heuristic_llm_disagree"] is True
    assert payload["grader_version"] == GRADER_VERSION
    assert "engine_usage" not in payload
    usage = payload["evaluator_usage"]
    assert usage["role"] == "evaluator"
    assert int(usage["requests"]) == 1
    assert "mode=" not in provider.prompts[0]
    assert "short_review" not in provider.prompts[0]
    assert public.question in provider.prompts[0]
    assert public.source_bundle["text"] in provider.prompts[0]


def test_overlay_scored_rows_collects_disagreements() -> None:
    from zhoda_core.eval.grading import overlay_scored_rows

    case_id = "evd-001"
    gold = _gold(case_id)
    public = _public(case_id)
    rows = [
        {
            "case_id": case_id,
            "mode": "zhoda",
            "decision": DECISIONS[case_id],
            "coverage_status": "ok",
        }
    ]
    scored, disagreements = _run(
        overlay_scored_rows(
            rows,
            {case_id: gold},
            {case_id: public},
            provider=FakeProvider(VOTES[case_id]),
            model="openai/gpt-4o-mini",
            judge_overlap=("protocol_judge",),
        )
    )
    assert scored[0]["action_correct"] is True
    assert scored[0]["action_correct_heuristic"] is False
    assert disagreements == [
        {
            "case_id": case_id,
            "mode": "zhoda",
            "heuristic": False,
            "llm": True,
            "picked_id": "Keep the index",
            "quote": "The index `idx_orders_created_at` should be retained",
        }
    ]


def test_action_grade_prompt_has_no_arm_format_hints() -> None:
    assert "Recommended (majority at cap" not in ACTION_GRADE_PROMPT
    assert "short_review" not in ACTION_GRADE_PROMPT
    assert "zhoda" not in ACTION_GRADE_PROMPT.casefold()


def test_abstain_regex_positives_and_negatives() -> None:
    assert decision_abstains("There is insufficient information to confirm.")
    assert decision_abstains("невозможно определить причину по этому фрагменту.")
    assert not decision_abstains("insufficient index coverage is not the issue; keep the index")
    dissent_only = (
        "Keep the index; the Seq Scan is expected.\n"
        "Dissent:\nIf more data were available we would need more information."
    )
    assert not decision_abstains(dissent_only)


def test_string_false_committed_is_ungraded() -> None:
    gold = _gold("evd-001")
    public = _public("evd-001")
    provider = FakeProvider({"committed": "false", "picked_id": "Keep the index", "quote": "hedge"})
    grade = _run(score_action_llm(provider, "j", public, gold, DECISIONS["evd-001"]))
    assert grade.grade_status == "ungraded"
    assert grade.action_correct is None
    assert grade.committed is not False
    assert grade.committed is not True
    assert grade.raw == {
        "committed": "false",
        "picked_id": "Keep the index",
        "quote": "hedge",
    }


def test_unknown_picked_id_is_ungraded() -> None:
    gold = _gold("evd-001")
    public = _public("evd-001")
    provider = FakeProvider({"committed": True, "picked_id": "invented-label", "quote": "nope"})
    grade = _run(score_action_llm(provider, "j", public, gold, DECISIONS["evd-001"]))
    assert grade.grade_status == "ungraded"
    assert grade.action_correct is None
    assert grade.picked_id is None


def test_appropriate_abstention_policies() -> None:
    public = _public("evd-001")
    forbidden = _gold("evd-001")
    required = _gold("unc-002")
    unc_public = _public("unc-002")
    abstain_text = "INSUFFICIENT_CONTEXT: cannot decide."
    pick_text = "Yes, yesterday's deploy caused it."
    forbidden_vote = {"picked_id": "ABSTAIN", "committed": True, "quote": "cannot decide"}
    required_vote = {
        "picked_id": "ABSTAIN",
        "committed": True,
        "quote": "insufficient information to definitively confirm or deny",
    }
    required_pick_vote = {
        "picked_id": "Abstain; insufficient context",
        "committed": True,
        "quote": "insufficient information to definitively confirm or deny",
    }

    forbidden_abstain = _run(
        score_action_llm(
            FakeProvider(forbidden_vote),
            "j",
            public,
            forbidden,
            abstain_text,
        )
    )
    assert forbidden_abstain.action_correct is False
    assert forbidden_abstain.appropriate_abstention is False
    heur_forbidden = score_action_heuristic(abstain_text, "ok", forbidden, public.answer_options)
    assert heur_forbidden.action_correct is False
    assert heur_forbidden.appropriate_abstention is False

    required_abstain = _run(
        score_action_llm(
            FakeProvider(required_vote),
            "j",
            unc_public,
            required,
            DECISIONS["unc-002"],
        )
    )
    assert required_abstain.action_correct is True
    assert required_abstain.appropriate_abstention is True
    heur_required = score_action_heuristic(
        DECISIONS["unc-002"],
        "ok",
        required,
        unc_public.answer_options,
    )
    assert heur_required.action_correct is True
    assert heur_required.appropriate_abstention is True

    required_pick = _run(
        score_action_llm(
            FakeProvider(required_pick_vote),
            "j",
            unc_public,
            required,
            DECISIONS["unc-002"],
        )
    )
    assert required_pick.grade_status == "graded"
    assert required_pick.picked_id == "Abstain; insufficient context"
    assert required_pick.action_correct is True
    assert required_pick.abstain is True
    assert required_pick.appropriate_abstention is True
    heur_pick = score_action_heuristic(pick_text, "ok", required, unc_public.answer_options)
    assert heur_pick.action_correct is False
    assert heur_pick.appropriate_abstention is False


def test_required_abstain_credits_gold_label() -> None:
    gold = _gold("unc-001")
    public = _public("unc-001")
    grade = _run(
        score_action_llm(
            FakeProvider(
                {
                    "committed": True,
                    "picked_id": "Abstain; insufficient context",
                    "quote": "insufficient information",
                }
            ),
            "j",
            public,
            gold,
            DECISIONS["unc-001"],
        )
    )
    assert grade.grade_status == "graded"
    assert grade.action_correct is True
    assert grade.abstain is True


def test_judge_prompt_omits_dissent_and_arm_markers() -> None:
    gold = _gold("evd-001")
    public = _public("evd-001")
    provider = FakeProvider({"committed": True, "picked_id": "Keep the index", "quote": "retained"})
    _run(score_action_llm(provider, "j", public, gold, DECISIONS["evd-001"]))
    prompt = provider.prompts[0]
    assert "Dissent:" not in prompt
    assert "Index Droppers" not in prompt
    assert "majority at cap" not in prompt
    visible = judge_visible_decision(DECISIONS["evd-001"])
    assert "majority at cap" not in visible
    assert "Dissent:" not in visible
    assert "should be retained" in visible


def test_lowercase_dissent_is_not_visible_to_judge() -> None:
    decision = (
        "Recommended (majority at cap, not zhoda): Keep the index.\n"
        "dissent:\nIndex Droppers: drop idx_orders_created_at; the table is small."
    )
    visible = judge_visible_decision(decision)
    assert "drop idx_orders_created_at" not in visible.casefold()
    assert "majority at cap" not in visible
    assert quote_in_visible("Keep the index", visible) is True


def test_quote_outside_visible_decision_is_ungraded() -> None:
    gold = _gold("evd-001")
    public = _public("evd-001")
    provider = FakeProvider(
        {
            "committed": True,
            "picked_id": "Keep the index",
            "quote": "drop idx_orders_created_at because the table is small",
        }
    )
    grade = _run(score_action_llm(provider, "j", public, gold, DECISIONS["evd-001"]))
    assert grade.grade_status == "ungraded"
    assert grade.grade_error == "quote_not_in_decision"
    assert grade.action_correct is None


def test_empty_quote_is_ungraded() -> None:
    gold = _gold("evd-001")
    public = _public("evd-001")
    provider = FakeProvider({"committed": True, "picked_id": "Keep the index", "quote": ""})
    grade = _run(score_action_llm(provider, "j", public, gold, DECISIONS["evd-001"]))
    assert quote_in_visible("", judge_visible_decision(DECISIONS["evd-001"])) is False
    assert grade.grade_status == "ungraded"
    assert grade.grade_error == "quote_not_in_decision"
    assert grade.action_correct is None


def test_minority_without_dissent_header_is_not_visible_pick() -> None:
    decision = (
        "Keep the index `idx_orders_created_at`.\n"
        "Minority:\nDrop the unused index; the table is small."
    )
    visible = judge_visible_decision(decision)
    assert "drop the unused index" not in visible.casefold()
    assert "keep the index" in visible.casefold()
    report = (
        "Keep the index `idx_orders_created_at`.\n"
        "Minority report:\nDrop the unused index; the table is small."
    )
    assert "drop the unused index" not in judge_visible_decision(report).casefold()


def test_no_zhoda_split_map_is_not_a_single_pick_span() -> None:
    decision = (
        "No zhoda (split).\n"
        "Response C: Keep the index `idx_orders_created_at`.\n"
        "Response B: Drop the unused index; the table is small."
    )
    visible = judge_visible_decision(decision)
    assert "drop the unused index" not in visible.casefold()
    assert "keep the index" not in visible.casefold()
    assert visible.casefold().startswith("no zhoda")
    same_line = "No zhoda (majority). PostgreSQL Advocates: Use PostgreSQL. Kafkaists: Use Kafka."
    same_visible = judge_visible_decision(same_line)
    assert "use kafka" not in same_visible.casefold()
    assert "use postgresql" not in same_visible.casefold()


def test_no_zhoda_split_quote_from_response_b_is_ungraded() -> None:
    gold = _gold("evd-001")
    public = _public("evd-001")
    decision = (
        "No zhoda (split).\n"
        "Response C: Keep the index `idx_orders_created_at`.\n"
        "Response B: Drop the unused index; the table is small."
    )
    provider = FakeProvider(
        {
            "committed": True,
            "picked_id": "Drop the index",
            "quote": "Drop the unused index; the table is small.",
        }
    )
    grade = _run(score_action_llm(provider, "j", public, gold, decision))
    assert grade.grade_status == "ungraded"
    assert grade.grade_error == "quote_not_in_decision"
    assert grade.action_correct is None


def test_prompt_hash_frozen_rubric_is_grader_v2() -> None:
    import inspect

    from zhoda_core.benchmarks.spec import hash_prompts, hash_rubric
    from zhoda_core.eval.pilot import FROZEN_MANIFEST

    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    assert hash_prompts() == frozen["prompt_hash"]
    assert hash_rubric() != frozen["rubric_hash"]
    assert "grading" not in inspect.getsource(hash_prompts)
    assert "ACTION_GRADE" not in inspect.getsource(hash_prompts)
