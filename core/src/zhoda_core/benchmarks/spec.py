"""EffectiveRunSpec: одно resolved состояние, hashes содержимого, не ярлыков.

Неприменимый override — явная ошибка, не тихое игнорирование.
Человекочитаемые версии не заменяют content hashes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from zhoda_core.providers.openrouter import DEFAULT_MAX_TOKENS

from .datasets import BenchmarkCase, dump_cases

SPEC_SCHEMA = "zhoda.eval.spec.v1"
DATASET_SPLIT_DEVELOPMENT = "development"
RUBRIC_VERSION = "zhoda.eval.quality.v1"
PROMPT_SET_VERSION = "zhoda.eval.prompts.v1"

CACHE_FRESH = "fresh"
CACHE_REPLAY = "replay"
CACHE_MODES = (CACHE_FRESH, CACHE_REPLAY)


class InapplicableOverride(ValueError):
    """Override нельзя применить к этому run — не глотать."""


class SpecMismatch(ValueError):
    """Declared spec не совпал с фактическими вызовами."""


def canonical_json(payload: object) -> str:
    """Стабильная сериализация: sort_keys, без лишних пробелов."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def source_sha(repo: Path | None = None) -> str:
    """SHA текущего дерева. Нет git — пустая строка, не выдуманный hash."""
    cwd = str(repo) if repo is not None else None
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return out.strip()


def hash_cases(cases: Sequence[BenchmarkCase]) -> str:
    """Hash тех кейсов, которые реально вошли в run."""
    rows = []
    for case in cases:
        item = asdict(case)
        item["seed_agents"] = [asdict(a) for a in case.seed_agents]
        rows.append(item)
    return content_hash(rows)


def hash_prompts() -> str:
    """Hash шаблонов, которые реально импортирует eval, не ярлыка версии."""
    from zhoda_core.debate import EVIDENCE_CRITIQUE_PROMPT

    from . import baselines, judge

    return content_hash(
        {
            "answer": baselines.ANSWER_PROMPT,
            "sc_json": baselines.SC_JSON_PROMPT,
            "cluster": baselines.CLUSTER_PROMPT,
            "synthesize": baselines.SYNTHESIZE_PROMPT,
            "pick_best": baselines.PICK_BEST_PROMPT,
            "blind_judge": judge.BLIND_JUDGE_PROMPT,
            "evidence_critique": EVIDENCE_CRITIQUE_PROMPT,
        }
    )


def hash_rubric() -> str:
    """Hash осей grader и adversarial corpus, не ярлыка версии."""
    from dataclasses import asdict, fields

    from . import quality

    return content_hash(
        {
            "axes": [f.name for f in fields(quality.QualityScores)],
            "corpus": [asdict(item) for item in quality.calibration_corpus()],
            "no_re": quality._NO_RE.pattern,
            "hedge_re": quality._HEDGE_RE.pattern,
            "abstain_re": quality._ABSTAIN_RE.pattern,
        }
    )


