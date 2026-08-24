"""Orchestration state machine: router -> elicitor -> positions -> factions
(chairman names them) -> debate rounds (revise BEFORE switches) -> verdict
-> plan contract (ONLY on zhoda) + decision tree.

Round-10: paths_rejected is honest (rejections by a REACHED consensus only);
the plan contract never renders on a non-zhoda verdict; an appellate
decision carries decision_origin="appeal_without_consensus" — a single
model's fiat is labeled, never mistaken for zhoda.
"""

from collections.abc import Callable

from .anonymize import make_aliases
from .consensus import ConsensusChecker
from .debate import DebateEngine
from .elicitor import ClarifyingQuestion, Elicitor, grounding_need
from .factions import ADVOCATE_ALIAS, Faction, FactionClusterer
from .judges import Judges
from .models import (
    ConsensusStrength,
    CostReport,
    Disagreement,
    ObjectionStatus,
    Position,
    Protocol,
    ValueMap,
    Verdict,
)
from .plan import collect_rejected_paths, render_plan_contract
from .positions import extract_positions
from .progress import ProgressEvent
from .providers.openrouter import OpenRouterProvider
from .router import ProtocolRouter
from .transcripts import TranscriptStore
from .tree import build_decision_tree
from .verdict import VerdictBuilder, synthesize_decision

NAMING_PROMPT = """Name each faction descriptively (e.g. \"Pragmatists\", \"Maximalists\").
Factions:
{lines}

ONLY valid JSON: {{"<current name>": "<descriptive name>"}}"""

APPEAL_PROMPT = """The council deadlocked on: {question}
Final theses: {theses}
Unclosed objections: {objections}

You are the appellate judge. Decide, and state which arguments won.
ONLY valid JSON: {{"decision": "...", "winning_arguments": ["..."]}}"""

