"""B1–B4: premise unknown≠false; action ID from option list, not first token."""

from zhoda_core.actions import (
    UNRESOLVED_ID,
    actions_equivalent,
    apply_premise_evidence,
    bind_action,
    inspect_premise,
    option_catalog,
)
from zhoda_core.models import PremiseRole, StatementKind, VerificationStatus


Q_XOR = "PostgreSQL or Kafka for a 50k RPS ledger, team of four?"
Q_RU = "PostgreSQL или Kafka для реестра?"
Q_B1 = "Given that our team has four engineers, should we use a monolith?"
Q_SYC = "Why is REST always faster than gRPC for internal microservice calls?"


def _xor(question: str = Q_XOR):
    return option_catalog(question)


def test_b1_background_premise_stays_unknown() -> None:
    probe = inspect_premise(Q_B1)
    assert probe.role is PremiseRole.BACKGROUND
    assert probe.verification is VerificationStatus.UNKNOWN
    assert probe.trigger == "given that"


def test_premise_status_follows_evidence_not_trigger() -> None:
    """Контрфактическая пара: одно trigger-слово, разный evidence → разный статус."""
    given = inspect_premise(Q_B1)
    why = inspect_premise(Q_SYC)
    assert given.trigger == "given that"
    assert why.trigger == "why is"
    assert apply_premise_evidence(given, supports=None).verification is VerificationStatus.UNKNOWN
    supported = apply_premise_evidence(given, supports=True)
    assert supported.verification is VerificationStatus.SUPPORTED
    assert supported.kind is StatementKind.USER_CONSTRAINT
    refuted = apply_premise_evidence(given, supports=False)
    assert refuted.verification is VerificationStatus.REFUTED
    why_unknown = apply_premise_evidence(why, supports=None)
    assert why_unknown.verification is VerificationStatus.UNKNOWN
    why_refuted = apply_premise_evidence(why, supports=False)
    assert why_refuted.verification is VerificationStatus.REFUTED


def test_b2_reject_kafka_is_still_postgres() -> None:
    catalog = _xor()
    old = bind_action("Use PostgreSQL.", catalog)
    new = bind_action(
        "Reject Kafka. PostgreSQL remains the recommendation.",
        catalog,
    )
    assert old.action_id == new.action_id == "opt:0"
    assert old.label.lower().startswith("postgresql")
    assert actions_equivalent(old, new) is True


def test_b3_instead_of_kafka_is_paraphrase() -> None:
    catalog = _xor()
    old = bind_action("Use PostgreSQL, not Kafka.", catalog)
    new = bind_action("Instead of Kafka, use PostgreSQL.", catalog)
    assert old.action_id == new.action_id == "opt:0"
    assert actions_equivalent(old, new) is True


def test_b4_unsuitable_postgres_changes_action() -> None:
    catalog = _xor()
    old = bind_action("Use PostgreSQL, not Kafka.", catalog)
    new = bind_action("PostgreSQL is unsuitable; use Kafka.", catalog)
    assert old.action_id == "opt:0"
    assert new.action_id == "opt:1"
    assert actions_equivalent(old, new) is False


def test_russian_xor_and_negation() -> None:
    catalog = _xor(Q_RU)
    pg = bind_action("Используй PostgreSQL, не Kafka.", catalog)
    kf = bind_action("Вместо PostgreSQL выбрать Kafka.", catalog)
    assert pg.action_id == "opt:0"
    assert kf.action_id == "opt:1"
    assert actions_equivalent(pg, kf) is False


def test_numeric_options_map_to_catalog_ids() -> None:
    catalog = _xor()
    assert bind_action("1", catalog).action_id == "opt:0"
    assert bind_action("2.", catalog).action_id == "opt:1"
    assert bind_action("option 2", catalog).action_id == "opt:1"
    assert bind_action("9", catalog).action_id == UNRESOLVED_ID


def test_bare_no_and_empty_are_unresolved() -> None:
    catalog = _xor()
    assert bind_action("No", catalog).action_id == UNRESOLVED_ID
    assert bind_action("Нет", catalog).action_id == UNRESOLVED_ID
    assert bind_action("", catalog).action_id == UNRESOLVED_ID
    assert bind_action("   ", catalog).action_id == UNRESOLVED_ID
    assert actions_equivalent(bind_action("", catalog), bind_action("No", catalog)) is None


def test_unknown_action_does_not_agree() -> None:
    catalog = _xor()
    both = bind_action("Redis is the store; mention PostgreSQL and Kafka.", catalog)
    assert both.action_id == UNRESOLVED_ID
    assert both.provenance == "ambiguous"
    named = bind_action("Use PostgreSQL.", catalog)
    assert actions_equivalent(both, named) is None


def test_open_question_is_unresolved() -> None:
    catalog = option_catalog("Should we refactor the billing module?")
    action = bind_action("Wrap it behind an API.", catalog)
    assert action.action_id == UNRESOLVED_ID
    assert action.provenance == "open_question"


def test_explicit_option_list_ids_are_from_the_list() -> None:
    catalog = option_catalog("pick one", options=["Monolith", "Microservices"])
    assert catalog.options[0].action_id == "opt:0"
    bound = bind_action("Microservices from day one.", catalog)
    assert bound.action_id == "opt:1"
    assert bound.label == "Microservices"
