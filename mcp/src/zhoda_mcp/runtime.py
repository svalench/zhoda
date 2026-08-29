"""Инструменты MCP: JSON-ответы, без простыни текста. Ядро in-process."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from zhoda_core.config import load_council_config, make_engine, make_provider
from zhoda_core.elicitor import Elicitor
from zhoda_core.env import load_zhoda_env
from zhoda_core.models import Protocol, ValueMap, Verdict
from zhoda_core.progress import ProgressEvent
from zhoda_core.providers.openrouter import (
    BudgetExceededError,
    QuotaExceededError,
    ZhodaProviderError,
)
from zhoda_core.reputation import Domain, ReputationStorage
from zhoda_core.transcripts import TranscriptStore

from .estimate import estimate_cost
from .render import last_verdict, render_transcript_md

KNOWN_PROTOCOLS = {p.value for p in Protocol}
KNOWN_DOMAINS = {d.value for d in Domain}

_STAGES = ("route", "elicit", "positions", "factions", "round", "consensus", "verdict")


def _quota(exc: BaseException) -> dict[str, str]:
    return {
        "error": "quota_exceeded",
        "message": str(exc),
        "hint": "wait for the daily reset or add credits; never silently degrade",
    }


def _provider_error(exc: BaseException) -> dict[str, str]:
    text = str(exc)
    if "OPENROUTER_API_KEY" in text:
        return {
            "error": "missing_api_key",
            "message": text,
            "hint": "set OPENROUTER_API_KEY (BYOK) in the host MCP env",
        }
    return {"error": "provider_error", "message": text}


class Runtime:
    """Сессия MCP: конфиг совета + сторы. Дельберация только через zhoda-core."""

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        transcripts: TranscriptStore,
        reputation: ReputationStorage,
        remote_core_url: str | None = None,
        session_factory: Callable[[int | None], tuple[Any, Any]] | None = None,
        elicit_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self.transcripts = transcripts
        self.reputation = reputation
        self.remote_core_url = remote_core_url
        self.session_factory = session_factory
        self.elicit_fn = elicit_fn

    @classmethod
    def from_env(cls) -> Runtime:
        load_zhoda_env()
        path = os.environ.get("ZHODA_COUNCIL", "zhoda.yaml")
        cfg = load_council_config(path)
        budget_raw = os.environ.get("ZHODA_BUDGET_USD")
        if budget_raw is not None and budget_raw != "":
            cfg = {**cfg, "budget_per_question_usd": float(budget_raw)}
        transcripts_dir = os.environ.get("ZHODA_TRANSCRIPTS_DIR") or str(
            cfg.get("transcripts_dir", "transcripts")
        )
        return cls(
            cfg,
            transcripts=TranscriptStore(transcripts_dir),
            reputation=ReputationStorage(),
            remote_core_url=os.environ.get("ZHODA_CORE_URL"),
        )

    def _guard(self) -> dict[str, str] | None:
        if self.remote_core_url:
            return {
                "error": "remote_core_unwired",
                "message": (
                    "ZHODA_CORE_URL is reserved for a future remote core; "
                    "unset it to use in-process zhoda-core"
                ),
            }
        return None

    def estimate(self, protocol: str | None = None) -> dict[str, Any]:
        proto = protocol or "debate"
        if proto not in KNOWN_PROTOCOLS:
            return {
                "error": "invalid_protocol",
                "message": f"unknown protocol {proto!r}",
                "known": sorted(KNOWN_PROTOCOLS),
            }
        return {"status": "estimate", "estimate": estimate_cost(self.cfg, proto)}

    async def clarify(self, question: str, *, context: str = "") -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        if self.elicit_fn is not None:
            stub: dict[str, Any] = await self.elicit_fn(question, context)
            stub["estimate"] = estimate_cost(self.cfg)
            return stub
        try:
            provider = make_provider(self.cfg)
        except ValueError as exc:
            return _provider_error(exc)
        try:
            elicitor = Elicitor(
                provider,
                ambiguity_threshold=float(self.cfg.get("ambiguity_threshold", 0.6)),
            )
            chairman = str(self.cfg.get("chairman", self.cfg["council"][0]))
            elicitation = await elicitor.elicit(
                question,
                list(self.cfg["council"]),
                mode="smart",
                context=context,
                dedup_model=chairman,
            )
            to_ask = elicitation.questions + elicitation.pending
            return {
                "questions": [q.model_dump() for q in to_ask],
                "all_questions": [q.model_dump() for q in elicitation.all_questions],
                "ambiguity_score": elicitation.ambiguity_score,
                "open_ambiguities": list(elicitation.value_map.open_ambiguities),
                "estimate": estimate_cost(self.cfg),
            }
        except QuotaExceededError as exc:
            return _quota(exc)
        except ZhodaProviderError as exc:
            return _provider_error(exc)
        finally:
            await provider.close()

    async def deliberate(
        self,
        question: str,
        *,
        confirm: bool = False,
        value_map: dict[str, Any] | None = None,
        rounds_cap: int | None = None,
        protocol: str | None = None,
        context: str = "",
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        if protocol is not None and protocol not in KNOWN_PROTOCOLS:
            return {
                "error": "invalid_protocol",
                "message": f"unknown protocol {protocol!r}",
                "known": sorted(KNOWN_PROTOCOLS),
            }
        if not confirm:
            return self.estimate(protocol)
        supplied: ValueMap | None = None
        if value_map is not None:
            supplied = ValueMap.model_validate(value_map)
        engine, provider = self._open_session(rounds_cap)
        try:
            verdict: Verdict = await engine.deliberate(
                question,
                force_protocol=Protocol(protocol) if protocol else None,
                clarify_mode="no-clarify" if supplied is not None else "auto-clarify",
                on_progress=on_progress,
                context=context,
                value_map=supplied,
            )
            return {"status": "verdict", "verdict": verdict.model_dump()}
        except QuotaExceededError as exc:
            return _quota(exc)
        except BudgetExceededError as exc:
            return {"error": "budget_exceeded", "message": str(exc)}
        except ZhodaProviderError as exc:
            return _provider_error(exc)
        except ValueError as exc:
            return _provider_error(exc)
        finally:
            await provider.close()

    def verdict(self, transcript_id: str) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        try:
            events = self.transcripts.read(transcript_id)
        except OSError:
            return {"error": "not_found", "transcript_id": transcript_id}
        found = last_verdict(events)
        if found is None:
            return {"error": "not_found", "transcript_id": transcript_id}
        return {"status": "verdict", "verdict": found}

    def transcript(self, transcript_id: str, fmt: str = "json") -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        if fmt not in {"json", "md"}:
            return {
                "error": "invalid_format",
                "message": f"format must be json or md, got {fmt!r}",
            }
        try:
            events = self.transcripts.read(transcript_id)
        except OSError:
            return {"error": "not_found", "transcript_id": transcript_id}
        if fmt == "md":
            return {
                "status": "transcript",
                "transcript_id": transcript_id,
                "format": "md",
                "markdown": render_transcript_md(transcript_id, events),
            }
        return {
            "status": "transcript",
            "transcript_id": transcript_id,
            "format": "json",
            "events": events,
        }

    def reputation_report(self, domain: str | None = None) -> dict[str, Any]:
        blocked = self._guard()
        if blocked is not None:
            return blocked
        data = self.reputation.load().to_dict()
        if domain is None or domain == "":
            return {"status": "reputation", **data}
        if domain not in KNOWN_DOMAINS:
            return {
                "error": "unknown_domain",
                "domain": domain,
                "known": sorted(KNOWN_DOMAINS),
            }
        ratings: dict[str, dict[str, float]] = {}
        for model, row in data["ratings"].items():
            if domain in row:
                ratings[model] = {domain: row[domain]}
        return {"status": "reputation", "domain": domain, "ratings": ratings}

    def _open_session(self, rounds_cap: int | None) -> tuple[Any, Any]:
        if self.session_factory is not None:
            return self.session_factory(rounds_cap)
        provider = make_provider(self.cfg)
        engine = make_engine(
            self.cfg,
            provider,
            transcripts_dir=str(self.transcripts.dir),
            rounds_cap=rounds_cap,
        )
        return engine, provider


def progress_sink(ctx: Any) -> Callable[[ProgressEvent], None] | None:
    """Синхронный callback для engine: нотификации MCP не блокируют раунд."""
    if ctx is None:
        return None

    def emit(event: ProgressEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(_notify(ctx, event))

    return emit


async def _notify(ctx: Any, event: ProgressEvent) -> None:
    try:
        await ctx.info(event.message)
        if event.done and event.stage in _STAGES:
            await ctx.report_progress(_STAGES.index(event.stage) + 1, len(_STAGES))
    except Exception:  # noqa: BLE001 — хост без progress не должен ронять дебат
        return
