"""End-to-end tests on a SCRIPTED provider (consumable script, deterministic
via alias_seed, transcripts in tmp_path).

Round-10: the plan contract renders ONLY on zhoda; paths_rejected counts
rejections by a reached consensus; majority at cap is dissent
(`decision_origin=majority_at_cap`); an appeal carries decision_origin.
"""

import json

import pytest

from zhoda_core.anonymize import content_alias_seed, make_aliases
from zhoda_core.engine import ZhodaEngine
from zhoda_core.models import CheckStatus, ConsensusStrength, Protocol
from zhoda_core.progress import ProgressEvent
from zhoda_core.providers.openrouter import (
    BudgetExceededError,
    OpenRouterProvider,
    ZhodaProviderError,
)

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
                self.cost.requests += 1  # честный breakdown: скрипт = вызов
                return response if isinstance(response, str) else json.dumps(response)
        raise AssertionError(f"no scripted response: model={model} prompt={prompt[:80]}")

    async def ask_json(self, model, prompt, **kwargs):
        return json.loads(await self.complete(model, prompt, **kwargs))


class CachingScriptedProvider(OpenRouterProvider):
    """Как ScriptedProvider, но cache_key попадает в sqlite — повтор без скрипта."""

    def __init__(
        self,
        script: list[tuple[str | None, tuple[str, ...], object]],
        *,
        cache_path: str,
    ) -> None:
        super().__init__(api_key="test", cache_path=cache_path)
        self.script = list(script)

    async def complete(self, model, prompt, *, cache_key=None, **kwargs):
        del kwargs
        if cache_key:
            cached = self._cache_get(cache_key)
            if cached is not None:
                self.cost.cache_hits += 1
                return cached
        for i, (want_model, patterns, response) in enumerate(self.script):
            if (want_model is None or want_model == model) and all(p in prompt for p in patterns):
                self.script.pop(i)
                text = response if isinstance(response, str) else json.dumps(response)
                self.cost.requests += 1
                if cache_key:
                    self._cache_put(cache_key, text)
                return text
        raise AssertionError(f"no scripted response: model={model} prompt={prompt[:80]}")


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
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path, stability_rounds=1, rounds_cap=1)
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is False
    assert verdict.consensus_strength == ConsensusStrength.MAJORITY
    assert verdict.decision_origin == "majority_at_cap"
    assert verdict.decision.startswith("Recommended (majority at cap, not zhoda):")
    assert "partitioning" in verdict.decision
    assert "Kafka" in verdict.decision or (
        verdict.minority_report is not None and "Kafka" in verdict.minority_report
    )
    assert verdict.plan_contract is None
    assert verdict.paths_rejected == []
    assert verdict.switches == []
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


def _closed_majority_round() -> list:
    """2 фракции + DA, все возражения закрыты, тезисы не сходятся."""
    return [
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
                "claim": "the platform ignores read replicas as a simpler scaling path",
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
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
    ]


@pytest.mark.asyncio
async def test_majority_does_not_early_stop_before_cap(tmp_path) -> None:
    """2/3 голов — не згода на 2-м раунде; дебаты идут до cap."""
    aliases = make_aliases(COUNCIL, seed=42)
    script = opening_script(aliases) + _closed_majority_round() * 3
    engine = make_engine(
        ScriptedProvider(script),
        tmp_path,
        stability_rounds=2,
        rounds_cap=3,
    )
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.rounds_taken == 3
    assert verdict.zhoda_reached is False
    assert verdict.consensus_strength == ConsensusStrength.MAJORITY
    assert verdict.decision_origin == "majority_at_cap"
    assert verdict.plan_contract is None
    assert verdict.decision.startswith("Recommended (majority at cap, not zhoda):")
    assert PG in verdict.decision
    assert KF in verdict.decision


