"""Domain taxonomy and question classifier for Stage 0 elicitation.

The default classifier is heuristic (keyword scoring + softmax) so it
runs offline and for free. An LLM-backed classifier can be injected via
``classify_domains(question, llm_classifier=...)``; it must return a
mapping of domain name -> probability and is renormalized here.
"""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Callable, Dict, Mapping, Optional


class Domain(str, Enum):
    CODE_ARCHITECTURE = "code_architecture"
    SECURITY_AUDIT = "security_audit"
    FACTUAL_QA = "factual_qa"
    LOGIC_MATH = "logic_math"
    ETHICS_PHILOSOPHY = "ethics_philosophy"
    GENERAL = "general"


DomainVector = Dict[Domain, float]

_KEYWORDS: Dict[Domain, tuple] = {
    Domain.CODE_ARCHITECTURE: (
        "architecture", "microservice", "monolith", "api", "rest", "grpc",
        "graphql", "database", "postgres", "redis", "queue", "refactor",
        "dependency", "design pattern", "code review", "framework",
        "backend", "frontend", "deploy", "kubernetes", "docker", "ci",
        "repository", "repo", "typing", "compiler",
    ),
    Domain.SECURITY_AUDIT: (
        "security", "vulnerability", "auth", "oauth", "token", "jwt",
        "encryption", "xss", "csrf", "injection", "password", "bcrypt",
        "md5", "hash", "cve", "exploit", "secret", "api key", "owasp",
    ),
    Domain.FACTUAL_QA: (
        "who", "when", "where", "what is", "history", "capital", "date",
        "fact", "definition", "meaning", "standard", "specification",
        "ieee", "rfc", "sql",
    ),
    Domain.LOGIC_MATH: (
        "prove", "proof", "theorem", "calculate", "equation",
        "probability", "logic", "paradox", "math", "complexity",
        "big-o", "floating point", "ieee 754", "nan", "infinity",
    ),
    Domain.ETHICS_PHILOSOPHY: (
        "ethic", "moral", "should we", "philosophy", "fairness",
        "rights", "dilemma", "value", "harm", "consent",
    ),
}

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\- ]*")


def _raw_scores(question: str) -> Dict[Domain, float]:
    text = question.lower()
    scores: Dict[Domain, float] = {}
    for domain, keywords in _KEYWORDS.items():
        score = 0.0
        for kw in keywords:
            if " " in kw:
                if kw in text:
                    score += 1.0
            elif re.search(rf"\b{re.escape(kw)}\b", text):
                score += 1.0
        scores[domain] = score
    return scores


def _softmax(scores: Mapping[Domain, float], temperature: float = 1.0) -> DomainVector:
    peak = max(scores.values())
    exps = {d: math.exp((s - peak) / temperature) for d, s in scores.items()}
    total = sum(exps.values())
    return {d: v / total for d, v in exps.items()}


def classify_domains(
    question: str,
    llm_classifier: Optional[Callable[[str], Mapping[str, float]]] = None,
) -> DomainVector:
    """Return a probability vector over domains for a question.

    Guarantees: values are non-negative and sum to 1. If no domain scores
    and no LLM classifier is provided, the mass falls back to GENERAL.
    """
    if llm_classifier is not None:
        raw = llm_classifier(question)
        vector: DomainVector = {}
        for name, prob in raw.items():
            try:
                vector[Domain(name)] = max(0.0, float(prob))
            except ValueError:
                continue
        total = sum(vector.values())
        if total > 0:
            return {d: p / total for d, p in vector.items()}

    scores = _raw_scores(question)
    if max(scores.values()) == 0.0:
        return {Domain.GENERAL: 1.0}
    return _softmax(scores)