OPPOSITION_PROMPT = """Spawn an opposition faction. The council currently holds ONE position.
Write a genuine alternative with a DIFFERENT primary recommended action — not a nitpick.

Question: {question}
Current thesis: {thesis}
Current answer: {answer}

ONLY valid JSON:
{{"thesis": "...", "answer": "...",
  "claims": [{{"claim": "...", "evidence_url": null, "confidence": 0.0}}],
  "falsifiability": "...", "confidence": 0.0}}"""

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
        on_progress: Callable[[ProgressEvent], None] | None = None,
        context: str = "",
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

        def emit(stage: str, message: str, *, done: bool = False) -> None:
            if on_progress is not None:
                on_progress(ProgressEvent(stage=stage, message=message, done=done))

        emit("route", "routing protocol…")
        route = await self.router.route(question, force_protocol)
        self.transcripts.append(tid, {"stage": "route", **route.model_dump()})
        mark("route")
        emit("route", f"protocol={route.protocol}", done=True)

        value_map = ValueMap()
        elicit_questions: list[ClarifyingQuestion] = []
        elicit_answers: list[str] = []
        if clarify_mode != "no-clarify":
            emit("elicit", "eliciting clarifying questions…")
            elicitation = await self.elicitor.elicit(
                question,
                self.council,
                mode=clarify_mode,
                context=context,
                dedup_model=self.chairman,
            )
            leftover = list(elicitation.value_map.open_ambiguities)
            elicit_questions = elicitation.all_questions or elicitation.questions
            if elicitation.questions:
                elicit_answers = on_questions(elicitation.questions) if on_questions else []
                value_map = self.elicitor.apply_answers(elicitation.questions, elicit_answers)
                for item in leftover:
                    if item not in value_map.open_ambiguities:
                        value_map.open_ambiguities.append(item)
                self.transcripts.append(tid, {"stage": "answers", "answers": elicit_answers})
            else:
                value_map = elicitation.value_map
            self.transcripts.append(tid, {"stage": "elicit", **elicitation.model_dump()})
            emit(
                "elicit",
                f"elicit: {len(elicitation.questions)} questions",
                done=True,
            )
        mark("elicit")

        need = grounding_need(question, elicit_questions, elicit_answers, context)
        if need is not None:
            cost = self.provider.question_report()
            cost.breakdown = breakdown
            verdict = Verdict(
                decision=f"INSUFFICIENT_CONTEXT: {need}",
                zhoda_reached=False,
                consensus_strength=ConsensusStrength.SPLIT,
                protocol=route.protocol,
                router_confidence=route.confidence,
                value_map=value_map,
                cost=cost,
                transcript_id=tid,
                insufficient_context=True,
            )
            self.transcripts.append(
                tid,
                {"stage": "verdict", "verdict": verdict.model_dump(), "insufficient_context": True},
            )
            emit("verdict", "insufficient_context — no debate", done=True)
            return verdict

        emit("positions", f"collecting positions ({len(self.council)} models)…")
        positions = await extract_positions(
            self.provider,
            self.council,
            question,
            value_map,
            aliases,
            context=context,
        )
        self.transcripts.append(
            tid,
            {"stage": "positions", "positions": [p.model_dump() for p in positions]},
        )
        mark("positions")
        emit("positions", f"positions: {len(positions)} models", done=True)

        emit("factions", "clustering factions…")
        factions = await clusterer.cluster(positions, judges=judges, speakers=speakers)
        if route.protocol == Protocol.DEBATE and len(factions) == 1 and self.devils_advocate:
            opposition = await self._spawn_opposition(question, factions[0], speakers)
            if opposition is not None:
                factions.append(opposition)
                self.transcripts.append(
                    tid,
                    {
                        "stage": "opposition_spawned",
                        "faction": opposition.model_dump(),
                    },
                )
        await self._name_factions(factions)
        self.transcripts.append(
            tid,
            {
                "stage": "factions",
                "factions": [f.model_dump() for f in factions],
                "prefilter_merges": clusterer.prefilter_merges,
            },
        )
        mark("factions")
        emit("factions", f"factions: {len(factions)}", done=True)

        rounds_taken = 0
        if route.protocol == Protocol.VOTE:
            emit("consensus", "classifying agreement…")
            strength = await consensus.classify(factions, judges=judges)
            zhoda = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
            emit("consensus", f"zhoda={zhoda} {strength}", done=True)
        elif route.protocol == Protocol.RED_TEAM:
            emit("round", "round 1/1 (red_team)…")
            round_ = await debate.run_round(1, factions, speakers=speakers, judges=judges)
            self.transcripts.append(tid, {"stage": "round", **round_.model_dump()})
            rounds_taken = 1
            emit("round", "round 1/1 done", done=True)
            emit("consensus", "classifying agreement…")
            strength = await consensus.classify(factions, judges=judges)
            zhoda = strength in (ConsensusStrength.UNANIMOUS, ConsensusStrength.MAJORITY)
            emit("consensus", f"zhoda={zhoda} {strength}", done=True)
        else:
            zhoda, strength = False, ConsensusStrength.SPLIT
            if len(factions) == 1:
                rounds_taken = 0
                strength = ConsensusStrength.UNANIMOUS
                zhoda = True
                self.transcripts.append(
                    tid,
                    {
                        "stage": "consensus",
                        "zhoda": True,
                        "strength": str(strength),
                        "fast_pass": "unanimity_at_birth",
                    },
                )
                emit("consensus", "fast_pass unanimity_at_birth", done=True)
            else:
                for rounds_taken in range(1, self.rounds_cap + 1):
                    emit("round", f"round {rounds_taken}/{self.rounds_cap}…")
                    round_ = await debate.run_round(
                        rounds_taken,
                        factions,
                        speakers=speakers,
                        judges=judges,
                    )
                    self.transcripts.append(tid, {"stage": "round", **round_.model_dump()})
                    emit("round", f"round {rounds_taken}/{self.rounds_cap} done", done=True)
                    factions = [f for f in factions if f.members]
                    emit("consensus", "checking consensus…")
                    zhoda, strength = await consensus.check(factions, judges=judges)
                    self.transcripts.append(
                        tid,
                        {"stage": "consensus", "zhoda": zhoda, "strength": str(strength)},
                    )
                    emit("consensus", f"zhoda={zhoda} {strength}", done=True)
                    if zhoda:
                        break
            mark("debate")
            if not zhoda and strength == ConsensusStrength.SPLIT:
                strength = ConsensusStrength.DEADLOCK

        # escalation: the appellate model decides a deadlock — LABELED (round-10 §2)
        escalated_to = None
        appeal_decision = None
        if strength == ConsensusStrength.DEADLOCK and self.escalation_model:
            emit("appeal", "appellate review…")
            escalated_to = self.escalation_model
            appeal = await self.provider.ask_json(
                self.escalation_model,
                APPEAL_PROMPT.format(
                    question=question,
                    theses="; ".join(
                        f"{f.name}: {f.platform.thesis}" for f in factions if f.platform
                    ),
                    objections="; ".join(
                        c.claim for c in debate.objections if c.status == ObjectionStatus.OPEN
                    ),
                ),
            )
            appeal_decision = appeal.get("decision")
            self.transcripts.append(
                tid,
                {"stage": "appeal", "model": self.escalation_model, **appeal},
            )
            emit("appeal", "appeal recorded", done=True)

        divergences = clusterer.divergences + [
            Disagreement(topic=c.claim, factions=[c.target_faction], summary=c.claim)
            for c in debate.objections
            if c.status == ObjectionStatus.OPEN
        ]

        verdict = self.verdicts.build(
            factions,
            strength,
            route.protocol,
            value_map,
            zhoda_reached=zhoda,
            router_confidence=route.confidence,
            rounds_taken=rounds_taken,
            transcript_id=tid,
            switches=debate.switches,
            cost=CostReport(),
            divergences=divergences,
        )
        if appeal_decision:
            verdict.decision = appeal_decision
            verdict.decision_origin = "appeal_without_consensus"  # labeled fiat
        verdict.escalated_to = escalated_to
        leading = max(factions, key=lambda f: len(f.members))
        if zhoda and verdict.decision_origin == "council" and leading.platform is not None:
            try:
                verdict.decision = await synthesize_decision(
                    self.provider,
                    self.chairman,
                    question=question,
                    leading=leading,
                    objections=debate.objections,
                    value_map=value_map,
                )
            except (ValueError, TypeError, KeyError, RuntimeError):
                verdict.decision = leading.platform.thesis
        # honest metric (round-10 §3): rejections by a REACHED consensus only
        verdict.paths_rejected = collect_rejected_paths(
            factions,
            debate.objections,
            zhoda_reached=zhoda,
        )
        # the plan contract renders ONLY on zhoda (round-10 §2)
        emit("verdict", "rendering verdict…")
        if zhoda:
            verdict.plan_contract = await render_plan_contract(
                self.provider,
                self.chairman,
                verdict,
            )
        verdict.decision_tree = build_decision_tree(
            factions,
            debate.objections,
            debate.switches,
            verdict.decision,
        ).model_dump()
        mark("render")
        cost = self.provider.question_report()
        cost.breakdown = breakdown
        verdict.cost = cost
        self.transcripts.append(tid, {"stage": "verdict", "verdict": verdict.model_dump()})
        emit("verdict", f"verdict zhoda={zhoda}", done=True)
        return verdict

    async def _name_factions(self, factions: list[Faction]) -> None:
        """The chairman names factions — sanitized and UNIQUE."""
        lines = "\n".join(f"- {f.name}: {f.platform.thesis}" for f in factions if f.platform)
        if not lines:
            return
        try:
            names = await self.provider.ask_json(self.chairman, NAMING_PROMPT.format(lines=lines))
        except Exception:  # noqa: BLE001
            return
        seen: set[str] = set()
        for faction in factions:
            name = str(names.get(faction.name, "")).strip().strip("\"'").replace("\n", " ")
            name = name[:MAX_FACTION_NAME]
            if name and name not in seen:
                seen.add(name)
                faction.name = name

    async def _spawn_opposition(
        self,
        question: str,
        leading: Faction,
        speakers: dict[str, str],
    ) -> Faction | None:
        """Адвокат порождает вторую фракцию с другим primary action."""
        if leading.platform is None:
            return None
        actor = self.council[0]
        speakers[ADVOCATE_ALIAS] = actor
        try:
            data = await self.provider.ask_json(
                actor,
                OPPOSITION_PROMPT.format(
                    question=question,
                    thesis=leading.platform.thesis,
                    answer=leading.platform.answer,
                ),
            )
            platform = Position(model=ADVOCATE_ALIAS, **data)
        except Exception:  # noqa: BLE001
            return None
        if not platform.thesis.strip() or not platform.answer.strip():
            return None
        return Faction(
            name=ADVOCATE_ALIAS,
            members=[ADVOCATE_ALIAS],
            platform=platform,
            synthetic=True,
        )
