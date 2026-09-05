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


_LOADED_PREMISE_RE = re.compile(
    r"\bwhy is\b|\bwhy are\b|\bsince\b|\bgiven that\b|"
    r"\beveryone\b.{0,40}\bagrees\b",
    re.IGNORECASE,
)
_XOR_SPLIT_RE = re.compile(
    r"\s+(?:or|vs\.?|versus|или)\s+",
    re.IGNORECASE,
)
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
LOADED_PREMISE_REJECT = (
    "The premise is false: assertions in the question "
    "(always/never/since/given that/why is) are not confirmed constraints "
    "and are not adopted as the recommendation."
)


def looks_like_loaded_premise(question: str) -> bool:
    """Why-always / since / given that / everyone agrees — не XOR-опция «always»."""
    text = question or ""
    if _LOADED_PREMISE_RE.search(text):
        return True
    if looks_like_xor_question(text):
        return False
    return bool(re.search(r"\balways\b|\bnever\b", text, re.IGNORECASE))


def challenges_loaded_premise(text: str) -> bool:
    """Текст явно отвергает loaded premise, а не объясняет его как факт."""
    return bool(_CHALLENGE_RE.search(text or ""))


def xor_option_pair(question: str) -> tuple[str, str] | None:
    """Левая и правая метки XOR (до «for/to/?»)."""
    if not looks_like_xor_question(question):
        return None
    head = re.split(r"[?]", question or "", maxsplit=1)[0]
    head = re.split(r"\s+for\s+|\s+to\s+", head, maxsplit=1, flags=re.IGNORECASE)[0]
    parts = _XOR_SPLIT_RE.split(head, maxsplit=1)
    if len(parts) != 2:
        return None
    left = re.sub(
        r"^(?:should we|is it|do we)\s+", "", parts[0].strip(), flags=re.IGNORECASE
    ).strip()
    right = parts[1].strip()
    if not left or not right:
        return None
    return left, right


def _option_stem(option: str) -> str:
    token = re.split(r"\s+", option.casefold().strip())[0]
    token = re.sub(r"[^a-z0-9]", "", token)
    if len(token) >= 3:
        return token
    return re.sub(r"[^a-z0-9]", "", option.casefold())


def named_xor_pick(text: str, left: str, right: str) -> str | None:
    """Какой из двух XOR-вариантов thesis держит как primary (первое вхождение)."""
    body = (text or "").casefold()
    left_stem, right_stem = _option_stem(left), _option_stem(right)
    left_at = body.find(left_stem) if left_stem else -1
    right_at = body.find(right_stem) if right_stem else -1
    if left_at < 0 and right_at < 0:
        return None
    if left_at < 0:
        return right
    if right_at < 0:
        return left
    return left if left_at <= right_at else right


def xor_primary_flipped(question: str, old_thesis: str, new_thesis: str) -> bool:
    """Ревизия сменила named XOR-pick — это switch, не правка платформы."""
    pair = xor_option_pair(question)
    if pair is None:
        return False
    old_pick = named_xor_pick(old_thesis, pair[0], pair[1])
    new_pick = named_xor_pick(new_thesis, pair[0], pair[1])
    if old_pick is None or new_pick is None:
        return False
    return old_pick.casefold() != new_pick.casefold()


def blocks_loaded_premise_switch(question: str, from_thesis: str, to_thesis: str) -> bool:
    """Нельзя переходить с опровержения premise на его принятие."""
    if not looks_like_loaded_premise(question):
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
    """Primary rec не может принять loaded premise. Не копирует minority thesis."""
    if not looks_like_loaded_premise(question):
        return text
    if challenges_loaded_premise(text):
        return text
    return LOADED_PREMISE_REJECT


def should_apply_revision(
    old_thesis: str,
    new_thesis: str,
    *,
    changed: bool,
    question: str = "",
) -> bool:
    """Hedge не заменяет pick; XOR-flip и принятие loaded premise — отказ."""
    if not changed or not (new_thesis or "").strip():
        return False
    if is_hedge_text(new_thesis) and not is_hedge_text(old_thesis):
        return False
    if xor_primary_flipped(question, old_thesis, new_thesis):
        return False
    if looks_like_loaded_premise(question):
        if challenges_loaded_premise(old_thesis) and not challenges_loaded_premise(
            new_thesis
        ):
            return False
        if not challenges_loaded_premise(new_thesis) and not challenges_loaded_premise(
            old_thesis
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
