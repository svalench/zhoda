"""Orchestration state machine: router -> elicitor -> positions -> factions
-> debate rounds -> consensus -> verdict.

Trust is not eliminated, it is CONCENTRATED in three auditable points
(round-3 §6): router classification, objection closure, position comparison.
Each is a separate logged call in the transcript (хроніка), persisted
BEFORE the verdict is returned.

Round-4: aliases are per-deliberation (§7); budget is per-question via
provider.begin_question() (§1).
"""

from .anonymize import make_aliases
from .consensus import ConsensusChecker
from .debate import DebateEngine
from .elicitor import Elicitor
from .factions import FactionClusterer
from .models import ConsensusStrength, Protocol, ValueMap, Verdict
from .positions import extract_positions
from .providers.openrouter import OpenRouterProvider
from .router import ProtocolRouter
from .transcripts import TranscriptStore
from .verdict import VerdictBuilder


class ZhodaEngine:
    def __init__(
        self,
        provider: OpenRouterProvider,
        council: list[str],
        *,
        chairman: str,
        router_classifiers: tuple[str, str] | None = None,
        rounds_cap: int = 4,
        transcripts_dir: str = "transcripts",
    ) -> None:
        if len(council) < 2:
            raise ValueError("council needs at least 2 models")
        self.provider = provider
        self.council = council
        self.chairman = chairman
        self.rounds_cap = rounds_cap
        self.transcripts = TranscriptStore(transcripts_dir)
        self.router = ProtocolRouter(
            provider, classifiers=router_classifiers or (council[0], council[1]),
        )
        self.elicitor = Elicitor(provider)
        self.clusterer = FactionClusterer(provider, judge_model=chairman)
        self.debate = DebateEngine(provider, judge_model=chairman)
        self.consensus = ConsensusChecker(provider, judge_model=chairman)
        self.verdicts = VerdictBuilder()

    async def deliberate(
        self,
        question: str,
        *,
        force_protocol: Protocol | None = None,
        clarify_mode: str = "smart",
    ) -> Verdict:
        tid = self.transcripts.create()
        self.provider.begin_question()  # per-question budget delta (round-4 §1)
        aliases = make_aliases(self.council)  # per deliberation (round-4 §7)
        speakers = {alias: real for real, alias in aliases.items()}

        route = await self.router.route(question, force_protocol)
        self.transcripts.append(tid, {"stage": "route", **route.model_dump()})

        value_map = ValueMap()
        if clarify_mode != "no-clarify":
            elicitation = await self.elicitor.elicit(question, self.council, mode=clarify_mode)
            value_map = elicitation.value_map
            self.transcripts.append(tid, {"stage": "elicit", **elicitation.model_dump()})

        positions = await extract_positions(
            self.provider, self.council, question, value_map, aliases,
        )
        self.transcripts.append(
            tid, {"stage": "positions", "positions": [p.model_dump() for p in positions]},
        )

        factions = await self.clusterer.cluster(positions)
        self.transcripts.append(
            tid, {"stage": "factions", "factions": [f.model_dump() for f in factions]},
        )

        rounds_taken = 0
        if route.protocol == Protocol.VOTE:
            # cheap path: single check, stability rule does not apply
            _, strength = await self.consensus.check(factions)
            zhoda = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
        else:
            zhoda, strength = False, ConsensusStrength.SPLIT
            for rounds_taken in range(1, self.rounds_cap + 1):
                round_ = await self.debate.run_round(rounds_taken, factions, speakers=speakers)
                self.transcripts.append(tid, {"stage": "round", **round_.model_dump()})
                factions = [f for f in factions if f.members]  # drop empty factions
                zhoda, strength = await self.consensus.check(factions)
                self.transcripts.append(
                    tid, {"stage": "consensus", "zhoda": zhoda, "strength": str(strength)},
                )
                if zhoda:
                    break

        verdict = self.verdicts.build(
            factions, strength, route.protocol, value_map,
            zhoda_reached=zhoda,
            router_confidence=route.confidence,
            rounds_taken=rounds_taken,
            transcript_id=tid,
            switches=[s for r in [self.debate] for s in []] or [],  # collected below
            cost=self.provider.cost,
            divergences=self.clusterer.divergences,
        )
        verdict.switches = [
            s for s in getattr(self.debate, "_all_switches", [])
        ]
        self.transcripts.append(tid, {"stage": "verdict", "verdict": verdict.model_dump()})
        return verdict
