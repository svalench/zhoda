"""Unit tests for zhoda_core.reputation."""

import math

from zhoda_core.reputation import (
    Domain,
    DomainEloMatrix,
    ReputationEvent,
    ReputationEventType,
    ReputationStorage,
    classify_domains,
)
from zhoda_core.reputation.matrix import DEFAULT_RATING, MIN_WEIGHT


def test_classify_code_question():
    vector = classify_domains("Should we split this monolith into microservices with gRPC APIs?")
    assert abs(sum(vector.values()) - 1.0) < 1e-9
    top = max(vector, key=vector.get)
    assert top == Domain.CODE_ARCHITECTURE


def test_classify_security_question():
    vector = classify_domains("Is JWT auth vulnerable to XSS token theft?")
    top = max(vector, key=vector.get)
    assert top == Domain.SECURITY_AUDIT


def test_classify_fallback_general():
    vector = classify_domains("Blorp zingle quax?")
    assert vector == {Domain.GENERAL: 1.0}


def test_llm_classifier_renormalized():
    vector = classify_domains(
        "anything",
        llm_classifier=lambda q: {"logic_math": 2.0, "factual_qa": 1.0},
    )
    assert math.isclose(vector[Domain.LOGIC_MATH], 2 / 3)
    assert math.isclose(vector[Domain.FACTUAL_QA], 1 / 3)


def test_elo_match_updates_only_target_domains():
    matrix = DomainEloMatrix()
    dv = {Domain.LOGIC_MATH: 1.0}
    matrix.record_match("a", "b", dv)
    assert matrix.get("a", Domain.LOGIC_MATH) > DEFAULT_RATING
    assert matrix.get("b", Domain.LOGIC_MATH) < DEFAULT_RATING
    assert matrix.get("a", Domain.SECURITY_AUDIT) == DEFAULT_RATING


def test_elo_zero_sum_per_domain():
    matrix = DomainEloMatrix()
    dv = {Domain.CODE_ARCHITECTURE: 0.7, Domain.SECURITY_AUDIT: 0.3}
    matrix.record_match("a", "b", dv)
    for d in dv:
        total = matrix.get("a", d) + matrix.get("b", d)
        assert math.isclose(total, 2 * DEFAULT_RATING)


def test_expected_score_bounds():
    assert DomainEloMatrix.expected_score(1000, 1000) == 0.5
    assert DomainEloMatrix.expected_score(1200, 1000) > 0.5


def test_vote_weights_sum_and_order():
    matrix = DomainEloMatrix()
    dv = {Domain.LOGIC_MATH: 1.0}
    matrix.record_match("strong", "weak", dv, k=64)
    weights = matrix.vote_weights(["strong", "weak", "new"], dv)
    assert math.isclose(sum(weights.values()), 1.0)
    assert weights["strong"] > weights["new"] > weights["weak"]
    assert all(w >= MIN_WEIGHT - 1e-9 for w in weights.values())


def test_vote_weights_empty():
    assert DomainEloMatrix().vote_weights([], {Domain.GENERAL: 1.0}) == {}


def test_event_deltas_sign():
    matrix = DomainEloMatrix()
    dv = {Domain.FACTUAL_QA: 1.0}
    matrix.record_event(ReputationEvent("m", ReputationEventType.CRITIQUE_ACCEPTED, dv))
    assert matrix.get("m", Domain.FACTUAL_QA) > DEFAULT_RATING
    matrix.record_event(ReputationEvent("m", ReputationEventType.FLAW_CONFIRMED, dv, magnitude=2))
    assert matrix.get("m", Domain.FACTUAL_QA) < DEFAULT_RATING


def test_serialization_roundtrip(tmp_path):
    matrix = DomainEloMatrix()
    matrix.record_match("a", "b", {Domain.LOGIC_MATH: 0.5, Domain.GENERAL: 0.5})
    storage = ReputationStorage(tmp_path / "rep.json")
    storage.save(matrix)
    loaded = storage.load()
    assert loaded.to_dict() == matrix.to_dict()


def test_storage_missing_file(tmp_path):
    storage = ReputationStorage(tmp_path / "absent" / "rep.json")
    matrix = storage.load()
    assert matrix.models() == []
