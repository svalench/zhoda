"""Orchestration state machine: router -> elicitor -> positions -> factions
(chairman names them) -> debate rounds (revise BEFORE switches) -> verdict
-> plan contract (ONLY on zhoda) + decision tree.

Round-10: paths_rejected is honest (rejections by a REACHED consensus only);
the plan contract never renders on a non-zhoda verdict; an appellate
decision carries decision_origin="appeal_without_consensus" — a single
model's fiat is labeled, never mistaken for zhoda. Headcount majority at
rounds_cap is decision_origin="majority_at_cap": dissent, not zhoda.
"""

from collections.abc import Callable

from .anonymize import content_alias_seed, make_aliases
from .actions import attach_action, inspect_premise, option_catalog
from .consensus import ConsensusChecker
from .debate import DebateEngine
from .elicitor import (
    DEFAULT_MAX_ELICIT_TURNS,
    ClarifyingQuestion,
    ElicitationResult,
    Elicitor,
    grounding_need,
)
from .factions import ADVOCATE_ALIAS, Faction, FactionClusterer
from .guards import (
    challenges_loaded_premise,
    loaded_premise_ambiguities,
)
from .judges import Judges
from .models import (
    ConsensusStrength,
    CostReport,
    Disagreement,
    ObjectionStatus,
    PremiseRole,
    Protocol,
    ValueMap,
    Verdict,
    bind_user_context,
)
from .plan import collect_rejected_paths, render_plan_contract
from .positions import extract_positions
from .progress import ProgressEvent
from .providers.openrouter import OpenRouterProvider, make_cache_key
from .router import ProtocolRouter
from .transcripts import TranscriptStore
from .tree import build_decision_tree
from .verdict import VerdictBuilder, synthesize_decision
from .stage_dtos import DecisionVote, parse_stage, position_from_model

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
If the question embeds a loaded premise (always/never/since/given that/why is),
the alternative must still not treat that premise as a fact.

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
        max_elicit_turns: int = DEFAULT_MAX_ELICIT_TURNS,
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
        self.max_elicit_turns = max_elicit_turns
        self.escalation_model = escalation_model
        self.alias_seed = alias_seed
        self.transcripts = TranscriptStore(transcripts_dir)
        self.last_transcript_id: str | None = None
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
        value_map: ValueMap | None = None,
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

        tid = self.transcripts.create(
            {"question": question, "clarify_mode": clarify_mode}
        )
        self.last_transcript_id = tid
        try:
            return await self._deliberate_body(
                tid,
                question,
                debate,
                clusterer,
                consensus,
                force_protocol=force_protocol,
                clarify_mode=clarify_mode,
                on_questions=on_questions,
                on_progress=on_progress,
                context=context,
                value_map=value_map,
            )
        except Exception as exc:
            # Падение до verdict не оставляет пустой jsonl.
            self.transcripts.append(
                tid,
                {
                    "stage": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:800],
                },
            )
            raise

    async def _deliberate_body(
        self,
        tid: str,
        question: str,
        debate: DebateEngine,
        clusterer: FactionClusterer,
        consensus: ConsensusChecker,
        *,
        force_protocol: Protocol | None,
        clarify_mode: str,
        on_questions: Callable[[list[ClarifyingQuestion]], list[str]] | None,
        on_progress: Callable[[ProgressEvent], None] | None,
        context: str,
        value_map: ValueMap | None,
    ) -> Verdict:
        self.provider.begin_question()
        catalog = option_catalog(question)
        debate.catalog = catalog
        clusterer.catalog = catalog
        consensus.catalog = catalog
        seed = (
            self.alias_seed
            if self.alias_seed is not None
            else content_alias_seed(question, self.council, context=context)
        )
        aliases = make_aliases(self.council, seed=seed)
        speakers = {alias: real for real, alias in aliases.items()}
        judges = Judges(self.judge_models, aliases)
        breakdown: dict[str, int] = {}
        cache_breakdown: dict[str, int] = {}
        last_mark = 0
        last_hits = 0

        def mark(stage: str) -> None:
            nonlocal last_mark, last_hits
            report = self.provider.question_report()
            breakdown[stage] = report.requests - last_mark
            cache_breakdown[stage] = report.cache_hits - last_hits
            last_mark = report.requests
            last_hits = report.cache_hits

        def emit(stage: str, message: str, *, done: bool = False) -> None:
            if on_progress is not None:
                on_progress(ProgressEvent(stage=stage, message=message, done=done))

        emit("route", "routing protocol…")
        route = await self.router.route(question, force_protocol)
        self.transcripts.append(tid, {"stage": "route", **route.model_dump()})
        mark("route")
        emit("route", f"protocol={route.protocol}", done=True)

        elicit_questions: list[ClarifyingQuestion] = []
        elicit_answers: list[str] = []
        pre_need = grounding_need(question, [], [], context)
        if value_map is not None:
            emit("elicit", "value_map provided — skip Stage 0", done=True)
        elif pre_need is not None and clarify_mode != "smart":
            # Объект уже ясно отсутствует — не тратим Stage 0, чтобы спросить его имя.
            value_map = ValueMap()
            emit("elicit", "elicit skipped — object missing", done=True)
        elif (
            route.protocol in (Protocol.VOTE, Protocol.RED_TEAM)
            and clarify_mode == "auto-clarify"
        ):
            # Однопроход: интервью не меняет NULL≠TRUE и не должно мыть находки
            # гипотезой «а вдруг sanitize». no-clarify и так молча пустой value_map.
            value_map = ValueMap()
            emit("elicit", "elicit skipped — single-pass protocol", done=True)
        elif clarify_mode != "no-clarify":
            emit("elicit", "eliciting clarifying questions…")

            def on_turn(turn: int, result: ElicitationResult) -> None:
                self.transcripts.append(
                    tid, {"stage": "elicit", "turn": turn, **result.model_dump()}
                )

            def stop_after_batch(
                qs: list[ClarifyingQuestion],
                ans: list[str],
                _vm: ValueMap,
            ) -> bool:
                return grounding_need(question, qs, ans, context) is not None

            session = await self.elicitor.interview(
                question,
                self.council,
                mode=clarify_mode,
                context=context,
                dedup_model=self.chairman,
                on_questions=on_questions,
                max_turns=self.max_elicit_turns,
                stop_after_batch=stop_after_batch,
                on_turn=on_turn,
            )
            elicit_questions = session.questions or session.all_questions
            elicit_answers = session.answers
            value_map = session.value_map
            if session.answers:
                self.transcripts.append(tid, {"stage": "answers", "answers": session.answers})
            emit(
                "elicit",
                f"elicit: {len(session.questions)} questions",
                done=True,
            )
        else:
            value_map = ValueMap()
        value_map = value_map.model_copy(
            update={
                "open_ambiguities": loaded_premise_ambiguities(
                    question, list(value_map.open_ambiguities)
                )
            }
        )
        mark("elicit")

        need = grounding_need(question, elicit_questions, elicit_answers, context)
        if need is not None:
            cost = self.provider.question_report()
            cost.breakdown = breakdown
            cost.cache_breakdown = cache_breakdown
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

        user_context = value_map.as_prompt_block()
        debate.user_context = user_context
        debate.question = question
        clusterer.user_context = user_context
        consensus.user_context = user_context
        consensus.question = question

        emit("positions", f"collecting positions ({len(self.council)} models)…")
        positions = await extract_positions(
            self.provider,
            self.council,
            question,
            value_map,
            aliases,
            context=context,
        )
        positions = [
            pos.model_copy(
                update={"action": attach_action(pos.thesis, pos.answer, catalog)}
            )
            for pos in positions
        ]
        self.transcripts.append(
            tid,
            {"stage": "positions", "positions": [p.model_dump() for p in positions]},
        )
        mark("positions")
        if breakdown["positions"] == 0 and cache_breakdown.get("positions", 0) > 0:
            emit("positions", f"positions: cached ({len(positions)} models)", done=True)
        else:
            emit("positions", f"positions: {len(positions)} models", done=True)

        emit("factions", "clustering factions…")
        factions = await clusterer.cluster(positions, judges=judges, speakers=speakers)
        for faction in factions:
            if faction.platform is None:
                continue
            faction.platform = faction.platform.model_copy(
                update={
                    "action": attach_action(
                        faction.platform.thesis,
                        faction.platform.answer,
                        catalog,
                        prior=faction.platform.action,
                    )
                }
            )
        if route.protocol == Protocol.DEBATE and len(factions) == 1 and self.devils_advocate:
            opposition = await self._spawn_opposition(
                question, factions[0], speakers, user_context,
            )
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
        majority_at_cap = False
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
            mark("debate")
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
                    if (
                        not zhoda
                        and rounds_taken == self.rounds_cap
                        and strength is ConsensusStrength.UNANIMOUS
                    ):
                        # Early-stop требует streak; на капе ждать нечего.
                        # Majority без all_agree — не згода (честный раскол).
                        zhoda = True
                    self.transcripts.append(
                        tid,
                        {
                            "stage": "consensus",
                            "zhoda": zhoda,
                            "strength": str(strength),
                            "parse_failures": [
                                f.model_dump() for f in consensus.parse_failures
                            ],
                        },
                    )
                    emit("consensus", f"zhoda={zhoda} {strength}", done=True)
                    if zhoda:
                        break
            mark("debate")
            if not zhoda and strength == ConsensusStrength.SPLIT:
                strength = ConsensusStrength.DEADLOCK
            elif not zhoda and strength is ConsensusStrength.MAJORITY:
                # Majority на капе — честный раскол, не згода и не апелляция.
                majority_at_cap = True

        # escalation: the appellate model decides a deadlock — LABELED (round-10 §2)
        escalated_to = None
        appeal_decision = None
        if strength == ConsensusStrength.DEADLOCK and self.escalation_model:
            emit("appeal", "appellate review…")
            escalated_to = self.escalation_model
            appeal_prompt = bind_user_context(
                APPEAL_PROMPT.format(
                    question=question,
                    theses="; ".join(
                        f"{f.name}: {f.platform.thesis}" for f in factions if f.platform
                    ),
                    objections="; ".join(
                        c.claim for c in debate.objections if c.status == ObjectionStatus.OPEN
                    ),
                ),
                user_context,
            )
            appeal = await self.provider.ask_json(
                self.escalation_model,
                appeal_prompt,
                cache_key=make_cache_key("appeal", self.escalation_model, appeal_prompt),
            )
            parsed_appeal = parse_stage(
                DecisionVote, appeal, stage="appeal", prompt=appeal_prompt,
            )
            appeal_decision = (
                parsed_appeal.value.decision if parsed_appeal.value is not None else None
            )
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

        leading = max(factions, key=lambda f: len(f.members))
        # Сикофанское принятие asked-proposition — не згода. Background given-that
        # не отменяет действие (B1).
        probe = inspect_premise(question)
        if (
            zhoda
            and leading.platform is not None
            and probe.role is PremiseRole.ASKED_PROPOSITION
            and not challenges_loaded_premise(leading.platform.thesis)
        ):
            zhoda = False
            majority_at_cap = True
            if strength is ConsensusStrength.UNANIMOUS:
                strength = ConsensusStrength.MAJORITY

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
            question=question,
        )
        if appeal_decision:
            verdict.decision = appeal_decision
            verdict.decision_origin = "appeal_without_consensus"  # labeled fiat
        elif majority_at_cap:
            verdict.decision_origin = "majority_at_cap"
        verdict.escalated_to = escalated_to
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
            question=question,
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
        cost.cache_breakdown = cache_breakdown
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
            names_prompt = NAMING_PROMPT.format(lines=lines)
            names = await self.provider.ask_json(
                self.chairman,
                names_prompt,
                cache_key=make_cache_key("name", self.chairman, names_prompt),
            )
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
        user_context: str,
    ) -> Faction | None:
        """Адвокат порождает вторую фракцию с другим primary action."""
        if leading.platform is None:
            return None
        actor = self.council[0]
        speakers[ADVOCATE_ALIAS] = actor
        try:
            opp_prompt = bind_user_context(
                OPPOSITION_PROMPT.format(
                    question=question,
                    thesis=leading.platform.thesis,
                    answer=leading.platform.answer,
                ),
                user_context,
            )
            data = await self.provider.ask_json(
                actor,
                opp_prompt,
                cache_key=make_cache_key("opp", actor, opp_prompt),
            )
            parsed = position_from_model(data, alias=ADVOCATE_ALIAS, prompt=opp_prompt)
            if parsed.value is None:
                return None
            platform = parsed.value.model_copy(
                update={
                    "action": attach_action(
                        parsed.value.thesis, parsed.value.answer, option_catalog(question)
                    )
                }
            )
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