@pytest.mark.asyncio
async def test_unanimous_at_cap_is_zhoda_without_full_streak(tmp_path) -> None:
    """Live kafka: unanimous только на последнем раунде капа — это згода."""
    aliases = make_aliases(COUNCIL, seed=42)
    majority = _closed_majority_round()
    last = majority[:-2] + [
        (None, ("theses of all factions",), {"all_agree": True}),
        (None, ("theses of all factions",), {"all_agree": True}),
    ]
    script = (
        opening_script(aliases)
        + majority
        + last
        + [
            (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
            (None, ("PLAN CONTRACT",), PLAN),
        ]
    )
    engine = make_engine(
        ScriptedProvider(script),
        tmp_path,
        stability_rounds=2,
        rounds_cap=2,
    )
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.rounds_taken == 2
    assert verdict.zhoda_reached is True
    assert verdict.consensus_strength == ConsensusStrength.UNANIMOUS


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
    assert verdict.cost.breakdown.get("debate", 0) > 0


@pytest.mark.asyncio
async def test_red_team_with_context_skips_elicit(tmp_path) -> None:
    """Live login: --context уже исходник — auto-clarify не спрашивает sanitize."""
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
        "Review this login helper for production use.",
        force_protocol=Protocol.RED_TEAM,
        clarify_mode="auto-clarify",
        context="def login(user, password): ...",
    )
    assert verdict.insufficient_context is False
    assert verdict.cost.breakdown.get("elicit", 0) == 0
    assert "positions" in verdict.cost.breakdown


@pytest.mark.asyncio
async def test_vote_auto_clarify_skips_elicit(tmp_path) -> None:
    """Vote — однопроход: auto-clarify не тратит Stage 0 на NULL≠TRUE."""
    script = [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path, devils_advocate=False)
    verdict = await engine.deliberate(
        "Does SQL NULL equal TRUE?",
        force_protocol=Protocol.VOTE,
        clarify_mode="auto-clarify",
    )
    assert verdict.zhoda_reached is True
    assert verdict.cost.breakdown.get("elicit", 0) == 0
    assert "positions" in verdict.cost.breakdown


@pytest.mark.asyncio
async def test_cited_switch_is_recorded(tmp_path) -> None:
    """Возражение осталось OPEN — модель переходит с цитатой claim, не thesis."""
    aliases = make_aliases(COUNCIL, seed=42)
    a1 = aliases["m1"]
    claim = "PostgreSQL at 50k RPS writes needs a partitioning plan"
    citation = (
        "PostgreSQL at 50k RPS writes needs a partitioning plan — "
        "a single node cannot hold that write load"
    )
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
                "claim": claim,
                "specifics": "no write-scaling story beyond a single node",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely", "operational complexity"), "Staffing is a training issue."),
        (None, ("Rebut it concisely", "partitioning"), "Solved."),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
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
        ("m1", ("Do you switch factions?",), {"switch": True, "convinced_by": citation}),
        ("m3", ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
    ]
    engine = make_engine(
        ScriptedProvider(script),
        tmp_path,
        stability_rounds=1,
        rounds_cap=1,
    )
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert len(verdict.switches) == 1
    switch = verdict.switches[0]
    assert switch.from_faction == "Pragmatists"
    assert switch.to_faction == "Throughputists"
    assert switch.model == a1
    assert "partitioning" in switch.convinced_by.casefold()
    assert verdict.zhoda_reached is False
    assert verdict.decision_origin == "majority_at_cap"


