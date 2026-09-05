"""Стражи протокола: не давать «it depends» за згоду и за ревизию платформы."""

from __future__ import annotations

import re

_HEDGE_RE = re.compile(
    r"it depends|"
    r"contingent on|"
    r"based on team expertise|"
    r"team's operational proficiency|"
    r"comparable operational complexity|"
    r"both (?:are|options) (?:valid|comparable|acceptable)|"
    r"neither is (?:clearly )?(?:better|superior)|"
    r"the choice is therefore|"
    r"select the data backbone based on|"
    r"rather than on assumptions of inherent",
    re.IGNORECASE,
)


def is_hedge_text(text: str) -> bool:
    """Капитуляция вместо primary rec — не решение и не ревизия платформы."""
    body = (text or "").strip()
    if not body:
        return False
    if _HEDGE_RE.search(body):
        return True
    # Live kafka: «While PostgreSQL…, Kafka can… However…» — оба pick, ни одного.
    if re.match(r"While\b", body) and len(body) > 120:
        return True
    return False


_XOR_QUESTION_RE = re.compile(
    r"\s+or\s+|\s+vs\.?\s+|\s+versus\s+|\s+или\s+",
    re.IGNORECASE,
)
_HYBRID_RE = re.compile(
    r"\bhybrid\b|"
    r"\bcombin(?:e|ing|ed|ation)\b|"
    r"\balongside\b|"
    r"\bas well as\b|"
    r"\bboth\s+.{0,80}\s+and\s+",
    re.IGNORECASE,
)


def looks_like_xor_question(question: str) -> bool:
    """A или B / A vs B — пользователь просит один pick, не смесь."""
    return bool(_XOR_QUESTION_RE.search(question or ""))


def is_hybrid_decision(text: str) -> bool:
    """Смесь обоих вариантов как действие, не один primary rec."""
    return bool(_HYBRID_RE.search(text or ""))


def should_apply_revision(old_thesis: str, new_thesis: str, *, changed: bool) -> bool:
    """Hedge не заменяет конкретный pick; оговорки в thesis без смены действия — ок."""
    if not changed or not (new_thesis or "").strip():
        return False
    if is_hedge_text(new_thesis) and not is_hedge_text(old_thesis):
        return False
    return True


_CLAIM_STOP = frozenset(
    {
        "about",
        "after",
        "because",
        "being",
        "could",
        "critical",
        "direct",
        "enables",
        "function",
        "helper",
        "implementation",
        "inherent",
        "inherently",
        "insecure",
        "login",
        "production",
        "provided",
        "require",
        "requires",
        "security",
        "should",
        "their",
        "there",
        "these",
        "this",
        "unsafe",
        "unsuitable",
        "which",
        "would",
        "system",
        "systems",
        "vulnerabilities",
        "vulnerability",
        "without",
        "additional",
    }
)


def _claim_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{5,}", (text or "").casefold())) - _CLAIM_STOP


def claims_reflected_in_decision(claims: list[str], decision: str) -> bool:
    """Хотя бы один claim узнаваем в тексте — иначе председатель стёр находку."""
    named = [c.strip() for c in claims if c.strip()]
    if not named:
        return True
    body = _claim_tokens(decision)
    for claim in named:
        if _claim_tokens(claim) & body:
            return True
    return False


def ensure_claims_in_decision(decision: str, claims: list[str]) -> str:
    """Live login: generic 'inherently insecure' без SQL injection — дописать Findings."""
    named = [c.strip() for c in claims if c.strip()]
    if not named or claims_reflected_in_decision(named, decision):
        return decision
    lines = [decision.rstrip(), "", "Findings:"]
    lines.extend(f"- {c}" for c in named)
    return "\n".join(lines)
