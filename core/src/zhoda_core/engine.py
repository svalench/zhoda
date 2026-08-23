"""Orchestration state machine: router -> elicitor -> positions -> factions
(chairman names them) -> debate rounds (revise BEFORE switches) -> verdict.

Trust is CONCENTRATED in three auditable points (round-3 §6), guarded by
conflict-free judge pairs (round-5 §2). Round-6: smart elicitation without a
callback DEGRADES to open_ambiguities instead of dropping the questions (§4);
router classifiers are mandatory config, never council order (§5); the
chairman earns its keep by naming factions.
"""

from collections.abc import Callable

from .anonymize import make_aliases
from .consensus import ConsensusChecker
from .debate import DebateEngine
from .elicitor import ClarifyingQuestion, Elicitor
from .factions import Faction, FactionClusterer
from .judges import Judges
from .models import ConsensusStrength, Disagreement, ObjectionStatus, Protocol, ValueMap, Verdict
from .positions import extract_positions
from .providers.openrouter import OpenRouterProvider
from .router import ProtocolRouter
from .transcripts import TranscriptStore
from .verdict import VerdictBuilder

NAMING_PROMPT = """Name each faction descriptively (e.g. \"Pragmatists\", \"Maximalists\").
Factions:
{lines}

ONLY valid JSON: {{"<current name>": "<descriptive name>"}}"""


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
        transcripts_dir: str = "transcripts",
        alias_seed: int | None = None,  # testability hook
    ) -> None:
        if len(council) < 2:
            raise ValueError("council needs at least 2 models")
        if router_classifiers is None:
            raise ValueError("router_classifiers are mandatory config (round-6 §5)")
        self.provider = provider
        self.council = council
        self.chairman = chairman
        self.judge_models = judges
        self.rounds_cap = rounds_cap
        self.alias_seed = alias_seed
        self.transcripts = TranscriptStore(transcripts_dir)
        self.router = ProtocolRouter(provider, classifiers=router_classifiers)
        self.elicitor = Elicitor(provider)
        self.clusterer = FactionClusterer(provider)
        self.debate = DebateEngine(provider, devils_advocate=devils_advocate)
        self.consensus = ConsensusChecker(provider, stability_rounds=stability_rounds)
        self.verdicts = VerdictBuilder()

    async def deliberate(
        self,
        question: str,
        *,
        force_protocol: Protocol | None = None,
        clarify_mode: str = "smart",
        on_questions: Callable[[list[ClarifyingQuestion]], list[str]] | None = None,
    ) -> Verdict:
        tid = self.transcripts.create()
        self.provider.begin_question()
        aliases = make_aliases(self.council, seed=self.alias_seed)
        speakers = {alias: real for real, alias in aliases.items()}
        judges = Judges(self.judge_models, aliases)

        route = await self.router.route(question, force_protocol)
        self.transcripts.append(tid, {"stage": "route", **route.model_dump()})

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
                    # degrade honestly (round-6 §4): never drop raised ambiguities
                    value_map = ValueMap(
                        assumptions=elicitation.value_map.assumptions,
                        open_ambiguities=[q.question for q in elicitation.questions],
                    )
            self.transcripts.append(tid, {"stage": "elicit", **elicitation.model_dump()})

        positions = await extract_positions(
            self.provider, self.council, question, value_map, aliases,
        )
        self.transcripts.append(
            tid, {"stage": "positions", "positions": [p.model_dump() for p in positions]},
        )

        factions = await self.clusterer.cluster(positions, judges=judges, speakers=speakers)
        await self._name_factions(factions)
        self.transcripts.append(
            tid, {"stage": "factions", "factions": [f.model_dump() for f in factions]},
        )

        rounds_taken = 0
        if route.protocol == Protocol.VOTE:
            strength = await self.consensus.classify(factions, judges=judges)
            zhoda = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
        elif route.protocol == Protocol.RED_TEAM:
            round_ = await self.debate.run_round(1, factions, speakers=speakers, judges=judges)
            self.transcripts.append(tid, {"stage": "round", **round_.model_dump()})
            rounds_taken = 1
            strength = await self.consensus.classify(factions, judges=judges)
            zhoda = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
        else:
            zhoda, strength = False, ConsensusStrength.SPLIT
            for rounds_taken in range(1, self.rounds_cap + 1):
                round_ = await self.debate.run_round(
                    rounds_taken, factions, speakers=speakers, judges=judges,
                )
                self.transcripts.append(tid, {"stage": "round", **round_.model_dump()})
                factions = [f for f in factions if f.members]
                zhoda, strength = await self.consensus.check(factions, judges=judges)
                self.transcripts.append(
                    tid, {"stage": "consensus", "zhoda": zhoda, "strength": str(strength)},
                )
                if zhoda:
                    break

        divergences = self.clusterer.divergences + [
            Disagreement(topic=c.claim, factions=[c.target_faction], summary=c.claim)
            for c in self.debate.objections
            if c.status == ObjectionStatus.OPEN
        ]

        verdict = self.verdicts.build(
            factions, strength, route.protocol, value_map,
            zhoda_reached=zhoda,
            router_confidence=route.confidence,
            rounds_taken=rounds_taken,
            transcript_id=tid,
            switches=self.debate.switches,
            cost=self.provider.question_report(),
            divergences=divergences,
        )
        self.transcripts.append(tid, {"stage": "verdict", "verdict": verdict.model_dump()})
        return verdict

    async def _name_factions(self, factions: list[Faction]) -> None:
        """The chairman earns its config: descriptive faction names."""
        lines = "\n".join(f"- {f.name}: {f.platform.thesis}" for f in factions if f.platform)
        if not lines:
            return
        try:
            names = await self.provider.ask_json(self.chairman, NAMING_PROMPT.format(lines=lines))
            for faction in factions:
                if faction.name in names:
                    faction.name = names[faction.name]
        except Exception:
            pass  # naming is cosmetic; aliases remain
