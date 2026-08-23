"""End-to-end tests on a SCRIPTED provider (consumable script, deterministic
via alias_seed, transcripts in tmp_path).

Round-10: the plan contract renders ONLY on zhoda; paths_rejected counts
rejections by a reached consensus; an appeal carries decision_origin.
"""

import json

import pytest

from zhoda_core.anonymize import make_aliases
from zhoda_core.engine import ZhodaEngine
from zhoda_core.models import ConsensusStrength, Protocol
from zhoda_core.progress import ProgressEvent
from zhoda_core.providers.openrouter import OpenRouterProvider

COUNCIL = ["m1", "m2", "m3"]
JUDGES = ("j1", "j2")
CLASSIFIERS = ("m1", "m2")

PG = "Use PostgreSQL (simple, sufficient)"
KF = "Use Kafka (built for throughput)"
PG_REVISED = "Use PostgreSQL with a partitioning plan for write scaling"

PLAN = {
    "goal": "pick the ledger store",
    "steps": [
        {
            "step": "prototype PostgreSQL",
            "goal": "validate write path",
            "hard_constraints": ["single node first"],
            "forbidden_paths": [],
            "acceptance": "50k RPS in staging",
        }
    ],
    "constraints": ["team of four"],
    "open_ambiguities": [],
}

DECISION = {"decision": "Use PostgreSQL with a partitioning plan for write scaling"}


def position(thesis: str) -> dict:
    return {
        "thesis": thesis,
        "answer": f"Answer: {thesis}",
        "claims": [{"claim": "key argument", "evidence_url": None, "confidence": 0.7}],
        "falsifiability": "if load grows",
        "confidence": 0.7,
    }


class ScriptedProvider(OpenRouterProvider):
    """Ordered (model, patterns, response) script; each match is CONSUMED."""

    def __init__(self, script: list[tuple[str | None, tuple[str, ...], object]]) -> None:
        super().__init__(api_key="test")
        self.script = list(script)

    async def complete(self, model, prompt, **kwargs):
        for i, (want_model, patterns, response) in enumerate(self.script):
            if (want_model is None or want_model == model) and all(p in prompt for p in patterns):
                self.script.pop(i)
                return response if isinstance(response, str) else json.dumps(response)
        raise AssertionError(f"no scripted response: model={model} prompt={prompt[:80]}")

    async def ask_json(self, model, prompt, **kwargs):
        return json.loads(await self.complete(model, prompt, **kwargs))


def make_engine(provider: ScriptedProvider, tmp_path, **kwargs) -> ZhodaEngine:
    defaults = {
        "chairman": "j1",
        "judges": JUDGES,
        "router_classifiers": CLASSIFIERS,
        "alias_seed": 42,
        "transcripts_dir": str(tmp_path),
    }
    council = kwargs.pop("council", COUNCIL)
    return ZhodaEngine(provider, council, **(defaults | kwargs))


def two_faction_opening(aliases: dict[str, str]) -> list:
    """Две фракции 1v1 — majority 2/3 не срабатывает, deadlock реален."""
    a1, a2 = aliases["m1"], aliases["m2"]
    return [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(KF)),
        (
            None,
            ("Position A thesis: Use PostgreSQL", "Position B thesis: Use Kafka"),
            {"same": False, "divergence": "simplicity vs throughput"},
        ),
        (None, ("Name each faction",), {a1: "Pragmatists", a2: "Throughputists"}),
    ]


def opening_script(aliases: dict[str, str]) -> list:
    a1, a2 = aliases["m1"], aliases["m2"]
    return [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(KF)),
        ("m3", ("independent structured stance",), position(PG)),
        (
            None,
            ("Position A thesis: Use PostgreSQL", "Position B thesis: Use Kafka"),
            {"same": False, "divergence": "simplicity vs throughput"},
        ),
        (
            None,
            ("Position A thesis: Use PostgreSQL", "Position B thesis: Use PostgreSQL"),
            {"same": True, "divergence": ""},
        ),
        (
            None,
            ("Position A thesis: Use Kafka",),
            {"same": False, "divergence": "simplicity vs throughput"},
        ),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {a1: "Pragmatists", a2: "Throughputists"}),
    ]


