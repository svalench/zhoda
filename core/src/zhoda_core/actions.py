"""Контракт действия и посылка: ID из списка вариантов, не из порядка слов.

Триггеры given that / since / always — сигнал проверить, не истина.
Неизвестная эквивалентность не сливает фракции и не держит stability streak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    ActionContract,
    ActionRelation,
    Condition,
    PremiseProbe,
    PremiseRole,
    StatementKind,
    VerificationStatus,
)

UNRESOLVED_ID = "unresolved"

_XOR_SPLIT_RE = re.compile(
    r"\s+(?:or|vs\.?|versus|или)\s+",
    re.IGNORECASE,
)
_XOR_QUESTION_RE = re.compile(
    r"\s+or\s+|\s+vs\.?\s+|\s+versus\s+|\s+или\s+",
    re.IGNORECASE,
)
_WHY_IS_RE = re.compile(r"\bwhy is\b|\bwhy are\b|\bпочему\b", re.IGNORECASE)
_BACKGROUND_RE = re.compile(
    r"\bgiven that\b|\bsince\b|\beveryone\b.{0,40}\bagrees\b|"
    r"\bучитывая\s+что\b|\bпоскольку\b",
    re.IGNORECASE,
)
_ALWAYS_NEVER_RE = re.compile(r"\balways\b|\bnever\b|\bвсегда\b|\bникогда\b", re.IGNORECASE)
_MATERIAL_COND_RE = re.compile(
    r"(?:only if|unless|provided that|iff|только если|при условии)\s+(.{3,80})",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(
    r"^(?:option|вариант|#)?\s*(\d+)\s*[.)]?$",
    re.IGNORECASE,
)
_NO_RE = re.compile(r"^(?:no|нет|nope)\.?$", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-zа-яё0-9']+", re.IGNORECASE)
_CLAUSE_RE = re.compile(
    r"[.;]+|\s*,\s*(?=not\b|instead\b|rather\b|не\b|вместо\b)",
    re.IGNORECASE,
)

_NEG_TOKENS = frozenset(
    {
        "no",
        "not",
        "never",
        "without",
        "avoid",
        "reject",
        "rejected",
        "rejecting",
        "unlike",
        "unsuitable",
        "instead",
        "rather",
        "dont",
        "don't",
        "не",
        "нет",
        "вместо",
        "отверг",
        "отвергает",
        "непригоден",
        "непригодна",
        "нельзя",
        "против",
    }
)
_AFFIRM_TOKENS = frozenset(
    {
        "use",
        "using",
        "choose",
        "choosing",
        "pick",
        "recommend",
        "recommended",
        "remain",
        "remains",
        "keep",
        "prefer",
        "preferred",
        "should",
        "используй",
        "использовать",
        "выбрать",
        "рекомендуем",
        "рекомендуется",
    }
)


@dataclass(frozen=True)
class CatalogOption:
    action_id: str
    label: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class OptionCatalog:
    """Варианты одного прогона. ID = индекс списка, не порядок слов в ответе."""

    options: tuple[CatalogOption, ...]
    source: str = "empty"

    def is_choice_list(self) -> bool:
        return len(self.options) >= 2


def looks_like_xor_question(question: str) -> bool:
    return bool(_XOR_QUESTION_RE.search(question or ""))


def xor_option_pair(question: str) -> tuple[str, str] | None:
    if not looks_like_xor_question(question):
        return None
    head = re.split(r"[?]", question or "", maxsplit=1)[0]
    head = re.split(r"\s+for\s+|\s+to\s+|\s+для\s+", head, maxsplit=1, flags=re.IGNORECASE)[0]
    parts = _XOR_SPLIT_RE.split(head, maxsplit=1)
    if len(parts) != 2:
        return None
    left = re.sub(
        r"^(?:should we|is it|do we|стоит ли)\s+",
        "",
        parts[0].strip(),
        flags=re.IGNORECASE,
    ).strip()
    right = parts[1].strip()
    if not left or not right:
        return None
    return left, right


def _aliases(label: str) -> tuple[str, ...]:
    words = _WORD_RE.findall(label.casefold())
    stems = [w for w in words if len(w) >= 4]
    if not stems:
        stems = [w for w in words if len(w) >= 3]
    return tuple(dict.fromkeys(stems))


def option_catalog(
    question: str,
    *,
    options: list[str] | None = None,
) -> OptionCatalog:
    """Список вариантов: явный (C/DTO) или XOR из вопроса. Не онтология мира."""
    labels: list[str]
    source: str
    if options:
        labels = [item.strip() for item in options if item.strip()]
        source = "explicit"
    else:
        pair = xor_option_pair(question)
        if pair is not None:
            labels = [pair[0], pair[1]]
            source = "xor"
        else:
            return OptionCatalog(options=(), source="empty")
    catalog_options = tuple(
        CatalogOption(action_id=f"opt:{idx}", label=label, aliases=_aliases(label))
        for idx, label in enumerate(labels)
    )
    return OptionCatalog(options=catalog_options, source=source)


def _mention_spans(body: str, option: CatalogOption) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    folded_label = option.label.casefold()
    if len(folded_label) >= 4:
        start = 0
        while True:
            at = body.find(folded_label, start)
            if at < 0:
                break
            spans.append((at, at + len(folded_label)))
            start = at + len(folded_label)
    for alias in option.aliases:
        pattern = re.compile(
            rf"(?<![a-zа-яё0-9]){re.escape(alias)}(?![a-zа-яё0-9])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(body):
            spans.append((match.start(), match.end()))
    spans.sort()
    deduped: list[tuple[int, int]] = []
    for span in spans:
        if not deduped or span[0] >= deduped[-1][1]:
            deduped.append(span)
    return deduped


def _polarity(tokens: list[str]) -> str:
    """neg | aff | none — отрицание побеждает; ничьих first-occurrence нет."""
    neg = any(tok in _NEG_TOKENS or tok.endswith("n't") for tok in tokens)
    aff = any(tok in _AFFIRM_TOKENS for tok in tokens)
    if neg:
        return "neg"
    if aff:
        return "aff"
    return "none"


def _clauses(body: str) -> list[str]:
    return [part.strip() for part in _CLAUSE_RE.split(body) if part.strip()]


def _clause_mentions(
    clause: str, catalog: OptionCatalog
) -> list[tuple[int, int, CatalogOption]]:
    found: list[tuple[int, int, CatalogOption]] = []
    for option in catalog.options:
        for start, end in _mention_spans(clause, option):
            found.append((start, end, option))
    found.sort(key=lambda item: (item[0], item[1]))
    return found


def _score_clause(clause: str, catalog: OptionCatalog) -> dict[str, str]:
    """action_id → neg|aff|none. Окно — только до соседней опции в этой клаузе."""
    scores: dict[str, str] = {}
    mentions = _clause_mentions(clause, catalog)
    for index, (start, end, option) in enumerate(mentions):
        prev_end = mentions[index - 1][1] if index else 0
        next_start = mentions[index + 1][0] if index + 1 < len(mentions) else len(clause)
        pre = _WORD_RE.findall(clause[prev_end:start].casefold())
        post = _WORD_RE.findall(clause[end:next_start].casefold())[:3]
        scores[option.action_id] = _polarity(pre + post)
    return scores


def _conditions(text: str) -> list[Condition]:
    found: list[Condition] = []
    for match in _MATERIAL_COND_RE.finditer(text or ""):
        clause = match.group(0).strip()
        slug = re.sub(r"[^a-zа-яё0-9]+", "-", clause.casefold()).strip("-")[:48]
        found.append(
            Condition(
                condition_id=f"cond:{slug or 'material'}",
                text=clause,
                kind=StatementKind.ASSUMPTION,
                verification=VerificationStatus.UNKNOWN,
                material=True,
            )
        )
    return found


def _unresolved(text: str, *, provenance: str) -> ActionContract:
    return ActionContract(
        action_id=UNRESOLVED_ID,
        label="",
        conditions=_conditions(text),
        relation=ActionRelation.UNKNOWN,
        provenance=provenance,
    )


def bind_action(text: str, catalog: OptionCatalog | None) -> ActionContract:
    """Привязать prose к ID из каталога. Ambiguous → unresolved, не agreement."""
    body = (text or "").strip()
    if not body:
        return _unresolved(body, provenance="empty")
    if catalog is None or not catalog.options:
        return _unresolved(body, provenance="open_question")
    numeric = _NUMERIC_RE.match(body)
    if numeric is not None:
        idx = int(numeric.group(1)) - 1
        if 0 <= idx < len(catalog.options):
            chosen = catalog.options[idx]
            return ActionContract(
                action_id=chosen.action_id,
                label=chosen.label,
                conditions=_conditions(body),
                provenance="numeric_option",
            )
        return _unresolved(body, provenance="numeric_out_of_range")
    if _NO_RE.match(body):
        return _unresolved(body, provenance="bare_no")

    folded = body.casefold()
    scores_by_id: dict[str, list[str]] = {option.action_id: [] for option in catalog.options}
    for clause in _clauses(folded):
        for action_id, polarity in _score_clause(clause, catalog).items():
            scores_by_id[action_id].append(polarity)

    by_id = {option.action_id: option for option in catalog.options}
    affirmed: list[CatalogOption] = []
    rejected: list[CatalogOption] = []
    mentioned: list[CatalogOption] = []
    for action_id, scores in scores_by_id.items():
        if not scores:
            continue
        option = by_id[action_id]
        mentioned.append(option)
        if any(score == "neg" for score in scores) and not any(score == "aff" for score in scores):
            rejected.append(option)
        elif any(score == "aff" for score in scores) or all(score == "none" for score in scores):
            affirmed.append(option)

    if len(affirmed) == 1:
        chosen = affirmed[0]
        return ActionContract(
            action_id=chosen.action_id,
            label=chosen.label,
            conditions=_conditions(body),
            provenance="option_list",
        )
    if len(affirmed) == 0 and len(mentioned) == 1 and mentioned[0] not in rejected:
        chosen = mentioned[0]
        return ActionContract(
            action_id=chosen.action_id,
            label=chosen.label,
            conditions=_conditions(body),
            provenance="option_list",
        )
    return _unresolved(body, provenance="ambiguous")


def actions_equivalent(left: ActionContract, right: ActionContract) -> bool | None:
    """True/False/None. None (unknown) не сливает и не стабилизирует."""
    if left.action_id == UNRESOLVED_ID or right.action_id == UNRESOLVED_ID:
        return None
    if left.action_id != right.action_id:
        return False
    left_conds = {c.condition_id for c in left.conditions if c.material}
    right_conds = {c.condition_id for c in right.conditions if c.material}
    if left_conds != right_conds:
        return False
    return True


def relate(prior: ActionContract, current: ActionContract) -> ActionRelation:
    eq = actions_equivalent(prior, current)
    if eq is None:
        return ActionRelation.UNKNOWN
    if eq:
        return ActionRelation.SAME
    if prior.action_id == current.action_id:
        return ActionRelation.REFINED
    return ActionRelation.CHANGED


def attach_action(
    thesis: str,
    answer: str,
    catalog: OptionCatalog | None,
    *,
    prior: ActionContract | None = None,
    provenance: str | None = None,
) -> ActionContract:
    action = bind_action(f"{thesis}\n{answer}", catalog)
    if provenance:
        action = action.model_copy(update={"provenance": provenance})
    if prior is not None:
        action = action.model_copy(update={"relation": relate(prior, action)})
    return action


def inspect_premise(question: str) -> PremiseProbe:
    """Триггер — проверка. XOR-опция «always» не считается loaded premise."""
    text = question or ""
    if _WHY_IS_RE.search(text):
        return PremiseProbe(
            role=PremiseRole.ASKED_PROPOSITION,
            kind=StatementKind.EMPIRICAL_CLAIM,
            verification=VerificationStatus.UNKNOWN,
            trigger="why is",
            text=text.strip(),
        )
    background = _BACKGROUND_RE.search(text)
    if background is not None:
        return PremiseProbe(
            role=PremiseRole.BACKGROUND,
            kind=StatementKind.ASSUMPTION,
            verification=VerificationStatus.UNKNOWN,
            trigger=background.group(0).casefold(),
            text=text.strip(),
        )
    if looks_like_xor_question(text):
        return PremiseProbe()
    always = _ALWAYS_NEVER_RE.search(text)
    if always is not None:
        return PremiseProbe(
            role=PremiseRole.ASKED_PROPOSITION,
            kind=StatementKind.EMPIRICAL_CLAIM,
            verification=VerificationStatus.UNKNOWN,
            trigger=always.group(0).casefold(),
            text=text.strip(),
        )
    return PremiseProbe()


def apply_premise_evidence(
    probe: PremiseProbe,
    *,
    supports: bool | None,
) -> PremiseProbe:
    """Статус только от evidence, не от одного и того же trigger-слова."""
    if supports is True:
        kind = (
            StatementKind.USER_CONSTRAINT
            if probe.role is PremiseRole.BACKGROUND
            else StatementKind.EMPIRICAL_CLAIM
        )
        return probe.model_copy(
            update={"verification": VerificationStatus.SUPPORTED, "kind": kind}
        )
    if supports is False:
        return probe.model_copy(update={"verification": VerificationStatus.REFUTED})
    return probe.model_copy(update={"verification": VerificationStatus.UNKNOWN})


def material_condition_ids(action: ActionContract) -> tuple[str, ...]:
    return tuple(sorted(c.condition_id for c in action.conditions if c.material))


def decision_fingerprint(
    theses: list[str],
    catalog: OptionCatalog | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Слепок решения для stability. unresolved не канонизируется в ложное id."""
    parts: list[tuple[str, tuple[str, ...]]] = []
    for thesis in theses:
        action = bind_action(thesis, catalog)
        if action.action_id == UNRESOLVED_ID:
            parts.append((UNRESOLVED_ID, ("?",)))
        else:
            parts.append((action.action_id, material_condition_ids(action)))
    return tuple(sorted(parts))


def fingerprints_stable(
    current: tuple[tuple[str, tuple[str, ...]], ...],
    previous: tuple[tuple[str, tuple[str, ...]], ...] | None,
) -> bool:
    if previous is None:
        return False
    if any(item[0] == UNRESOLVED_ID for item in current + previous):
        return False
    return current == previous
