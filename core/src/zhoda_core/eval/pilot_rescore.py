"""Rescore live G report.json новым грейдером. Engine не вызываем."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from zhoda_core.benchmarks.cache_guard import COST_CACHED, COST_EXACT, cost_status_for
from zhoda_core.eval.gold import GoldRow
from zhoda_core.eval.grading import (
    GRADER_VERSION,
    GradeProvider,
    overlay_scored_rows,
)
from zhoda_core.eval.pilot import PublicCase
from zhoda_core.eval.rescore import unique_arm_rows
from zhoda_core.models import CostReport
from zhoda_core.providers.openrouter import BudgetExceededError

SCHEMA_V1 = "zhoda.eval.live_g.v1"
SCHEMA_RESCORE_V2 = "zhoda.eval.live_g.rescore.v2"
EVALUATOR_CAP_USD = 1.0
ARMS = ("zhoda", "short_review", "majority")
SOURCE_REPORT_SHA256 = "6bb6cfce0d24254695f325facd3b5b51d467433366b5abaad86e58dbbd984756"


class JudgeOnlyProvider:
    """Только roster.judges[0]. Совет/chairman — ошибка, не тихий fallback."""

    def __init__(
        self,
        inner: GradeProvider,
        allowed_model: str,
        *,
        cap_usd: float = EVALUATOR_CAP_USD,
    ) -> None:
        self._inner = inner
        self.allowed_model = allowed_model
        self.cap_usd = cap_usd

    async def ask_json(
        self,
        model: str,
        prompt: str,
        cache_key: str | None = None,
        **kwargs: object,
    ) -> object:
        del kwargs
        if model != self.allowed_model:
            raise ValueError(f"refusing non-judge model {model!r}; allowed {self.allowed_model!r}")
        spent = self._spent()
        if spent >= self.cap_usd:
            raise BudgetExceededError(
                f"evaluator cap ${self.cap_usd:.2f} reached (spent ${spent:.4f})"
            )
        return await self._inner.ask_json(model, prompt, cache_key=cache_key)

    def begin_question(self) -> object:
        inner = self._inner
        if not hasattr(inner, "begin_question"):
            return None
        fn = inner.begin_question
        if not callable(fn):
            return None
        return fn()

    def end_question(self) -> None:
        inner = self._inner
        if not hasattr(inner, "end_question"):
            return
        fn = inner.end_question
        if callable(fn):
            fn()

    def question_report(self) -> object:
        inner = self._inner
        if not hasattr(inner, "question_report"):
            return None
        fn = inner.question_report
        if not callable(fn):
            return None
        return fn()

    def _spent(self) -> float:
        report = self.question_report()
        if report is None:
            return 0.0
        if isinstance(report, CostReport):
            return float(report.usd or 0.0)
        return 0.0


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def resumed_from_cache(usage: Mapping[str, object] | None) -> bool:
    if not usage:
        return False
    return _as_int(usage.get("requests")) == 0 and _as_int(usage.get("cache_hits")) > 0


def engine_cost_status(row: Mapping[str, Any]) -> str:
    """USD-статус engine-строки. Явный cost_status, иначе requests×cache_hits."""
    token = str(row.get("cost_status") or "").strip().lower()
    if token in {COST_EXACT, COST_CACHED, "partial"}:
        return token
    return cost_status_for(_as_int(row.get("requests")), _as_int(row.get("cache_hits")))


def annotate_v1_fields(
    scored: Sequence[Mapping[str, Any]], originals: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """v1 action_correct рядом с v2/heuristic. Источник не мутируем."""
    out: list[dict[str, Any]] = []
    for payload, src in zip(scored, originals, strict=True):
        row = dict(payload)
        row["action_correct_v1"] = src.get("action_correct")
        row["chosen_action_v1"] = src.get("chosen_action")
        row["appropriate_abstention_v1"] = src.get("appropriate_abstention")
        row["action_correct_v2"] = row.get("action_correct")
        usage = row.get("evaluator_usage") if isinstance(row.get("evaluator_usage"), dict) else {}
        row["resumed_from_cache"] = resumed_from_cache(usage)
        status = engine_cost_status(row)
        row["engine_cost_status"] = status
        row["engine_served_from_cache"] = status == COST_CACHED
        out.append(row)
    return out


def _arms_by_case(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Mapping[str, Any]]]:
    by_case: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_case[str(row["case_id"])][str(row["mode"])] = row
    return by_case


def _arm_ok_graded(row: Mapping[str, Any] | None) -> bool:
    if row is None:
        return False
    return row.get("coverage_status") == "ok" and row.get("grade_status") == "graded"


def dropped_cases(scored: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Кейсы вне primary: coverage или ungraded."""
    ungraded: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for case_id, arms in sorted(_arms_by_case(scored).items()):
        detail = {
            mode: {
                "coverage_status": (arms.get(mode) or {}).get("coverage_status"),
                "grade_status": (arms.get(mode) or {}).get("grade_status"),
            }
            for mode in ARMS
        }
        missing = [mode for mode in ARMS if mode not in arms]
        coverage_ok = all((arms.get(mode) or {}).get("coverage_status") == "ok" for mode in ARMS)
        if missing or not coverage_ok:
            incomplete.append({"case_id": case_id, "missing": missing, "arms": detail})
            continue
        if not all(_arm_ok_graded(arms.get(mode)) for mode in ARMS):
            ungraded.append({"case_id": case_id, "arms": detail})
    return {"dropped_ungraded": ungraded, "dropped_incomplete": incomplete}


