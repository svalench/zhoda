"""Стражи протокола: не давать «it depends» за згоду и за ревизию платформы."""

from __future__ import annotations

import re

from .actions import (
    actions_equivalent,
    bind_action,
    inspect_premise,
    looks_like_xor_question,
    option_catalog,
)
from .models import PremiseRole, VerificationStatus

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


_HYBRID_RE = re.compile(
    r"\bhybrid\b|"
    r"\bcombin(?:e|ing|ed|ation)\b|"
    r"\balongside\b|"
    r"\bas well as\b|"
    r"\bboth\s+.{0,80}\s+and\s+",
    re.IGNORECASE,
)


def is_hybrid_decision(text: str) -> bool:
    """Смесь обоих вариантов как действие, не один primary rec."""
    return bool(_HYBRID_RE.search(text or ""))


_CHALLENGE_RE = re.compile(
    r"premise is false|"
    r"false premise|"
    r"rejected premise|"
    r"rejects the premise|"
    r"reject(?:s|ed)? the premise|"
    r"not always|"
    r"is not (?:always|true|faster|better|obsolete)|"
    r"are not (?:always|true)|"
    r"not true|"
    r"incorrect|"
    r"misconception|"
    r"misleading|"
    r"on the contrary|"
    r"inadvisable|"
    r"not advisable|"
    r"\bnot recommended\b|"
    r"not suitable",
    re.IGNORECASE,
)

LOADED_PREMISE_NOTE = (
    "The question embeds an unproven premise (always/never/since/given that/why is). "
    "It is not a confirmed constraint; challenge it if it is false."
)
LOADED_PREMISE_UNVERIFIED = (
    "The question's premise is unverified: assertions in the question "
    "(always/never/since/given that/why is) are not confirmed constraints "
    "and are not adopted as the recommendation."
)
LOADED_PREMISE_REJECT = (
    "The premise is false: assertions in the question "
    "(always/never/since/given that/why is) are not confirmed constraints "
    "and are not adopted as the recommendation."
)


def looks_like_loaded_premise(question: str) -> bool:
    """Why-always / since / given that — сигнал проверить, не XOR-опция «always»."""
    return inspect_premise(question).role is not PremiseRole.NONE


def challenges_loaded_premise(text: str) -> bool:
    """Текст явно отвергает loaded premise, а не объясняет его как факт."""
    return bool(_CHALLENGE_RE.search(text or ""))


def xor_primary_flipped(question: str, old_thesis: str, new_thesis: str) -> bool:
    """Ревизия сменила named XOR-pick — без evidence это switch, не правка."""
    if not looks_like_xor_question(question):
        return False
    catalog = option_catalog(question)
    if not catalog.is_choice_list():
        return False
    old_action = bind_action(old_thesis, catalog)
    new_action = bind_action(new_thesis, catalog)
    equivalent = actions_equivalent(old_action, new_action)
    if equivalent is True:
        return False
    if equivalent is False:
        return True
    return False


def blocks_loaded_premise_switch(question: str, from_thesis: str, to_thesis: str) -> bool:
    """Нельзя переходить с опровержения asked-proposition на её принятие."""
    if inspect_premise(question).role is not PremiseRole.ASKED_PROPOSITION:
        return False
    return challenges_loaded_premise(from_thesis) and not challenges_loaded_premise(
        to_thesis
    )


def loaded_premise_ambiguities(question: str, existing: list[str]) -> list[str]:
    """Loaded premise → open_ambiguities, никогда в constraints (даже no-clarify)."""
    if not looks_like_loaded_premise(question):
        return existing
    if any(LOADED_PREMISE_NOTE in item for item in existing):
        return existing
    return [*existing, LOADED_PREMISE_NOTE]


def ensure_loaded_premise_not_adopted(question: str, text: str) -> str:
    """Unknown premise не становится false и не сочиняет rebuttal.

    Background (given that … should we Y) сохраняет действие Y.
    Asked-proposition без evidence → unverified, не «premise is false».
    """
    probe = inspect_premise(question)
    if probe.role is PremiseRole.NONE:
        return text
    if challenges_loaded_premise(text):
        return text
    if probe.role is PremiseRole.BACKGROUND:
        return text
    if probe.verification is VerificationStatus.REFUTED:
        return LOADED_PREMISE_REJECT
    if probe.verification is VerificationStatus.SUPPORTED:
        return text
    return LOADED_PREMISE_UNVERIFIED


def should_apply_revision(
    old_thesis: str,
    new_thesis: str,
    *,
    changed: bool,
    question: str = "",
    correction: bool = False,
    change_note: str = "",
) -> bool:
    """Hedge не заменяет pick. XOR-flip без cause — отказ; с evidence — correction."""
    if not changed or not (new_thesis or "").strip():
        return False
    if is_hedge_text(new_thesis) and not is_hedge_text(old_thesis):
        return False
    catalog = option_catalog(question) if question else None
    old_action = bind_action(old_thesis, catalog)
    new_action = bind_action(new_thesis, catalog)
    equivalent = actions_equivalent(old_action, new_action)
    if equivalent is True:
        return True
    if equivalent is None and catalog is not None and catalog.is_choice_list():
        return False
    if xor_primary_flipped(question, old_thesis, new_thesis):
        return bool(correction and change_note.strip())
    probe = inspect_premise(question)
    if probe.role is PremiseRole.ASKED_PROPOSITION:
        if challenges_loaded_premise(old_thesis) and not challenges_loaded_premise(
            new_thesis
        ):
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