def _config_for_hash(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Без секретов: ключи API в spec не пишем."""
    blocked = {"api_key", "OPENROUTER_API_KEY", "password"}
    return {k: v for k, v in cfg.items() if k not in blocked}


@dataclass(frozen=True)
class RoleRoster:
    council: tuple[str, ...]
    judges: tuple[str, ...]
    chairman: str
    classifiers: tuple[str, ...]
    judge_model: str = ""
    judge_overlap: tuple[str, ...] = ()

    def allowed_models(self) -> tuple[str, ...]:
        seen: list[str] = []
        for name in (
            *self.council,
            *self.judges,
            self.chairman,
            *self.classifiers,
            self.judge_model,
        ):
            if name and name not in seen:
                seen.append(name)
        return tuple(seen)


@dataclass(frozen=True)
class EffectiveRunSpec:
    """Один spec после defaults/overrides. Identity run = spec_hash."""

    schema: str = SPEC_SCHEMA
    source_sha: str = ""
    roster: RoleRoster = field(
        default_factory=lambda: RoleRoster((), (), "", ())
    )
    protocol_zhoda: str = "debate"
    rounds: int = 4
    max_tokens: int = DEFAULT_MAX_TOKENS
    budget_per_question_usd: float = 0.0
    cache_mode: str = CACHE_FRESH
    cache_namespace: str = ""
    isolate_cache: bool = True
    dataset_hash: str = ""
    config_hash: str = ""
    prompt_hash: str = ""
    rubric_hash: str = ""
    dataset_split: str = DATASET_SPLIT_DEVELOPMENT
    replicate_id: int = 0
    seed_capabilities: tuple[str, ...] = ()
    resource_policy: str = "request_and_cost"
    clarify_mode: str = "no-clarify"
    dry_run: bool = False
    prompt_set_version: str = PROMPT_SET_VERSION
    rubric_version: str = RUBRIC_VERSION

    @property
    def spec_hash(self) -> str:
        payload = asdict(self)
        payload["roster"] = asdict(self.roster)
        return content_hash(payload)

    def to_manifest(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["roster"] = asdict(self.roster)
        payload["spec_hash"] = self.spec_hash
        payload["models"] = list(self.roster.council)
        return payload


def _declare_judge_overlap(roster: RoleRoster, judge_model: str) -> tuple[str, ...]:
    roles: list[str] = []
    if judge_model and judge_model == roster.chairman:
        roles.append("chairman")
    if judge_model in roster.council:
        roles.append("council")
    if judge_model in roster.judges:
        roles.append("protocol_judge")
    if judge_model in roster.classifiers:
        roles.append("classifier")
    return tuple(roles)


def resolve_run_spec(
    *,
    cases: Sequence[BenchmarkCase],
    yaml_cfg: Mapping[str, Any] | None,
    models_override: Sequence[str] | None,
    rounds_override: int | None,
    budget_override: float | None,
    cache_path: str | None,
    isolate_cache: bool,
    cache_mode: str,
    replicate_id: int,
    clarify_mode: str,
    dry_run: bool,
    judge_model_override: str | None = None,
    source_dir: Path | None = None,
) -> EffectiveRunSpec:
    """Собрать spec. Override, который нельзя применить, — InapplicableOverride."""
    if cache_mode not in CACHE_MODES:
        raise InapplicableOverride(f"unknown cache_mode {cache_mode!r}")
    if replicate_id < 0:
        raise InapplicableOverride("replicate_id must be >= 0")
    if not isolate_cache and replicate_id > 0 and cache_mode == CACHE_FRESH:
        raise InapplicableOverride(
            "independent replicate requires isolated cache namespaces"
        )
    if rounds_override is not None and rounds_override < 1:
        raise InapplicableOverride("rounds override must be >= 1")

    cfg = dict(yaml_cfg or {})
    yaml_council = tuple(str(m) for m in cfg.get("council") or ())
    if models_override is not None:
        council = tuple(str(m).strip() for m in models_override if str(m).strip())
        if not council:
            raise InapplicableOverride("--models override is empty")
    elif yaml_council:
        council = yaml_council
    else:
        if not dry_run:
            raise InapplicableOverride("no council in YAML and no --models")
        council = (
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-chat-v3-0324:free",
            "qwen/qwen3-235b-a22b:free",
        )

    judges = tuple(str(j) for j in cfg.get("judges") or ())
    classifiers = tuple(str(c) for c in cfg.get("router_classifiers") or ())
    chairman = str(cfg.get("chairman") or (council[0] if council else ""))
    if judge_model_override:
        judge_model = str(judge_model_override).strip()
        if not judge_model:
            raise InapplicableOverride("--judge-model override is empty")
    elif judges:
        # Не брать chairman молча как «независимого» судью.
        judge_model = judges[0]
    else:
        judge_model = ""

    rounds = int(cfg.get("rounds_cap", 4)) if rounds_override is None else int(rounds_override)
    budget = (
        float(cfg.get("budget_per_question_usd", 0.0))
        if budget_override is None
        else float(budget_override)
    )
    if budget_override is not None and budget < 0:
        raise InapplicableOverride("budget override must be >= 0")

    base_cache = cache_path or str(cfg.get("cache_path") or ".zhoda-cache.db")
    roster = RoleRoster(
        council=council,
        judges=judges,
        chairman=chairman,
        classifiers=classifiers,
        judge_model=judge_model,
        judge_overlap=(),
    )
    overlap = _declare_judge_overlap(roster, judge_model)
    roster = RoleRoster(
        council=council,
        judges=judges,
        chairman=chairman,
        classifiers=classifiers,
        judge_model=judge_model,
        judge_overlap=overlap,
    )
    prompt_h = hash_prompts()
    rubric_h = hash_rubric()
    return EffectiveRunSpec(
        source_sha=source_sha(source_dir),
        roster=roster,
        rounds=rounds,
        max_tokens=DEFAULT_MAX_TOKENS,
        budget_per_question_usd=budget,
        cache_mode=cache_mode,
        cache_namespace=base_cache,
        isolate_cache=isolate_cache,
        dataset_hash=hash_cases(cases),
        config_hash=content_hash(_config_for_hash({**cfg, "council": list(council)})),
        prompt_hash=prompt_h,
        rubric_hash=rubric_h,
        replicate_id=replicate_id,
        seed_capabilities=("content_alias_seed", "forced_minority_positions"),
        clarify_mode=clarify_mode,
        dry_run=dry_run,
    )


def dump_dataset_fingerprint(cases: Sequence[BenchmarkCase], path: Path) -> str:
    dump_cases(cases, path)
    return hash_cases(cases)
