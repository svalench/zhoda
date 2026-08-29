"""Эвристика estimate: confirm_required, коридор запросов."""

from zhoda_mcp.estimate import estimate_cost

CFG = {
    "council": ["a", "b", "c"],
    "rounds_cap": 4,
    "budget_per_question_usd": 10.0,
    "prices": {"a": 0.001, "b": 0.001, "c": 0.001},
}


def test_debate_estimate_requires_confirm() -> None:
    out = estimate_cost(CFG, "debate")
    assert out["confirm_required"] is True
    assert out["requests_min"] == 15
    assert out["requests_max"] >= out["requests_min"]
    assert out["budget_usd"] == 10.0
    assert out["usd_max"] > 0


def test_vote_is_cheaper_than_debate() -> None:
    vote = estimate_cost(CFG, "vote")
    debate = estimate_cost(CFG, "debate")
    assert vote["requests_max"] < debate["requests_max"]
    assert vote["latency_s_max"] < debate["latency_s_max"]


def test_zero_budget_note() -> None:
    cfg = {**CFG, "budget_per_question_usd": 0.0}
    assert "free" in estimate_cost(cfg)["note"]