@pytest.mark.asyncio
async def test_debate_loop_with_platform_revision(tmp_path) -> None:
    aliases = make_aliases(COUNCIL, seed=42)
    script = opening_script(aliases) + [
        (
            None,
            ('You represent faction "Pragmatists"', "Produce ONE"),
            {
                "target_faction": "Throughputists",
                "flaw_type": "factual",
                "claim": "Kafka adds operational complexity the team cannot staff",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (
            None,
            ('You represent faction "Throughputists"', "Produce ONE"),
            {
                "target_faction": "Pragmatists",
                "flaw_type": "scope",
                "claim": "PostgreSQL at 50k RPS writes needs a partitioning plan",
                "specifics": "no write-scaling story beyond a single node",
                "evidence_url": None,
            },
        ),
        (
            None,
            ("devil's advocate",),
            {
                "target_faction": "Pragmatists",
                "flaw_type": "logical",
                "claim": "the platform ignores read replicas as a simpler scaling path",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely", "operational complexity"), "We accept the point."),
        (None, ("Rebut it concisely", "partitioning"), "Partitioning is solved."),
        (None, ("Rebut it concisely", "read replicas"), "Replicas do not help writes."),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "read replicas"), {"closed": True}),
        (None, ("Did the rebuttal", "read replicas"), {"closed": True}),
        (
            "m1",
            ("Revise your platform",),
            {
                "thesis": PG_REVISED,
                "answer": f"Answer: {PG_REVISED}",
                "claims": [{"claim": "partitioning plan", "evidence_url": None, "confidence": 0.8}],
                "falsifiability": "if writes explode",
                "confidence": 0.8,
                "changed": True,
                "change_note": "added partitioning",
            },
        ),
        (None, ("Revise your platform",), {"changed": False, "change_note": "rejected"}),
        ("m2", ("withdraw your objection",), {"withdraw": True}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path, stability_rounds=1)
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is True
    assert "partitioning" in verdict.decision
    assert verdict.decision != f"Answer: {PG_REVISED}"
    assert verdict.minority_report and "Kafka" in verdict.minority_report
    assert verdict.switches == []
    assert verdict.plan_contract is not None  # zhoda -> plan renders
    assert len(verdict.paths_rejected) >= 1  # the Kafka minority, rejected
    assert verdict.decision_tree["children"]
    assert verdict.cost.breakdown


@pytest.mark.asyncio
async def test_stability_rule_blocks_a_flip(tmp_path) -> None:
    pair = ["m1", "m2"]
    aliases = make_aliases(pair, seed=42)
    critique_pg = {
        "target_faction": "Pragmatists",
        "flaw_type": "scope",
        "claim": "PostgreSQL at 50k RPS writes needs a partitioning plan",
        "specifics": "no write-scaling story beyond a single node",
        "evidence_url": None,
    }
    critique_kf = {
        "target_faction": "Throughputists",
        "flaw_type": "factual",
        "claim": "Kafka adds operational complexity the team cannot staff",
        "specifics": "",
        "evidence_url": None,
    }
    script = two_faction_opening(aliases) + [
        (None, ('You represent faction "Pragmatists"', "Produce ONE"), critique_kf),
        (None, ('You represent faction "Throughputists"', "Produce ONE"), critique_pg),
        (None, ("devil's advocate",), critique_pg),
        (None, ("Rebut it concisely", "operational complexity"), "Accepted."),
        (None, ("Rebut it concisely", "partitioning"), "Solved."),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("theses of all factions",), {"all_agree": True}),
        (None, ("theses of all factions",), {"all_agree": True}),
        (None, ('You represent faction "Pragmatists"', "Produce ONE"), critique_kf),
        (None, ('You represent faction "Throughputists"', "Produce ONE"), critique_pg),
        (None, ("devil's advocate",), critique_pg),
        (None, ("Rebut it concisely", "operational complexity"), "Accepted."),
        (None, ("Rebut it concisely", "partitioning"), "Solved."),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (
            "m1",
            ("Revise your platform",),
            {
                "thesis": PG,
                "answer": f"Answer: {PG}",
                "claims": [],
                "falsifiability": "if load grows",
                "confidence": 0.7,
                "changed": False,
                "change_note": "objection rejected",
            },
        ),
        ("m1", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        ("m3", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
    ]
    engine = make_engine(
        ScriptedProvider(script),
        tmp_path,
        council=pair,
        stability_rounds=2,
        rounds_cap=2,
    )
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is False
    assert verdict.rounds_taken == 2
    assert verdict.plan_contract is None  # no zhoda -> no plan (round-10 §2)
    assert verdict.paths_rejected == []  # nothing was rejected


@pytest.mark.asyncio
async def test_deadlock_on_rounds_cap(tmp_path) -> None:
    pair = ["m1", "m2"]
    aliases = make_aliases(pair, seed=42)
    critique_pg = {
        "target_faction": "Pragmatists",
        "flaw_type": "scope",
        "claim": "PostgreSQL at 50k RPS writes needs a partitioning plan",
        "specifics": "no write-scaling story beyond a single node",
        "evidence_url": None,
    }
    critique_kf = {
        "target_faction": "Throughputists",
        "flaw_type": "factual",
        "claim": "Kafka adds operational complexity the team cannot staff",
        "specifics": "",
        "evidence_url": None,
    }
    round_script = [
        (None, ('You represent faction "Pragmatists"', "Produce ONE"), critique_kf),
        (None, ('You represent faction "Throughputists"', "Produce ONE"), critique_pg),
        (None, ("devil's advocate",), critique_pg),
        (None, ("Rebut it concisely", "operational complexity"), "Accepted."),
        (None, ("Rebut it concisely", "partitioning"), "Solved."),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (
            "m1",
            ("Revise your platform",),
            {
                "thesis": PG,
                "answer": f"Answer: {PG}",
                "claims": [],
                "falsifiability": "if load grows",
                "confidence": 0.7,
                "changed": False,
                "change_note": "objection rejected",
            },
        ),
        ("m1", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        ("m3", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
    ]
    script = two_faction_opening(aliases) + round_script + round_script
    engine = make_engine(
        ScriptedProvider(script),
        tmp_path,
        council=pair,
        stability_rounds=2,
        rounds_cap=2,
    )
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is False
    assert verdict.consensus_strength == ConsensusStrength.DEADLOCK
    assert verdict.minority_report
    assert verdict.paths_rejected == []  # deadlock rejected nothing


@pytest.mark.asyncio
async def test_deadlock_escalates_to_appeal(tmp_path) -> None:
    """Deadlock + escalation -> the appellate judge decides, LABELED."""
    pair = ["m1", "m2"]
    aliases = make_aliases(pair, seed=42)
    critique_pg = {
        "target_faction": "Pragmatists",
        "flaw_type": "scope",
        "claim": "PostgreSQL at 50k RPS writes needs a partitioning plan",
        "specifics": "no write-scaling story beyond a single node",
        "evidence_url": None,
    }
    critique_kf = {
        "target_faction": "Throughputists",
        "flaw_type": "factual",
        "claim": "Kafka adds operational complexity the team cannot staff",
        "specifics": "",
        "evidence_url": None,
    }
    round_script = [
        (None, ('You represent faction "Pragmatists"', "Produce ONE"), critique_kf),
        (None, ('You represent faction "Throughputists"', "Produce ONE"), critique_pg),
        (None, ("devil's advocate",), critique_pg),
        (None, ("Rebut it concisely", "operational complexity"), "Accepted."),
        (None, ("Rebut it concisely", "partitioning"), "Solved."),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": False}),
        (
            "m1",
            ("Revise your platform",),
            {
                "thesis": PG,
                "answer": f"Answer: {PG}",
                "claims": [],
                "falsifiability": "if load grows",
                "confidence": 0.7,
                "changed": False,
                "change_note": "objection rejected",
            },
        ),
        ("m1", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        ("m2", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
    ]
    script = (
        two_faction_opening(aliases)
        + round_script
        + round_script
        + [
            (
                "j3",
                ("appellate judge",),
                {
                    "decision": "Use PostgreSQL with a partitioning plan — appeal",
                    "winning_arguments": ["operational simplicity"],
                },
            ),
        ]
    )
    engine = make_engine(
        ScriptedProvider(script),
        tmp_path,
        council=pair,
        stability_rounds=2,
        rounds_cap=2,
        escalation_model="j3",
    )
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.consensus_strength == ConsensusStrength.DEADLOCK
    assert verdict.escalated_to == "j3"
    assert verdict.decision_origin == "appeal_without_consensus"  # labeled fiat
    assert "appeal" in verdict.decision
    assert verdict.plan_contract is None  # still no zhoda -> still no plan


@pytest.mark.asyncio
async def test_red_team_attacks_unanimous_platform(tmp_path) -> None:
    """One faction (everyone said 'fine') -> the devil's advocate still attacks."""
    aliases = make_aliases(COUNCIL, seed=42)
    a1 = aliases["m1"]
    script = [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {}),
        (
            None,
            ("devil's advocate",),
            {
                "target_faction": a1,
                "flaw_type": "logical",
                "claim": "the platform never considers write amplification on SSDs",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely",), "Covered by the storage engine."),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path)
    verdict = await engine.deliberate(
        "Is this storage layer fine?",
        force_protocol=Protocol.RED_TEAM,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is True
    assert verdict.rounds_taken == 1
    events = engine.transcripts.read(verdict.transcript_id)
    rounds = [e for e in events if e.get("stage") == "round"]
    assert rounds and rounds[0]["critiques"]  # the advocate fired at unanimity


@pytest.mark.asyncio
async def test_progress_emits_protocol_stages(tmp_path) -> None:
    """on_progress is optional; no-clarify debate stages include round, not elicit."""
    aliases = make_aliases(COUNCIL, seed=42)
    a1 = aliases["m1"]
    script = [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {}),
        (
            None,
            ("devil's advocate",),
            {
                "target_faction": a1,
                "flaw_type": "logical",
                "claim": "the platform never considers write amplification on SSDs",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely",), "Covered by the storage engine."),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    events: list[ProgressEvent] = []
    engine = make_engine(ScriptedProvider(script), tmp_path)
    await engine.deliberate(
        "Is this storage layer fine?",
        force_protocol=Protocol.RED_TEAM,
        clarify_mode="no-clarify",
        on_progress=events.append,
    )
    done = [e.stage for e in events if e.done]
    assert done[:4] == ["route", "positions", "factions", "round"]
    assert "verdict" in done
    assert "elicit" not in {e.stage for e in events}


@pytest.mark.asyncio
async def test_smart_mode_without_callback_degrades(tmp_path) -> None:
    ambiguity = {
        "ambiguities": [
            {
                "ambiguity": "target latency SLO unstated",
                "why_it_matters": "changes the storage choice",
                "candidate_question": "What latency SLO?",
                "options": ["<10ms", "<100ms"],
            }
        ],
    }
    script = [
        ("m1", ("Do NOT answer",), ambiguity),
        ("m2", ("Do NOT answer",), ambiguity),
        ("m3", ("Do NOT answer",), ambiguity),
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path)
    verdict = await engine.deliberate(
        "Which database for the ledger?",
        force_protocol=Protocol.VOTE,
        clarify_mode="smart",
    )
    assert verdict.value_map.open_ambiguities
    assert verdict.zhoda_reached is True


@pytest.mark.asyncio
async def test_state_does_not_leak_between_questions(tmp_path) -> None:
    aliases = make_aliases(COUNCIL, seed=42)
    q1_script = opening_script(aliases) + [
        (
            None,
            ('You represent faction "Pragmatists"', "Produce ONE"),
            {
                "target_faction": "Throughputists",
                "flaw_type": "factual",
                "claim": "Kafka adds operational complexity the team cannot staff",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (
            None,
            ('You represent faction "Throughputists"', "Produce ONE"),
            {
                "target_faction": "Pragmatists",
                "flaw_type": "scope",
                "claim": "PostgreSQL at 50k RPS writes needs a partitioning plan",
                "specifics": "no write-scaling story beyond a single node",
                "evidence_url": None,
            },
        ),
        (
            None,
            ("devil's advocate",),
            {
                "target_faction": "Pragmatists",
                "flaw_type": "logical",
                "claim": "the platform ignores read replicas as a simpler scaling path",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely",), "Accepted."),
        (None, ("Rebut it concisely",), "Solved."),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    q2_script = [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Spawn an opposition faction",), position("Use Redis")),
        (None, ("Name each faction",), {}),
        (
            None,
            ("You represent faction", "Produce ONE"),
            {
                "target_faction": "devils_advocate",
                "flaw_type": "logical",
                "claim": "the alternative ignores operational cost of a new datastore",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (
            None,
            ("You represent faction", "Produce ONE"),
            {
                "target_faction": aliases["m1"],
                "flaw_type": "logical",
                "claim": "the majority never considers write amplification on SSDs",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (
            None,
            ("devil's advocate",),
            {
                "target_faction": aliases["m1"],
                "flaw_type": "logical",
                "claim": "the platform never considers write amplification on SSDs",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely",), "ok"),
        (None, ("Rebut it concisely",), "ok"),
        (None, ("Rebut it concisely",), "ok"),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        ("m1", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        ("m2", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        ("m3", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("theses of all factions",), {"all_agree": True}),
        (None, ("theses of all factions",), {"all_agree": True}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(
        ScriptedProvider(q1_script + q2_script),
        tmp_path,
        stability_rounds=1,
    )
    first = await engine.deliberate(
        "PostgreSQL or Kafka?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert first.dissent_map
    second = await engine.deliberate(
        "Redis or Memcached?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert second.zhoda_reached is True
    assert second.switches == []
    assert second.minority_report is None
    assert second.transcript_id != first.transcript_id
    q1_topics = {d.topic for d in first.dissent_map}
    assert q1_topics.isdisjoint({d.topic for d in second.dissent_map})


@pytest.mark.asyncio
async def test_streak_does_not_leak(tmp_path) -> None:
    def one_question_script() -> list:
        return [
            ("m1", ("independent structured stance",), position(PG)),
            ("m2", ("independent structured stance",), position(PG)),
            ("m3", ("independent structured stance",), position(PG)),
            (None, ("Synthesize the shared platform",), position(PG)),
            (None, ("Name each faction",), {}),
            (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
            (None, ("PLAN CONTRACT",), PLAN),
        ]

    engine = make_engine(
        ScriptedProvider(one_question_script() + one_question_script()),
        tmp_path,
        stability_rounds=2,
        devils_advocate=False,
    )
    first = await engine.deliberate(
        "PostgreSQL?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    second = await engine.deliberate(
        "Redis?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert first.zhoda_reached and first.rounds_taken == 0
    assert second.zhoda_reached and second.rounds_taken == 0
    events = engine.transcripts.read(first.transcript_id)
    assert any(e.get("fast_pass") == "unanimity_at_birth" for e in events)


@pytest.mark.asyncio
async def test_advocate_spawns_opposition_on_unanimous_debate(tmp_path) -> None:
    aliases = make_aliases(COUNCIL, seed=42)
    a1 = aliases["m1"]
    script = [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Spawn an opposition faction",), position("Use Kafka instead")),
        (None, ("Name each faction",), {a1: "Pragmatists", "devils_advocate": "Skeptics"}),
        (
            None,
            ('You represent faction "Pragmatists"', "Produce ONE"),
            {
                "target_faction": "Skeptics",
                "flaw_type": "logical",
                "claim": "the alternative ignores operational cost of a new datastore",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (
            None,
            ('You represent faction "Skeptics"', "Produce ONE"),
            {
                "target_faction": "Pragmatists",
                "flaw_type": "logical",
                "claim": "the majority never considers write amplification on SSDs",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (
            None,
            ("devil's advocate",),
            {
                "target_faction": "Pragmatists",
                "flaw_type": "logical",
                "claim": "the platform never considers write amplification on SSDs",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely",), "ok"),
        (None, ("Rebut it concisely",), "ok"),
        (None, ("Rebut it concisely",), "ok"),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        (None, ("Did the rebuttal",), {"closed": True}),
        ("m1", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        ("m2", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        ("m3", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("theses of all factions",), {"all_agree": True}),
        (None, ("theses of all factions",), {"all_agree": True}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path, stability_rounds=1)
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is True
    assert verdict.rounds_taken >= 1
    assert verdict.decision != f"Answer: {PG}"
    events = engine.transcripts.read(verdict.transcript_id)
    spawned = next(e for e in events if e.get("stage") == "opposition_spawned")
    members = spawned["faction"]["members"]
    assert members == ["devils_advocate"]
    rounds = [e for e in events if e.get("stage") == "round"]
    assert rounds and rounds[0]["critiques"]


@pytest.mark.asyncio
async def test_mixed_elicitation_answers_stay_honest(tmp_path) -> None:
    """Мусор опций и пустой ответ — open_ambiguities; цифра 2 — текст опции."""
    three = {
        "ambiguities": [
            {
                "ambiguity": "store unstated",
                "why_it_matters": "changes the pick",
                "candidate_question": "Which store?",
                "options": ["Postgres", "Kafka"],
            },
            {
                "ambiguity": "budget unstated",
                "why_it_matters": "changes ops",
                "candidate_question": "Budget absolutely zero?",
                "options": ["yes", "no"],
            },
            {
                "ambiguity": "team unstated",
                "why_it_matters": "changes ops",
                "candidate_question": "Team size?",
                "options": ["two", "four"],
            },
        ],
    }
    script = [
        ("m1", ("Do NOT answer",), three),
        ("m2", ("Do NOT answer",), three),
        ("m3", ("Do NOT answer",), three),
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]

    def on_questions(questions: list) -> list[str]:
        assert len(questions) == 3
        return ["Postgres | Kafka", "", "2"]

    engine = make_engine(
        ScriptedProvider(script),
        tmp_path,
        devils_advocate=False,
    )
    verdict = await engine.deliberate(
        "Which database for the ledger?",
        force_protocol=Protocol.VOTE,
        clarify_mode="smart",
        on_questions=on_questions,
    )
    assert "Budget absolutely zero?" in verdict.value_map.open_ambiguities
    assert "Which store?" in verdict.value_map.open_ambiguities
    assert any("Team size?" in c and "four" in c for c in verdict.value_map.constraints)
    assert not any("Postgres | Kafka" in c for c in verdict.value_map.constraints)
    assert verdict.decision != ""
    assert "absolute zero" not in verdict.decision.lower()
