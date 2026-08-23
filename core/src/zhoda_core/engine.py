"""Orchestration state machine: router -> elicitor -> positions -> factions
(chairman names them) -> debate rounds (revise BEFORE switches) -> verdict
-> plan contract + decision tree. On DEADLOCK with escalation enabled, an
appellate model reads the outcome and decides (round-9 §4) — the escalation
ladder is real, not a config promise.
"""

from collections.abc import Callable

from .anonymize import make_aliases
from .consensus import ConsensusChecker
from .debate import DebateEngine
from .elicitor import ClarifyingQuestion, Elicitor
from .factions import Faction, FactionClusterer
from .judges import Judges
from .models import ConsensusStrength, Disagreement, ObjectionStatus, Protocol, ValueMap, Verdict
from .plan import render_plan_contract
from .positions import extract_positions
from .providers.openrouter import OpenRouterProvider
from .router import ProtocolRouter
from .transcripts import TranscriptStore
from .tree import build_decision_tree
from .verdict import VerdictBuilder

NAMING_PROMPT = """Name each faction descriptively (e.g. \"Pragmatists\", \"Maximalists\").
Factions:
{lines}

ONLY valid JSON: {{"<current name>": "<descriptive name>"}}"""

APPEAL_PROMPT = """The council deadlocked on: {question}
Final theses: {theses}
Unclosed objections: {objections}

You are the appellate judge. Decide, and state which arguments won.
ONLY valid JSON: {{"decision": "...", "winning_arguments": ["..."]}}"""

MAX_FACTION_NAME = 40


