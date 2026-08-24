"""Мусор/индекс/пустой ответ элиситации никогда не становится constraint."""

import pytest

from zhoda_core.elicitor import (
    ClarifyingQuestion,
    Elicitor,
    grounding_need,
    normalize_answer,
)


def _q(text: str, options: list[str] | None = None) -> ClarifyingQuestion:
    return ClarifyingQuestion(
        question=text,
        why_it_matters="changes the recommendation",
        options=options or [],
    )


def test_digit_maps_to_option_text() -> None:
    q = _q("Budget?", ["zero", "under $1k", "flexible"])
    assert normalize_answer(q, "2") == "under $1k"
    assert normalize_answer(q, "1") == "zero"


def test_exact_option_match_is_casefold() -> None:
    q = _q("Host?", ["Managed", "Self-hosted"])
    assert normalize_answer(q, "managed") == "Managed"


def test_pasted_option_list_is_unanswered() -> None:
    q = _q("Budget?", ["zero", "under $1k", "flexible"])
    assert normalize_answer(q, "zero | under $1k | flexible") is None
    assert normalize_answer(q, "zero under $1k") is None


def test_empty_and_out_of_range_are_unanswered() -> None:
    q = _q("Budget?", ["zero", "under $1k"])
    assert normalize_answer(q, "") is None
    assert normalize_answer(q, "   ") is None
    assert normalize_answer(q, "0") is None
    assert normalize_answer(q, "9") is None
    assert normalize_answer(q, "maybe") is None


def test_single_option_substring_counts_as_answer() -> None:
    q = _q("Kind of work?", ["technical improvement", "rewrite from scratch"])
    assert normalize_answer(q, "yes, technical improvement") == "technical improvement"


def test_free_text_without_options_is_kept() -> None:
    q = _q("Team size?")
    assert normalize_answer(q, "four engineers") == "four engineers"


def test_apply_answers_mixed_empty_neighbors_and_short_list() -> None:
    questions = [
        _q("Stack?", ["Postgres", "Kafka"]),
        _q("Budget absolutely zero?", ["yes", "no"]),
        _q("Team?", ["two", "four", "eight"]),
    ]
    value_map = Elicitor.apply_answers(questions, ["Postgres | Kafka", "", "2"])
    assert value_map.constraints == ["Q: Team? A: four"]
    assert value_map.open_ambiguities == [
        "Stack?",
        "Budget absolutely zero?",
    ]

    padded = Elicitor.apply_answers(questions, ["1"])
    assert padded.constraints == ["Q: Stack? A: Postgres"]
    assert padded.open_ambiguities == [
        "Budget absolutely zero?",
        "Team?",
    ]


def test_url_on_grounding_question_stays_open() -> None:
    q = _q("Which project are we evaluating?")
    value_map = Elicitor.apply_answers([q], ["https://github.com/org/zhoda"])
    assert value_map.constraints == []
    assert value_map.open_ambiguities == ["Which project are we evaluating?"]
    assert grounding_need(
        "Evaluate project X",
        [q],
        ["https://github.com/org/zhoda"],
        context="",
    )
    assert grounding_need("Evaluate project X", [q], ["https://x"], context="README") is None


@pytest.mark.asyncio
async def test_paraphrased_questions_are_asked_once() -> None:
    class QueueProvider:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            self.prompts.append(prompt)
            if "Group equivalent clarifying questions" in prompt:
                return {"groups": [[0, 1]]}
            question = (
                "Which project are we evaluating?"
                if model == "m1"
                else "What project is under review?"
            )
            return {
                "ambiguities": [
                    {
                        "ambiguity": "object unstated",
                        "why_it_matters": "changes the target",
                        "candidate_question": question,
                        "options": [],
                    }
                ]
            }

    provider = QueueProvider()
    elicitor = Elicitor(provider)  # type: ignore[arg-type]
    result = await elicitor.elicit(
        "Evaluate project X",
        ["m1", "m2"],
        mode="smart",
        dedup_model="cheap",
    )
    assert len(result.questions) == 1
    assert any("Group equivalent clarifying questions" in p for p in provider.prompts)


@pytest.mark.asyncio
async def test_auto_clarify_puts_questions_in_open_ambiguities_not_assumptions() -> None:
    """Round-12: auto-clarify не выдаёт вопросы за факты."""

    class QueueProvider:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            return {
                "ambiguities": [
                    {
                        "ambiguity": "What criteria will be used to evaluate success?",
                        "why_it_matters": "changes the verdict",
                        "candidate_question": "What criteria will be used to evaluate success?",
                        "options": [],
                    }
                ]
            }

    result = await Elicitor(QueueProvider()).elicit(  # type: ignore[arg-type]
        "Continue the project?",
        ["m1", "m2"],
        mode="auto-clarify",
    )
    assert result.questions == []
    assert result.value_map.assumptions == []
    assert "What criteria will be used to evaluate success?" in result.value_map.open_ambiguities
    assert result.all_questions


@pytest.mark.asyncio
async def test_questions_beyond_top3_land_in_open_ambiguities() -> None:
    """Хвост после top-3 не теряется и не становится assumption."""

    class QueueProvider:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            if "Group equivalent clarifying questions" in prompt:
                return {"groups": [[0], [1], [2], [3]]}
            return {
                "ambiguities": [
                    {
                        "ambiguity": f"a{i}",
                        "why_it_matters": "changes the pick",
                        "candidate_question": f"Question {i}?",
                        "options": [],
                    }
                    for i in range(1, 5)
                ]
            }

    result = await Elicitor(QueueProvider()).elicit(  # type: ignore[arg-type]
        "Which store?",
        ["m1", "m2"],
        mode="smart",
        dedup_model="cheap",
    )
    assert len(result.questions) == 3
    assert result.value_map.assumptions == []
    leftover = set(result.value_map.open_ambiguities)
    asked = {q.question for q in result.questions}
    assert leftover
    assert leftover.isdisjoint(asked)
    assert leftover | asked == {f"Question {i}?" for i in range(1, 5)}


@pytest.mark.asyncio
async def test_below_threshold_smart_lands_in_open_ambiguities() -> None:
    """Ниже порога в smart — тоже не assumptions."""

    class SplitProvider:
        def __init__(self) -> None:
            self.n = 0

        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            self.n += 1
            if self.n == 1:
                return {
                    "ambiguities": [
                        {
                            "ambiguity": "budget unstated",
                            "why_it_matters": "changes the pick",
                            "candidate_question": "What is the budget?",
                            "options": [],
                        }
                    ]
                }
            return {"ambiguities": []}

    result = await Elicitor(SplitProvider(), ambiguity_threshold=0.6).elicit(  # type: ignore[arg-type]
        "Which store?",
        ["m1", "m2"],
        mode="smart",
    )
    assert result.ambiguity_score < 0.6
    assert result.questions == []
    assert result.value_map.assumptions == []
    assert "What is the budget?" in result.value_map.open_ambiguities
