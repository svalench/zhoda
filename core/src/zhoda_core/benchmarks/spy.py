"""Runtime spies: declared spec vs фактические complete/ask_json.

Не второй mutable config. Обёртка вокруг того же provider, что у engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .cache_guard import replayed_without_http
from .spec import SpecMismatch


@dataclass
class CallRecord:
    model: str
    kind: str  # complete | ask_json
    cache_hit: bool = False
    max_tokens: int | None = None
    prompt_chars: int = 0
    failed: bool = False
    provider_request_id: str | None = None
    role: str = "engine"  # engine | evaluator


@dataclass
class CallSpy:
    allowed_models: tuple[str, ...]
    expected_max_tokens: int | None = None
    calls: list[CallRecord] = field(default_factory=list)

    def record(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    def models_called(self) -> tuple[str, ...]:
        seen: list[str] = []
        for rec in self.calls:
            if rec.model not in seen:
                seen.append(rec.model)
        return tuple(seen)

    def verify(self) -> None:
        allowed = set(self.allowed_models)
        unknown = sorted({c.model for c in self.calls if c.model not in allowed})
        if unknown:
            raise SpecMismatch(f"undeclared models in actual calls: {unknown}")
        if self.expected_max_tokens is not None:
            bad = [
                c for c in self.calls
                if c.kind == "complete"
                and c.max_tokens is not None
                and c.max_tokens != self.expected_max_tokens
            ]
            if bad:
                raise SpecMismatch(
                    f"max_tokens {bad[0].max_tokens} != spec {self.expected_max_tokens}"
                )


class SpyingProvider:
    """Делегирует в inner provider; пишет CallRecord. Не меняет budget/cache."""

    def __init__(self, inner: Any, spy: CallSpy, *, role: str = "engine") -> None:
        self.inner = inner
        self.spy = spy
        self.role = role

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def begin_question(self) -> Any:
        return self.inner.begin_question()

    def end_question(self) -> Any:
        end = getattr(self.inner, "end_question", None)
        if callable(end):
            return end()
        return None

    def question_report(self) -> Any:
        return self.inner.question_report()

    async def complete(
        self,
        model: str,
        prompt: str,
        *,
        cache_key: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        hits_before = _cache_hits(self.inner)
        recorded = max_tokens if max_tokens is not None else self.spy.expected_max_tokens
        try:
            kwargs: dict[str, Any] = {"cache_key": cache_key}
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
                text = await self.inner.complete(model, prompt, **kwargs)
            else:
                text = await self.inner.complete(model, prompt, cache_key=cache_key)
        except Exception:
            self.spy.record(
                CallRecord(
                    model=model, kind="complete", failed=True, role=self.role,
                    prompt_chars=len(prompt or ""),
                    max_tokens=recorded,
                )
            )
            raise
        hit = _cache_hits(self.inner) > hits_before
        self.spy.record(
            CallRecord(
                model=model,
                kind="complete",
                cache_hit=hit,
                max_tokens=recorded,
                prompt_chars=len(prompt or ""),
                failed=False,
                provider_request_id=_request_id(self.inner),
                role=self.role,
            )
        )
        return str(text)

    async def ask_json(
        self, model: str, prompt: str, *, cache_key: str | None = None,
    ) -> dict[str, object]:
        hits_before = _cache_hits(self.inner)
        try:
            data = await self.inner.ask_json(model, prompt, cache_key=cache_key)
        except Exception:
            self.spy.record(
                CallRecord(
                    model=model, kind="ask_json", failed=True, role=self.role,
                    prompt_chars=len(prompt or ""),
                )
            )
            raise
        hit = _cache_hits(self.inner) > hits_before
        self.spy.record(
            CallRecord(
                model=model,
                kind="ask_json",
                cache_hit=hit,
                prompt_chars=len(prompt or ""),
                provider_request_id=_request_id(self.inner),
                role=self.role,
            )
        )
        return {str(k): v for k, v in dict(data).items()}


def _cache_hits(provider: Any) -> int:
    cost = getattr(provider, "cost", None)
    if cost is None:
        return 0
    return int(getattr(cost, "cache_hits", 0) or 0)


def _request_id(provider: Any) -> str | None:
    rid = getattr(provider, "last_request_id", None)
    return str(rid) if rid else None


def usage_from_report(report: Any, *, role: str) -> dict[str, object]:
    """Engine/evaluator usage из CostReport. unknown usd не притворяется 0 matched."""
    usd = float(getattr(report, "usd", 0.0) or 0.0)
    status = str(getattr(report, "usd_status", "exact") or "exact")
    requests = int(getattr(report, "requests", 0) or 0)
    cache_hits = int(getattr(report, "cache_hits", 0) or 0)
    return {
        "role": role,
        "requests": requests,
        "cache_hits": cache_hits,
        "tokens_in": int(getattr(report, "tokens_in", 0) or 0),
        "tokens_out": int(getattr(report, "tokens_out", 0) or 0),
        "usd": usd,
        "usd_status": status,
        "usd_known": status == "exact",
        "latency_s": float(getattr(report, "latency_s", 0.0) or 0.0),
        "attempts": int(getattr(report, "attempts", 0) or 0),
        "failed": False,
        "replayed_without_http": replayed_without_http(requests, cache_hits),
    }


def combine_usage(records: Sequence[CallRecord], report: Any, *, role: str) -> dict[str, object]:
    base = usage_from_report(report, role=role) if report is not None else {
        "role": role,
        "requests": 0,
        "cache_hits": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "usd": 0.0,
        "usd_status": "unknown",
        "usd_known": False,
        "latency_s": 0.0,
        "attempts": 0,
        "failed": False,
        "replayed_without_http": False,
    }
    base["call_count"] = len([c for c in records if c.role == role])
    base["failed"] = any(c.failed for c in records if c.role == role)
    ids = [c.provider_request_id for c in records if c.role == role and c.provider_request_id]
    base["provider_request_ids"] = ids
    return base
