"""Public pilot cases + freeze/dry-run. Не импортирует gold."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from zhoda_core.benchmarks.datasets import BenchmarkCase, SeedAgent
from zhoda_core.evidence import content_hash
from zhoda_core.eval.cases import PUBLIC_FORBIDDEN, SPECS, TASK_CLASSES, PilotSpec

PILOT_STATUS = "READY_FOR_APPROVAL"
SUITE_PILOT = "pilot"
PLAN_ID = "zhoda.eval.preregister.v1"
N_MIN, N_MAX = 30, 50
_PII_RE = re.compile(
    r"(?i)(@[a-z0-9.-]+\.[a-z]{2,}|sk-[a-zA-Z0-9_-]{8,}|password\s*=\s*\S+|"
    r"\b\d{3}-\d{2}-\d{4}\b)"
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PUBLIC_JSONL = REPO_ROOT / "core" / "eval" / "pilot" / "public.jsonl"
GOLD_JSONL = REPO_ROOT / "core" / "eval" / "pilot" / "gold.jsonl"
PLAN_MD = REPO_ROOT / "docs" / "eval" / "2026-09-06-preregistration.md"
FROZEN_MANIFEST = REPO_ROOT / "docs" / "eval" / "frozen-manifest.json"


@dataclass(frozen=True)
class PublicCase:
    id: str
    task_class: str
    question: str
    source_bundle: dict[str, str]
    severity: str
    answer_options: tuple[str, ...] = ()
    seed_agents: tuple[SeedAgent, ...] = ()


def _seeds(spec: PilotSpec) -> tuple[SeedAgent, ...]:
    if not spec.majority_position or not spec.truth_seed:
        return ()
    return (
        SeedAgent("minority", spec.truth_seed),
        SeedAgent("majority-1", spec.majority_position),
        SeedAgent("majority-2", spec.majority_position),
    )


def public_from_spec(spec: PilotSpec) -> PublicCase:
    text = spec.source_text
    return PublicCase(
        id=spec.id,
        task_class=spec.task_class,
        question=spec.question,
        source_bundle={
            "id": spec.source_id,
            "text": text,
            "content_hash": content_hash(text),
        },
        severity=spec.severity,
        answer_options=spec.answer_options,
        seed_agents=_seeds(spec),
    )


def public_payload(case: PublicCase) -> dict[str, object]:
    return {
        "id": case.id,
        "task_class": case.task_class,
        "question": case.question,
        "source_bundle": case.source_bundle,
        "severity": case.severity,
        "answer_options": list(case.answer_options),
        "seed_agents": [
            {"name": s.name, "position": s.position, "arguments": list(s.arguments)}
            for s in case.seed_agents
        ],
    }


def dump_public(path: Path = PUBLIC_JSONL) -> str:
    lines = [json.dumps(public_payload(public_from_spec(s)), ensure_ascii=False) for s in SPECS]
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode()).hexdigest()


def load_public_cases(path: Path = PUBLIC_JSONL) -> list[PublicCase]:
    rows: list[PublicCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        leaked = PUBLIC_FORBIDDEN.intersection(raw)
        if leaked:
            raise ValueError(f"public JSONL leaked gold keys {sorted(leaked)} on {raw.get('id')}")
        seeds = tuple(
            SeedAgent(str(s["name"]), str(s["position"]), tuple(s.get("arguments") or ()))
            for s in raw.get("seed_agents") or ()
        )
        rows.append(
            PublicCase(
                id=str(raw["id"]),
                task_class=str(raw["task_class"]),
                question=str(raw["question"]),
                source_bundle=dict(raw["source_bundle"]),
                severity=str(raw["severity"]),
                answer_options=tuple(raw.get("answer_options") or ()),
                seed_agents=seeds,
            )
        )
    return rows


def public_to_benchmark(case: PublicCase) -> BenchmarkCase:
    """Runner-visible case. ground_truth пустой — gold не подмешиваем."""
    return BenchmarkCase(
        id=case.id,
        suite=SUITE_PILOT,
        kind=case.task_class,
        question=case.question,
        ground_truth="",
        seed_agents=case.seed_agents,
        answer_options=case.answer_options,
        majority_position=case.seed_agents[1].position if len(case.seed_agents) > 1 else None,
        context=str(case.source_bundle.get("text") or ""),
    )


def validate_public(cases: Sequence[PublicCase] | None = None) -> list[str]:
    rows = list(cases) if cases is not None else load_public_cases()
    errors: list[str] = []
    if not (N_MIN <= len(rows) <= N_MAX):
        errors.append(f"n={len(rows)} not in {N_MIN}–{N_MAX}")
    ids = [c.id for c in rows]
    if len(ids) != len(set(ids)):
        errors.append("duplicate ids")
    classes = {c.task_class for c in rows}
    missing = [t for t in TASK_CLASSES if t not in classes]
    if missing:
        errors.append(f"missing task classes {missing}")
    for case in rows:
        text = case.source_bundle.get("text") or ""
        digest = case.source_bundle.get("content_hash") or ""
        if text and digest != content_hash(text):
            errors.append(f"{case.id} source hash mismatch")
        blob = " ".join([case.question, text, case.id])
        if _PII_RE.search(blob):
            errors.append(f"{case.id} looks like PII/secret")
        if case.task_class == "correct_minority" and not case.seed_agents:
            errors.append(f"{case.id} minority without seeds")
        if case.task_class == "legitimate_uncertainty" and not case.source_bundle.get("text"):
            errors.append(f"{case.id} uncertainty missing source note")
    return errors


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_frozen_manifest() -> dict[str, object]:
    from zhoda_core.benchmarks.runner import PILOT_ARMS
    from zhoda_core.benchmarks.spec import hash_prompts, hash_rubric, source_sha

    public_h = file_hash(PUBLIC_JSONL) if PUBLIC_JSONL.exists() else dump_public()
    gold_h = file_hash(GOLD_JSONL) if GOLD_JSONL.exists() else ""
    plan_h = file_hash(PLAN_MD) if PLAN_MD.exists() else ""
    return {
        "schema": PLAN_ID,
        "status": PILOT_STATUS,
        "live": False,
        "independent_validation": False,
        "protocol_prereq": "bfd7e90",
        "f_acceptance": "6486e8e",
        "source_sha": source_sha(REPO_ROOT),
        "public_hash": public_h,
        "gold_hash": gold_h,
        "plan_hash": plan_h,
        "prompt_hash": hash_prompts(),
        "rubric_hash": hash_rubric(),
        "dataset_split": "pilot_holdout",
        "development_split": "decision-51",
        "n_cases": len(SPECS),
        "arms": list(PILOT_ARMS),
        "primary_hypothesis": "quality",
        "primary_metric": "paired_delta_action_correct_short_minus_oxford",
        "noninferiority_margin": 0.10,
        "spend_cap_usd": 8.0,
        "replicates": 1,
        "cache_mode": "fresh",
        "failed_ungraded_policy": "keep_in_denominator",
        "significance_promised": False,
    }


def write_frozen_manifest(path: Path = FROZEN_MANIFEST) -> dict[str, object]:
    payload = build_frozen_manifest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
