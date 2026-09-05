"""Strict stage DTOs: untrusted JSON не задаёт trust и state."""

import math

import pytest

from zhoda_core.models import ObjectionStatus, Protocol, ValueMap
from zhoda_core.positions import extract_positions
from zhoda_core.router import ProtocolRouter
from zhoda_core.stage_dtos import (
    ENGINE_OWNED_FIELDS,
    AgreeVote,
    ClosedVote,
    ParseFailure,
    ReviseVote,
    SwitchVote,
    critique_from_model,
    parse_stage,
    position_from_model,
)


def test_engine_owned_fields_are_listed() -> None:
    assert "verified" in ENGINE_OWNED_FIELDS
    assert "status" in ENGINE_OWNED_FIELDS
    assert "action" in ENGINE_OWNED_FIELDS


def test_strict_bool_rejects_strings_zero_one_null_missing() -> None:
    for payload in (
        {"all_agree": "false"},
        {"all_agree": "true"},
        {"all_agree": 0},
        {"all_agree": 1},
        {"all_agree": None},
        {},
    ):
        parsed = parse_stage(AgreeVote, payload, stage="agree")
        assert parsed.value is None
        assert parsed.error is not None
        assert isinstance(parsed.error, ParseFailure)


def test_strict_bool_accepts_json_true_false() -> None:
    yes = parse_stage(AgreeVote, {"all_agree": True}, stage="agree")
    no = parse_stage(AgreeVote, {"all_agree": False}, stage="agree")
    assert yes.value is not None and yes.value.all_agree is True
    assert no.value is not None and no.value.all_agree is False


def test_audit_preview_redacts_secrets() -> None:
    parsed = parse_stage(
        ClosedVote,
        {"closed": "false", "token": "Bearer sk-abcdefghijklmnop"},
        stage="closure",
        prompt="OPENROUTER_API_KEY=sk-live-secret",
    )
    assert parsed.error is not None
    assert "sk-abcdefghijklmnop" not in parsed.error.raw_preview
    assert "sk-live-secret" not in parsed.error.prompt_preview
    assert "[redacted]" in parsed.error.raw_preview + parsed.error.prompt_preview


def test_c8_model_verified_does_not_grant_sourced_trust() -> None:
    parsed = position_from_model(
        {
            "thesis": "Use a monolith",
            "answer": "Monolith for a four-person team",
            "claims": [
                {
                    "claim": "four engineers cannot operate a service mesh",
                    "evidence_url": "https://fake.example/paper",
                    "confidence": 0.9,
                    "verified": True,
                }
            ],
            "falsifiability": "if the team grows",
            "confidence": 0.9,
            "action": {"action_id": "opt:0", "label": "Monolith"},
            "status": "closed",
        },
        alias="Response A",
    )
    assert parsed.value is not None
    claim = parsed.value.claims[0]
    assert claim.verified is False
    assert claim.label == "unverified_claim"
    assert parsed.value.action is None


def test_nonfinite_confidence_is_rejected() -> None:
    parsed = parse_stage(
        ReviseVote,
        {
            "thesis": "Use PostgreSQL",
            "answer": "PG",
            "changed": True,
            "change_note": "x",
            "confidence": math.inf,
        },
        stage="revise",
    )
    assert parsed.value is None
    nan = parse_stage(
        ReviseVote,
        {
            "thesis": "Use PostgreSQL",
            "answer": "PG",
            "changed": True,
            "change_note": "x",
            "confidence": math.nan,
        },
        stage="revise",
    )
    assert nan.value is None


def test_unknown_faction_id_is_rejected() -> None:
    parsed = critique_from_model(
        {
            "target_faction": "Ghosts",
            "flaw_type": "factual",
            "claim": "PostgreSQL handles 50k RPS writes on a single node",
            "specifics": "",
            "evidence_url": None,
            "status": "closed",
            "evidence_verified": True,
        },
        author="Pragmatists",
        allowed_factions={"Pragmatists", "Throughputists"},
    )
    assert parsed.value is None
    assert parsed.error is not None
    assert parsed.error.error == "unknown_faction_id"


def test_known_faction_critique_strips_engine_status() -> None:
    parsed = critique_from_model(
        {
            "target_faction": "Pragmatists",
            "flaw_type": "factual",
            "claim": "PostgreSQL handles 50k RPS writes on a single node",
            "specifics": "",
            "evidence_url": "https://fake.example/src",
            "status": "closed",
            "evidence_verified": True,
        },
        author="Throughputists",
        allowed_factions={"Pragmatists", "Throughputists"},
    )
    assert parsed.value is not None
    assert parsed.value.status is ObjectionStatus.OPEN
    assert parsed.value.evidence_verified is False


def test_switch_string_false_is_not_a_move_flag() -> None:
    parsed = parse_stage(
        SwitchVote,
        {"switch": "false", "convinced_by": "quoted claim"},
        stage="switch",
    )
    assert parsed.value is None


@pytest.mark.asyncio
async def test_c8_extract_positions_drops_self_asserted_verified() -> None:
    class Prov:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            del model, prompt, kwargs
            return {
                "thesis": "Use a monolith",
                "answer": "Monolith",
                "claims": [
                    {
                        "claim": "four engineers cannot operate a mesh",
                        "evidence_url": "https://fake.example/paper",
                        "confidence": 0.9,
                        "verified": True,
                    }
                ],
                "falsifiability": "if headcount grows",
                "confidence": 0.9,
            }

    positions = await extract_positions(
        Prov(),  # type: ignore[arg-type]
        ["m1"],
        "Given that our team has four engineers, should we use a monolith?",
        ValueMap(),
        {"m1": "Response A"},
    )
    assert positions[0].claims[0].verified is False
    assert positions[0].claims[0].label == "unverified_claim"


@pytest.mark.asyncio
async def test_invalid_route_class_is_not_agreement() -> None:
    class Prov:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            del prompt, kwargs
            if model == "c1":
                return {"task_class": "decision"}
            return {"task_class": "not-a-class"}

    router = ProtocolRouter(Prov(), ("c1", "c2"))  # type: ignore[arg-type]
    route = await router.route("PostgreSQL or Kafka?")
    assert route.confidence == 0.0
    assert route.protocol is Protocol.DEBATE