def _credit(value: object) -> float:
    return 1.0 if value is True else 0.0


def paired_fully_graded(scored: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Δ только если все три arms coverage=ok и grade=graded."""
    paired: list[dict[str, Any]] = []
    for case_id, arms in sorted(_arms_by_case(scored).items()):
        if not all(_arm_ok_graded(arms.get(mode)) for mode in ARMS):
            continue
        ox = arms["zhoda"]
        sr = arms["short_review"]
        maj = arms["majority"]
        ox_c = _credit(ox.get("action_correct_v2"))
        sr_c = _credit(sr.get("action_correct_v2"))
        ox_status = engine_cost_status(ox)
        sr_status = engine_cost_status(sr)
        maj_status = engine_cost_status(maj)
        paired.append(
            {
                "case_id": case_id,
                "kind": str(ox.get("kind") or ""),
                "oxford_correct": ox_c,
                "short_review_correct": sr_c,
                "majority_correct": _credit(maj.get("action_correct_v2")),
                "delta": sr_c - ox_c,
                "oxford_usd": _as_float(ox.get("usd")) if ox_status == COST_EXACT else None,
                "short_review_usd": (_as_float(sr.get("usd")) if sr_status == COST_EXACT else None),
                "majority_usd": (_as_float(maj.get("usd")) if maj_status == COST_EXACT else None),
                "oxford_cost_status": ox_status,
                "short_review_cost_status": sr_status,
                "majority_cost_status": maj_status,
                "oxford_cached": ox_status == COST_CACHED,
                "short_review_cached": sr_status == COST_CACHED,
                "majority_cached": maj_status == COST_CACHED,
            }
        )
    n = len(paired)
    delta = sum(p["delta"] for p in paired) / n if n else None
    by_kind: dict[str, list[float]] = defaultdict(list)
    for row in paired:
        by_kind[row["kind"]].append(row["delta"])
    class_delta = {
        kind: {"n": len(vals), "delta": sum(vals) / len(vals)}
        for kind, vals in sorted(by_kind.items())
    }

    def _mean_exact(key: str) -> float | None:
        vals = [float(p[key]) for p in paired if p[key] is not None]
        return sum(vals) / len(vals) if vals else None

    return {
        "n_paired": n,
        "delta": delta,
        "class_delta": class_delta,
        "pairs": paired,
        "mean_usd_short_review": _mean_exact("short_review_usd"),
        "mean_usd_oxford": _mean_exact("oxford_usd"),
        "n_cached_oxford": sum(1 for p in paired if p["oxford_cached"]),
        "n_cached_short_review": sum(1 for p in paired if p["short_review_cached"]),
        "n_cached_majority": sum(1 for p in paired if p["majority_cached"]),
        "n_exact_cost_oxford": sum(1 for p in paired if p["oxford_cost_status"] == COST_EXACT),
        "n_exact_cost_short_review": sum(
            1 for p in paired if p["short_review_cost_status"] == COST_EXACT
        ),
    }


def class_arm_table(scored: Sequence[Mapping[str, Any]], *, field: str) -> dict[str, Any]:
    """Доли True по class×arm. Только fully graded triples."""
    eligible = {p["case_id"] for p in paired_fully_graded(scored)["pairs"]}
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in scored:
        if str(row.get("case_id")) not in eligible:
            continue
        kind = str(row.get("kind") or "")
        mode = str(row.get("mode") or "")
        buckets[(kind, mode)].append(_credit(row.get(field)))
    table: dict[str, dict[str, dict[str, float | int]]] = defaultdict(dict)
    for (kind, mode), vals in sorted(buckets.items()):
        table[kind][mode] = {"n": len(vals), "rate": sum(vals) / len(vals)}
    return dict(table)


def engine_zero_request_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {mode: 0 for mode in ARMS}
    for row in unique_arm_rows(list(rows)):
        mode = str(row.get("mode") or "")
        if mode in counts and _as_int(row.get("requests")) == 0:
            counts[mode] += 1
    return counts


def judge_roster(cfg: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    judges = [str(item) for item in (cfg.get("judges") or [])]
    model = judges[0] if judges else ""
    if not model:
        raise ValueError("no yaml judges[0] — refusing chairman as grader")
    chairman = str(cfg.get("chairman") or "")
    council = [str(item) for item in (cfg.get("council") or [])]
    if model == chairman:
        raise ValueError(f"judges[0]={model} is chairman — refusing")
    if model in council:
        raise ValueError(f"judges[0]={model} is on council — refusing")
    overlap: list[str] = []
    if model in judges:
        overlap.append("protocol_judge")
    classifiers = [str(item) for item in (cfg.get("router_classifiers") or [])]
    if model in classifiers:
        overlap.append("classifier")
    return model, tuple(overlap)


def refresh_engine_cost_tables(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Пересчитать primary/pairs по cost_status. Судью не зовём."""
    out = dict(payload)
    results: list[dict[str, Any]] = []
    for row in payload.get("results") or []:
        if not isinstance(row, Mapping):
            continue
        item = dict(row)
        status = engine_cost_status(item)
        item["engine_cost_status"] = status
        item["engine_served_from_cache"] = status == COST_CACHED
        results.append(item)
    primary = paired_fully_graded(results)
    out["results"] = results
    out["primary"] = {k: v for k, v in primary.items() if k != "pairs"}
    out["pairs"] = primary["pairs"]
    out["n_paired_fully_graded"] = primary["n_paired"]
    out["engine_zero_requests_by_arm"] = engine_zero_request_counts(results)
    return out


def evaluator_totals(scored: Sequence[Mapping[str, Any]]) -> dict[str, float | int]:
    requests = 0
    hits = 0
    usd = 0.0
    for row in scored:
        usage = row.get("evaluator_usage") or {}
        if not isinstance(usage, dict):
            continue
        requests += _as_int(usage.get("requests"))
        hits += _as_int(usage.get("cache_hits"))
        usd += _as_float(usage.get("usd"))
    return {"evaluator_requests": requests, "evaluator_cache_hits": hits, "evaluator_usd": usd}


async def rescore_report_v2(
    report: Mapping[str, Any],
    gold_map: Mapping[str, GoldRow],
    public_by_id: Mapping[str, PublicCase],
    *,
    provider: GradeProvider,
    model: str,
    judge_overlap: Sequence[str],
    source_sha256: str,
) -> dict[str, Any]:
    originals = [dict(row) for row in unique_arm_rows(list(report.get("results") or []))]
    scored_raw, disagreements = await overlay_scored_rows(
        originals,
        gold_map,
        public_by_id,
        provider=provider,
        model=model,
        judge_overlap=judge_overlap,
    )
    scored = annotate_v1_fields(scored_raw, originals)
    dropped = dropped_cases(scored)
    primary = paired_fully_graded(scored)
    totals = evaluator_totals(scored)
    n_cases = _as_int(report.get("n_cases"))
    return {
        "schema": SCHEMA_RESCORE_V2,
        "rescored_from": str(report.get("schema") or SCHEMA_V1),
        "source_report": "report.json",
        "source_sha256": source_sha256,
        "live": False,
        "independent_validation": False,
        "grader_version": GRADER_VERSION,
        "judge_model": model,
        "judge_overlap": list(judge_overlap),
        "evaluator_usd": totals["evaluator_usd"],
        "evaluator_requests": totals["evaluator_requests"],
        "evaluator_cache_hits": totals["evaluator_cache_hits"],
        "evaluator_cap_usd": EVALUATOR_CAP_USD,
        "n_cases": n_cases,
        "n_rows": len(scored),
        "n_paired_fully_graded": primary["n_paired"],
        "primary": {k: v for k, v in primary.items() if k != "pairs"},
        "pairs": primary["pairs"],
        "class_arm_v1": class_arm_table(scored, field="action_correct_v1"),
        "class_arm_v2": class_arm_table(scored, field="action_correct_v2"),
        "dropped_ungraded": dropped["dropped_ungraded"],
        "dropped_incomplete": dropped["dropped_incomplete"],
        "heuristic_llm_disagreements": disagreements,
        "engine_zero_requests_by_arm": engine_zero_request_counts(
            list(report.get("results") or [])
        ),
        "decision_rule": {
            "verdict": "pending_rerun",
            "product_default": "debate",
            "reason": ("preregistered rule is not applied on rescore; only a P5 rerun may decide"),
        },
        "product_default": "debate",
        "results": scored,
    }