@pytest.mark.asyncio
async def test_repeat_debate_replays_from_cache(tmp_path) -> None:
    """Live kafka repeat=2: тот же вопрос + sqlite → дебат без новых LLM."""
    question = "PostgreSQL or Kafka for a 50k RPS ledger?"
    seed = content_alias_seed(question, COUNCIL)
    aliases = make_aliases(COUNCIL, seed=seed)
    round_script = [
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
        (None, ("Rebut it concisely", "operational complexity"), "Staffing is a training issue."),
        (None, ("Rebut it concisely", "partitioning"), "Partitioning is a documented path."),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "operational complexity"), {"closed": True}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": True}),
        (None, ("Did the rebuttal", "partitioning"), {"closed": True}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
    ]
    cache = str(tmp_path / "debate.db")
    first = CachingScriptedProvider(
        opening_script(aliases) + round_script, cache_path=cache,
    )
    engine = make_engine(
        first, tmp_path, alias_seed=None, stability_rounds=1, rounds_cap=1,
    )
    v1 = await engine.deliberate(
        question, force_protocol=Protocol.DEBATE, clarify_mode="no-clarify",
    )
    assert v1.cost.requests > 0
    assert v1.cost.cache_hits == 0
    await first.close()

    second = CachingScriptedProvider([], cache_path=cache)
    engine2 = make_engine(
        second, tmp_path, alias_seed=None, stability_rounds=1, rounds_cap=1,
    )
    v2 = await engine2.deliberate(
        question, force_protocol=Protocol.DEBATE, clarify_mode="no-clarify",
    )
    await second.close()
    assert second.script == []
    assert v2.cost.requests == 0
    assert v2.cost.cache_hits > 0
    assert v2.cost.cache_breakdown.get("debate", 0) > 0
    assert v2.decision == v1.decision
    assert v2.zhoda_reached == v1.zhoda_reached


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
        rounds_cap=1,
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
async def test_loaded_premise_unanimity_is_not_zhoda(tmp_path) -> None:
    """syc-001: единогласное принятие loaded premise — не згода, rec отвергает."""
    from zhoda_core.guards import LOADED_PREMISE_NOTE, LOADED_PREMISE_UNVERIFIED

    rest = "REST is recommended over gRPC because it often yields faster responses."
    script = [
        ("m1", ("independent structured stance",), position(rest)),
        ("m2", ("independent structured stance",), position(rest)),
        ("m3", ("independent structured stance",), position(rest)),
        (None, ("Synthesize the shared platform",), position(rest)),
        (None, ("Name each faction",), {}),
    ]
    provider = ScriptedProvider(script)
    engine = make_engine(
        provider, tmp_path, stability_rounds=2, devils_advocate=False,
    )
    verdict = await engine.deliberate(
        "Why is REST always faster than gRPC for internal microservice calls?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is False
    assert verdict.decision_origin == "majority_at_cap"
    assert verdict.plan_contract is None
    assert verdict.decision.startswith("Recommended (majority at cap, not zhoda):")
    assert LOADED_PREMISE_UNVERIFIED in verdict.decision
    assert "REST is recommended" not in verdict.decision.split("Dissent:")[0]
    assert LOADED_PREMISE_NOTE in verdict.value_map.open_ambiguities
    assert LOADED_PREMISE_NOTE not in verdict.value_map.constraints
    assert provider.script == []


@pytest.mark.asyncio
async def test_b1_given_that_keeps_monolith_action(tmp_path) -> None:
    """Background given-that не заменяет rec на fabricated 'premise is false'."""
    from zhoda_core.guards import LOADED_PREMISE_NOTE, LOADED_PREMISE_REJECT

    rec = "Use a monolith to minimize coordination overhead."
    script = [
        ("m1", ("independent structured stance",), position(rec)),
        ("m2", ("independent structured stance",), position(rec)),
        ("m3", ("independent structured stance",), position(rec)),
        (None, ("Synthesize the shared platform",), position(rec)),
        (None, ("Name each faction",), {}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), {"decision": rec}),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(
        ScriptedProvider(script), tmp_path, devils_advocate=False,
    )
    verdict = await engine.deliberate(
        "Given that our team has four engineers, should we use a monolith?",
        force_protocol=Protocol.VOTE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is True
    assert "monolith" in verdict.decision.lower()
    assert LOADED_PREMISE_REJECT not in verdict.decision
    assert LOADED_PREMISE_NOTE in verdict.value_map.open_ambiguities
    assert LOADED_PREMISE_NOTE not in verdict.value_map.constraints


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
    assert spawned["faction"]["synthetic"] is True
    rounds = [e for e in events if e.get("stage") == "round"]
    assert rounds and rounds[0]["critiques"]
    authors = [c["author_faction"] for c in rounds[0]["critiques"]]
    assert "devils_advocate" not in authors
    assert "Skeptics" in authors


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
        ("j1", ("Group equivalent clarifying questions",), {"groups": [[0], [1], [2]]}),
        ("m1", ("Do NOT answer",), {"ambiguities": []}),
        ("m2", ("Do NOT answer",), {"ambiguities": []}),
        ("m3", ("Do NOT answer",), {"ambiguities": []}),
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
    assert sum(verdict.cost.breakdown.values()) == verdict.cost.requests


@pytest.mark.asyncio
async def test_url_answer_is_insufficient_context_and_skips_debate(tmp_path) -> None:
    """Совет не судит объект, которого у него нет — позиции и раунды не зовутся."""
    grounding = {
        "ambiguities": [
            {
                "ambiguity": "object unstated",
                "why_it_matters": "cannot judge an unseen artifact",
                "candidate_question": "Which project are we evaluating?",
                "options": [],
            }
        ],
    }
    provider = ScriptedProvider(
        [
            ("m1", ("Do NOT answer",), grounding),
            ("m2", ("Do NOT answer",), grounding),
            ("m3", ("Do NOT answer",), grounding),
        ]
    )
    engine = make_engine(provider, tmp_path)

    def on_questions(questions: list) -> list[str]:
        assert len(questions) == 1
        return ["https://github.com/org/zhoda"]

    verdict = await engine.deliberate(
        "Evaluate project X",
        force_protocol=Protocol.VOTE,
        clarify_mode="smart",
        on_questions=on_questions,
    )
    assert verdict.insufficient_context is True
    assert verdict.zhoda_reached is False
    assert verdict.consensus_strength == ConsensusStrength.SPLIT
    assert verdict.decision.startswith("INSUFFICIENT_CONTEXT:")
    assert verdict.rounds_taken == 0
    assert "positions" not in verdict.cost.breakdown
    assert provider.script == []


@pytest.mark.asyncio
async def test_auto_clarify_grounding_is_insufficient_without_context(tmp_path) -> None:
    """auto-clarify: объект уже ясно отсутствует — IC без Stage 0 LLM."""
    provider = ScriptedProvider([])
    engine = make_engine(provider, tmp_path)
    verdict = await engine.deliberate(
        "Evaluate project X",
        force_protocol=Protocol.VOTE,
        clarify_mode="auto-clarify",
    )
    assert verdict.insufficient_context is True
    assert verdict.zhoda_reached is False
    assert verdict.consensus_strength == ConsensusStrength.SPLIT
    assert verdict.decision.startswith("INSUFFICIENT_CONTEXT:")
    assert verdict.rounds_taken == 0
    assert verdict.cost.breakdown.get("elicit", 0) == 0
    assert "positions" not in verdict.cost.breakdown
    assert verdict.value_map.assumptions == []
    assert provider.script == []


@pytest.mark.asyncio
async def test_context_files_land_in_position_prompt(tmp_path) -> None:
    token = "SECRET_CONTEXT_TOKEN_README"
    script = [
        ("m1", ("independent structured stance", token), position(PG)),
        ("m2", ("independent structured stance", token), position(PG)),
        ("m3", ("independent structured stance", token), position(PG)),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path, devils_advocate=False)
    verdict = await engine.deliberate(
        "Which database for the ledger?",
        force_protocol=Protocol.VOTE,
        clarify_mode="no-clarify",
        context=token,
    )
    assert verdict.zhoda_reached is True
    assert verdict.insufficient_context is False


@pytest.mark.asyncio
async def test_elicitation_answers_reach_positions_and_verdict(tmp_path) -> None:
    """Ответы Stage 0 попадают в промпты позиций и вердикта (не в cache по одному вопросу)."""
    payload = {
        "ambiguities": [
            {
                "ambiguity": "stakes unstated",
                "why_it_matters": "changes urgency",
                "candidate_question": "What if we miss the deadline?",
                "options": ["minimal consequences", "serious consequences"],
            }
        ],
    }

    async def run(answer: str, thesis: str, decision: str) -> tuple:
        script = [
            ("m1", ("Do NOT answer",), payload),
            ("m2", ("Do NOT answer",), payload),
            ("m3", ("Do NOT answer",), payload),
            ("m1", ("Do NOT answer",), {"ambiguities": []}),
            ("m2", ("Do NOT answer",), {"ambiguities": []}),
            ("m3", ("Do NOT answer",), {"ambiguities": []}),
            ("m1", ("independent structured stance", answer), position(thesis)),
            ("m2", ("independent structured stance", answer), position(thesis)),
            ("m3", ("independent structured stance", answer), position(thesis)),
            (None, ("Synthesize the shared platform", answer), position(thesis)),
            (None, ("Name each faction",), {}),
            (None, ("SYNTHESIZE THE COUNCIL DECISION", answer), {"decision": decision}),
            (None, ("PLAN CONTRACT",), PLAN),
        ]
        engine = make_engine(
            ScriptedProvider(script),
            tmp_path,
            devils_advocate=False,
        )
        verdict = await engine.deliberate(
            "Should we ship next week?",
            force_protocol=Protocol.VOTE,
            clarify_mode="smart",
            on_questions=lambda qs: [answer],
        )
        events = engine.transcripts.read(verdict.transcript_id)
        pos_event = next(e for e in events if e.get("stage") == "positions")
        theses = [p["thesis"] for p in pos_event["positions"]]
        return verdict, theses

    cheap = "minimal consequences"
    costly = "serious consequences"
    v1, t1 = await run(
        cheap, "Ship now — miss is cheap", "Ship now; missing the deadline is cheap",
    )
    v2, t2 = await run(
        costly, "Slip the date — miss is costly", "Slip; missing the deadline is costly",
    )
    assert cheap in v1.value_map.constraints[0]
    assert costly in v2.value_map.constraints[0]
    assert t1 != t2
    assert all("cheap" in t.lower() for t in t1)
    assert all("costly" in t.lower() for t in t2)
    assert v1.decision != v2.decision
    assert "cheap" in v1.decision.lower()
    assert "costly" in v2.decision.lower()


@pytest.mark.asyncio
async def test_supplied_value_map_skips_elicitation(tmp_path) -> None:
    """Готовый value_map (MCP) не зовёт Stage 0, но попадает в промпты позиций."""
    from zhoda_core.models import ValueMap

    token = "TEAM_OF_FOUR_CONSTRAINT"
    script = [
        ("m1", ("independent structured stance", token), position(PG)),
        ("m2", ("independent structured stance", token), position(PG)),
        ("m3", ("independent structured stance", token), position(PG)),
        (None, ("Synthesize the shared platform", token), position(PG)),
        (None, ("Name each faction",), {}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    provider = ScriptedProvider(script)
    engine = make_engine(provider, tmp_path, devils_advocate=False)
    verdict = await engine.deliberate(
        "Which database for the ledger?",
        force_protocol=Protocol.VOTE,
        clarify_mode="smart",
        value_map=ValueMap(constraints=[token]),
    )
    assert verdict.value_map.constraints == [token]
    assert verdict.cost.breakdown.get("elicit", 0) == 0
    events = engine.transcripts.read(verdict.transcript_id)
    assert not any(e.get("stage") == "elicit" for e in events)
    assert provider.script == []
    assert verdict.zhoda_reached is True


class FlakyCouncilProvider(ScriptedProvider):
    """Совет a/b/c: отвечает только a; b/c и required opposition падают."""

    async def complete(self, model, prompt, **kwargs):
        if "independent structured stance" in prompt and model in {"m2", "m3"}:
            raise ZhodaProviderError(f"{model} failed")
        if "Spawn an opposition faction" in prompt:
            raise ZhodaProviderError("opposition failed")
        return await super().complete(model, prompt, **kwargs)


@pytest.mark.asyncio
async def test_d1_partial_council_is_not_trusted_zhoda(tmp_path) -> None:
    """D1: отсутствие ответа не повышает доверие. Roster — requested, не responders."""
    script = [
        ("m1", ("independent structured stance",), position(PG)),
        (None, ("Name each faction",), {}),
    ]
    engine = make_engine(FlakyCouncilProvider(script), tmp_path)
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is False
    assert verdict.degraded is True
    assert verdict.plan_contract is None
    assert verdict.decision_origin == "degraded"
    assert "degraded" in verdict.decision
    by_id = {rec.check_id: rec for rec in verdict.completeness.checks}
    assert set(by_id) == {
        "position:m1",
        "position:m2",
        "position:m3",
        "opposition:synthetic",
    }
    assert by_id["position:m1"].status is CheckStatus.SUCCEEDED
    assert by_id["position:m2"].status is CheckStatus.FAILED
    assert by_id["position:m3"].status is CheckStatus.FAILED
    assert by_id["opposition:synthetic"].status is CheckStatus.FAILED
    assert by_id["opposition:synthetic"].reason == "spawn_failed"
    assert verdict.completeness.requested_count == 4
    assert verdict.completeness.trusted is False
    events = engine.transcripts.read(verdict.transcript_id)
    assert any(e.get("fast_pass") == "unanimity_at_birth" for e in events)
    assert verdict.consensus_strength is ConsensusStrength.UNANIMOUS


@pytest.mark.asyncio
async def test_d2_invalid_required_json_is_degraded(tmp_path) -> None:
    """D2: невалидный JSON обязательной проверки → degraded, не approved plan."""
    script = [
        ("m1", ("independent structured stance",), position(PG)),
        ("m2", ("independent structured stance",), position(PG)),
        ("m3", ("independent structured stance",), "NOT_JSON"),
        (None, ("Synthesize the shared platform",), position(PG)),
        (None, ("Name each faction",), {}),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path, devils_advocate=False)
    verdict = await engine.deliberate(
        "Does SQL NULL equal TRUE?",
        force_protocol=Protocol.VOTE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is False
    assert verdict.degraded is True
    assert verdict.plan_contract is None
    assert verdict.decision_origin == "degraded"
    by_id = {rec.check_id: rec for rec in verdict.completeness.checks}
    assert by_id["position:m1"].status is CheckStatus.SUCCEEDED
    assert by_id["position:m2"].status is CheckStatus.SUCCEEDED
    assert by_id["position:m3"].status is CheckStatus.FAILED
    assert "JSON" in by_id["position:m3"].reason or "json" in by_id["position:m3"].reason.lower()
    assert verdict.completeness.requested_count == 3
    assert "opposition:synthetic" not in by_id


MARKER = "ATTACHED_SOURCE_sql = user_input_UNIQUE"
NO_SQL = "There is no SQL injection."


class RecordingProvider(ScriptedProvider):
    """Пишет фактические prompts — E1 проверяет их, не fake-ответ."""

    def __init__(self, script: list) -> None:
        super().__init__(script)
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, model, prompt, **kwargs):
        self.prompts.append((model, prompt))
        return await super().complete(model, prompt, **kwargs)


def _login_position(thesis: str, finding: str) -> dict:
    return {
        "thesis": thesis,
        "answer": f"Answer: {thesis}",
        "claims": [{"claim": finding, "evidence_url": None, "confidence": 0.9}],
        "falsifiability": "if the query is parameterized",
        "confidence": 0.9,
    }


@pytest.mark.asyncio
async def test_e1_source_span_reaches_closure_and_revision(tmp_path) -> None:
    """E1: маркер из initial context есть в prompts обеих closure-моделей и revision."""
    aliases = make_aliases(COUNCIL, seed=42)
    a1 = aliases["m1"]
    approve = _login_position("Approve login", "Helper looks fine")
    script = [
        ("m1", ("independent structured stance",), approve),
        ("m2", ("independent structured stance",), approve),
        ("m3", ("independent structured stance",), approve),
        (None, ("Synthesize the shared platform",), approve),
        (None, ("Name each faction",), {}),
        (
            None,
            ("devil's advocate",),
            {
                "target_faction": a1,
                "flaw_type": "factual",
                "claim": "user_input is interpolated into the SQL query string",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely",), "The helper uses a constant query template."),
        (None, ("Did the rebuttal",), {"closed": False}),
        (None, ("Did the rebuttal",), {"closed": False}),
        (
            None,
            ("Revise your platform",),
            {
                "thesis": "Approve login",
                "answer": "Answer: Approve login",
                "claims": [{"claim": "Helper looks fine", "evidence_url": None, "confidence": 0.9}],
                "changed": False,
                "change_note": "stand",
            },
        ),
        (None, ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), {"decision": "Approve login"}),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    provider = RecordingProvider(script)
    engine = make_engine(provider, tmp_path)
    await engine.deliberate(
        "Review this login helper for production use.",
        force_protocol=Protocol.RED_TEAM,
        clarify_mode="no-clarify",
        context=f"def login(u, p): cur.execute('SELECT * FROM u WHERE id=' + {MARKER})",
    )
    closure = [p for _, p in provider.prompts if "Did the rebuttal" in p]
    revision = [p for _, p in provider.prompts if "Revise your platform" in p]
    assert len(closure) == 2
    assert all(MARKER in p for p in closure)
    assert revision and all(MARKER in p for p in revision)
    assert all("CONCEDE" not in p.split("Rebuttal:", 1)[-1][:80] for p in closure)


@pytest.mark.asyncio
async def test_e2_revision_does_not_inherit_false_finding(tmp_path) -> None:
    """Approve → Reject: 'There is no SQL injection' не остаётся active finding."""
    aliases = make_aliases(COUNCIL, seed=42)
    a1 = aliases["m1"]
    approve = _login_position("Approve login", NO_SQL)
    reject = _login_position(
        "Reject login",
        "SQL injection from interpolating user input into the query",
    )
    script = [
        ("m1", ("independent structured stance",), approve),
        ("m2", ("independent structured stance",), approve),
        ("m3", ("independent structured stance",), approve),
        (None, ("Synthesize the shared platform",), approve),
        (None, ("Name each faction",), {}),
        (
            None,
            ("devil's advocate",),
            {
                "target_faction": a1,
                "flaw_type": "factual",
                "claim": "user_input is interpolated into the SQL query string",
                "specifics": "",
                "evidence_url": None,
            },
        ),
        (None, ("Rebut it concisely",), "The helper uses a constant query template."),
        (None, ("Did the rebuttal",), {"closed": False}),
        (None, ("Did the rebuttal",), {"closed": False}),
        (
            None,
            ("Revise your platform",),
            {
                **reject,
                "changed": True,
                "change_note": "the helper interpolates user input",
            },
        ),
        (None, ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("Do you switch factions?",), {"switch": False, "convinced_by": ""}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), {"decision": "Reject login"}),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path)
    verdict = await engine.deliberate(
        "Review this login helper for production use.",
        force_protocol=Protocol.RED_TEAM,
        clarify_mode="no-clarify",
        context="def login(u, p): cur.execute('SELECT * FROM u WHERE id=' + u)",
    )
    from zhoda_core.models import ClaimState

    active = [c.claim for c in verdict.claim_ledger if c.state is ClaimState.ACTIVE]
    history = [c.claim for c in verdict.claim_ledger if c.state is ClaimState.SUPERSEDED]
    assert NO_SQL not in active
    assert any("There is no SQL injection" in c for c in history)
    assert any("SQL injection from interpolating" in c for c in active)
    assert NO_SQL not in verdict.decision


@pytest.mark.asyncio
async def test_e6_replay_restores_structure_without_provider(tmp_path) -> None:
    """E6: reducer без cache/network; не копирует final Verdict event."""
    from unittest.mock import patch

    from zhoda_core.models import ClaimState
    from zhoda_core.replay import reduce_transcript

    f1 = "SQL injection from interpolating user input into the query"
    f2 = "Session cookies lack the Secure flag"
    pos = {
        "thesis": "Reject login",
        "answer": "Do not approve.",
        "claims": [
            {"claim": f1, "evidence_url": None, "confidence": 0.9},
            {"claim": f2, "evidence_url": None, "confidence": 0.9},
        ],
        "falsifiability": "if parameterized",
        "confidence": 0.9,
    }
    script = [
        ("m1", ("independent structured stance",), pos),
        ("m2", ("independent structured stance",), pos),
        ("m3", ("independent structured stance",), pos),
        (None, ("Synthesize the shared platform",), pos),
        (None, ("Name each faction",), {}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), {"decision": "Reject login"}),
        (None, ("PLAN CONTRACT",), PLAN),
    ]
    cache = tmp_path / "e6.db"
    engine = make_engine(
        ScriptedProvider(script), tmp_path, devils_advocate=False,
    )
    engine.provider._db = None
    verdict = await engine.deliberate(
        "Review this login helper for production use.",
        force_protocol=Protocol.VOTE,
        clarify_mode="no-clarify",
    )
    if cache.exists():
        cache.unlink()
    events = engine.transcripts.read(verdict.transcript_id)
    without_verdict = [e for e in events if e.get("stage") != "verdict"]
    assert any(e.get("stage") == "verdict" for e in events)
    with patch(
        "zhoda_core.providers.openrouter.OpenRouterProvider.complete",
        side_effect=AssertionError("provider must not be called during replay"),
    ), patch(
        "zhoda_core.providers.openrouter.OpenRouterProvider.ask_json",
        side_effect=AssertionError("provider must not be called during replay"),
    ):
        replayed = reduce_transcript(without_verdict)
    assert replayed.zhoda_reached is verdict.zhoda_reached
    assert replayed.consensus_strength is verdict.consensus_strength
    assert replayed.completeness is not None
    assert replayed.completeness.trusted is verdict.completeness.trusted
    live_active = {c.claim for c in verdict.claim_ledger if c.state is ClaimState.ACTIVE}
    replay_active = {c.claim for c in replayed.current_claims()}
    assert f1 in live_active and f2 in live_active
    assert live_active == replay_active
    created = [
        e for e in without_verdict
        if e.get("stage") == "protocol_event"
        and (e.get("event") or {}).get("type") == "claim_created"
    ]
    assert len(created) >= 2
    assert replayed.decision == verdict.decision
    assert replayed.action is not None
    assert replayed.switches == []


SOURCE_FRIDAY = (
    "On-call coverage ends at 18:00 on Fridays with no rollback owner."
)


@pytest.mark.asyncio
async def test_short_review_is_critique_revision_not_synthesis(tmp_path) -> None:
    """Force short_review: 1 раунд, не vote и не chairman-only synthesis."""
    aliases = make_aliases(COUNCIL, seed=42)
    quote = {
        "target_faction": "Throughputists",
        "flaw_type": "factual",
        "claim": (
            "On-call coverage ends at 18:00 on Fridays with no rollback owner "
            "so Kafka ops are unstaffed"
        ),
        "specifics": SOURCE_FRIDAY,
        "evidence_url": None,
    }
    quote_kf = {
        **quote,
        "target_faction": "Pragmatists",
        "claim": (
            "On-call coverage ends at 18:00 on Fridays with no rollback owner "
            "so a Friday deploy is the asked risk"
        ),
    }
    script = opening_script(aliases) + [
        (None, ("evidence-focused critique",), quote),
        (None, ("evidence-focused critique",), quote_kf),
        (
            None,
            ("Revise your platform",),
            {
                "thesis": PG,
                "answer": f"Answer: {PG}",
                "claims": [],
                "falsifiability": "if load grows",
                "confidence": 0.7,
                "changed": False,
                "change_note": "kept",
            },
        ),
        (
            None,
            ("Revise your platform",),
            {
                "thesis": KF,
                "answer": f"Answer: {KF}",
                "claims": [],
                "falsifiability": "if load grows",
                "confidence": 0.7,
                "changed": False,
                "change_note": "kept",
            },
        ),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("theses of all factions",), {"all_agree": False}),
        (None, ("SYNTHESIZE THE COUNCIL DECISION",), DECISION),
    ]
    engine = make_engine(ScriptedProvider(script), tmp_path, rounds_cap=4)
    verdict = await engine.deliberate(
        "Is a Friday 22:00 payment deploy acceptable given the attached memo?",
        force_protocol=Protocol.SHORT_REVIEW,
        clarify_mode="no-clarify",
        context=SOURCE_FRIDAY,
    )
    assert verdict.protocol is Protocol.SHORT_REVIEW
    assert verdict.rounds_taken == 1
    assert verdict.switches == []
    assert verdict.zhoda_reached is False
    assert verdict.decision_origin == "majority_at_cap"
    rows = engine.transcripts.read(verdict.transcript_id)
    stages = [r.get("stage") for r in rows]
    assert "round" in stages
    assert "positions" in stages
    leftover = engine.provider.script
    assert not any("evidence-focused" in str(step) for step in leftover)
    assert not any("Rebut it concisely" in str(step) for step in leftover)


@pytest.mark.asyncio
async def test_router_does_not_select_short_review() -> None:
    """Default product path: decision → debate, never short_review."""
    from zhoda_core.router import PROTOCOL_BY_CLASS, ProtocolRouter, TaskClass

    assert PROTOCOL_BY_CLASS[TaskClass.DECISION] is Protocol.DEBATE
    assert Protocol.SHORT_REVIEW not in PROTOCOL_BY_CLASS.values()

    class Prov:
        async def ask_json(self, model: str, prompt: str, **kwargs: object) -> dict:
            del model, prompt, kwargs
            return {"task_class": "decision"}

    route = await ProtocolRouter(Prov(), ("c1", "c2")).route(  # type: ignore[arg-type]
        "PostgreSQL or Kafka for a 50k RPS ledger?",
    )
    assert route.protocol is Protocol.DEBATE
    assert route.overridden is False


class FreezeAfterN(ScriptedProvider):
    """После N complete — BudgetExceededError, как admissions freeze в live G."""

    def __init__(
        self,
        script: list[tuple[str | None, tuple[str, ...], object]],
        freeze_after: int,
    ) -> None:
        super().__init__(script)
        self.freeze_after = freeze_after
        self.calls = 0

    async def complete(self, model, prompt, **kwargs):
        if self.calls >= self.freeze_after:
            raise BudgetExceededError("admissions frozen after overrun $0.0001")
        self.calls += 1
        return await super().complete(model, prompt, **kwargs)


@pytest.mark.asyncio
async def test_budget_freeze_after_factions_is_local_verdict(tmp_path) -> None:
    """Freeze после фракций не роняет deliberate: локальный вердикт, не згода."""
    aliases = make_aliases(COUNCIL, seed=42)
    script = opening_script(aliases)
    engine = make_engine(
        FreezeAfterN(script, freeze_after=len(script)),
        tmp_path,
        rounds_cap=4,
        devils_advocate=False,
    )
    verdict = await engine.deliberate(
        "PostgreSQL or Kafka for a 50k RPS ledger?",
        force_protocol=Protocol.DEBATE,
        clarify_mode="no-clarify",
    )
    assert verdict.zhoda_reached is False
    assert verdict.decision
    assert verdict.plan_contract is None
    assert "PostgreSQL" in verdict.decision or "Kafka" in verdict.decision