class ZhodaEngine:
    def __init__(
        self,
        provider: OpenRouterProvider,
        council: list[str],
        *,
        chairman: str,
        judges: tuple[str, str],
        router_classifiers: tuple[str, str],
        rounds_cap: int = 4,
        stability_rounds: int = 2,
        devils_advocate: bool = True,
        ambiguity_threshold: float = 0.6,
        max_new_per_round: int = 3,
        max_active: int = 6,
        escalation_model: str | None = None,
        transcripts_dir: str = "transcripts",
        alias_seed: int | None = None,
    ) -> None:
        if len(council) < 2:
            raise ValueError("council needs at least 2 models")
        if router_classifiers is None or len(set(router_classifiers)) < 2:
            raise ValueError("router_classifiers: two distinct models, from config")
        clean_judges = [j for j in judges if j not in council]
        if len(clean_judges) < 2:
            raise ValueError(
                "at least two judges must sit OUTSIDE the council "
                f"(clean: {clean_judges}) — no silent fallback"
            )
        self.provider = provider
        self.council = council
        self.chairman = chairman
        self.judge_models = judges
        self.rounds_cap = rounds_cap
        self.stability_rounds = stability_rounds
        self.devils_advocate = devils_advocate
        self.max_new_per_round = max_new_per_round
        self.max_active = max_active
        self.escalation_model = escalation_model
        self.alias_seed = alias_seed
        self.transcripts = TranscriptStore(transcripts_dir)
        self.router = ProtocolRouter(provider, classifiers=router_classifiers)
        self.elicitor = Elicitor(provider, ambiguity_threshold=ambiguity_threshold)
        self.verdicts = VerdictBuilder()

    async def deliberate(
        self,
        question: str,
        *,
        force_protocol: Protocol | None = None,
        clarify_mode: str = "smart",
        on_questions: Callable[[list[ClarifyingQuestion]], list[str]] | None = None,
    ) -> Verdict:
        # fresh session state per question (round-8 §1)
        debate = DebateEngine(
            self.provider,
            devils_advocate=self.devils_advocate,
            max_new_per_round=self.max_new_per_round,
            max_active=self.max_active,
        )
        clusterer = FactionClusterer(self.provider)
        consensus = ConsensusChecker(self.provider, stability_rounds=self.stability_rounds)

        tid = self.transcripts.create()
        self.provider.begin_question()
        aliases = make_aliases(self.council, seed=self.alias_seed)
        speakers = {alias: real for real, alias in aliases.items()}
        judges = Judges(self.judge_models, aliases)
        breakdown: dict[str, int] = {}
        last_mark = 0

        def mark(stage: str) -> None:
            nonlocal last_mark
            now = self.provider.question_report().requests
            breakdown[stage] = now - last_mark
            last_mark = now

        route = await self.router.route(question, force_protocol)
        self.transcripts.append(tid, {"stage": "route", **route.model_dump()})
        mark("route")

        value_map = ValueMap()
        if clarify_mode != "no-clarify":
            elicitation = await self.elicitor.elicit(question, self.council, mode=clarify_mode)
            value_map = elicitation.value_map
            if elicitation.questions:
                answers = on_questions(elicitation.questions) if on_questions else []
                if any(a.strip() for a in answers):
                    value_map = self.elicitor.apply_answers(elicitation.questions, answers)
                    self.transcripts.append(tid, {"stage": "answers", "answers": answers})
                else:
                    value_map = ValueMap(
                        assumptions=elicitation.value_map.assumptions,
                        open_ambiguities=[q.question for q in elicitation.questions],
                    )
            self.transcripts.append(tid, {"stage": "elicit", **elicitation.model_dump()})
        mark("elicit")

        positions = await extract_positions(
            self.provider, self.council, question, value_map, aliases,
        )
        self.transcripts.append(
            tid, {"stage": "positions", "positions": [p.model_dump() for p in positions]},
        )
        mark("positions")

        factions = await clusterer.cluster(positions, judges=judges, speakers=speakers)
        await self._name_factions(factions)
        self.transcripts.append(tid, {
            "stage": "factions",
            "factions": [f.model_dump() for f in factions],
            "prefilter_merges": clusterer.prefilter_merges,
        })
        mark("factions")

        rounds_taken = 0
        if route.protocol == Protocol.VOTE:
            strength = await consensus.classify(factions, judges=judges)
            zhoda = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
        elif route.protocol == Protocol.RED_TEAM:
            round_ = await debate.run_round(1, factions, speakers=speakers, judges=judges)
            self.transcripts.append(tid, {"stage": "round", **round_.model_dump()})
            rounds_taken = 1
            strength = await consensus.classify(factions, judges=judges)
            zhoda = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
        else:
            zhoda, strength = False, ConsensusStrength.SPLIT
            for rounds_taken in range(1, self.rounds_cap + 1):
                round_ = await debate.run_round(
                    rounds_taken, factions, speakers=speakers, judges=judges,
                )
                self.transcripts.append(tid, {"stage": "round", **round_.model_dump()})
                factions = [f for f in factions if f.members]
                zhoda, strength = await consensus.check(factions, judges=judges)
                self.transcripts.append(
                    tid, {"stage": "consensus", "zhoda": zhoda, "strength": str(strength)},
                )
                if zhoda:
                    break
            mark("debate")
            if not zhoda and strength == ConsensusStrength.SPLIT:
                strength = ConsensusStrength.DEADLOCK

        # escalation (round-9 §4): the appellate model decides a deadlock
        escalated_to = None
        appeal_decision = None
        if strength == ConsensusStrength.DEADLOCK and self.escalation_model:
            escalated_to = self.escalation_model
            appeal = await self.provider.ask_json(
                self.escalation_model,
                APPEAL_PROMPT.format(
                    question=question,
                    theses="; ".join(
                        f"{f.name}: {f.platform.thesis}" for f in factions if f.platform
                    ),
                    objections="; ".join(
                        c.claim for c in debate.objections
                        if c.status == ObjectionStatus.OPEN
                    ),
                ),
            )
            appeal_decision = appeal.get("decision")
            self.transcripts.append(
                tid, {"stage": "appeal", "model": self.escalation_model, **appeal},
            )

        divergences = clusterer.divergences + [
            Disagreement(topic=c.claim, factions=[c.target_faction], summary=c.claim)
            for c in debate.objections
            if c.status == ObjectionStatus.OPEN
        ]

        cost = self.provider.question_report()
        cost.breakdown = breakdown
        verdict = self.verdicts.build(
            factions, strength, route.protocol, value_map,
            zhoda_reached=zhoda,
            router_confidence=route.confidence,
            rounds_taken=rounds_taken,
            transcript_id=tid,
            switches=debate.switches,
            cost=cost,
            divergences=divergences,
        )
        if appeal_decision:
            verdict.decision = appeal_decision
        verdict.escalated_to = escalated_to
        # second render + explainability + ROI metric (values №1–№3)
        verdict.plan_contract = await render_plan_contract(
            self.provider, self.chairman, verdict, debate.objections, factions,
        )
        verdict.dead_ends_prevented = len(verdict.plan_contract.rejected_paths)
        verdict.decision_tree = build_decision_tree(
            factions, debate.objections, debate.switches, verdict.decision,
        ).model_dump()
        mark("render")
        self.transcripts.append(tid, {"stage": "verdict", "verdict": verdict.model_dump()})
        return verdict

    async def _name_factions(self, factions: list[Faction]) -> None:
        """The chairman names factions — sanitized and UNIQUE."""
        lines = "\n".join(f"- {f.name}: {f.platform.thesis}" for f in factions if f.platform)
        if not lines:
            return
        try:
            names = await self.provider.ask_json(self.chairman, NAMING_PROMPT.format(lines=lines))
        except Exception:
            return
        seen: set[str] = set()
        for faction in factions:
            name = str(names.get(faction.name, "")).strip().strip('"\'').replace("\n", " ")
            name = name[:MAX_FACTION_NAME]
            if name and name not in seen:
                seen.add(name)
                faction.name = name
