"""Request-count vs cost matching. Request-matched ≠ compute-matched.

Tolerance заранее задана. Mandatory/tail calls учитываются.
Если минимум council > target — infeasible, не «matched».
"""

from __future__ import annotations

from dataclasses import dataclass

STATUS_REFERENCE = "reference"
STATUS_MATCHED = "matched"
STATUS_UNMATCHED = "unmatched"
STATUS_INFEASIBLE = "infeasible"

# Относительный допуск + пол: 0.04 vs 0.10/0.20 никогда не matched.
USD_REL_TOL = 0.15
USD_ABS_TOL = 0.005
TOKEN_REL_TOL = 0.15
TOKEN_ABS_TOL = 50
REQUEST_ABS_TOL = 0  # request-matched только точное равенство


def min_council_calls(n_models: int) -> int:
    """Каждый член совета + chairman. C=1 при 3+1 вызовах — не «one request»."""
    return max(n_models, 0) + 1


def _within(actual: float, target: float, rel: float, abs_tol: float) -> bool:
    return abs(actual - target) <= max(abs(target) * rel, abs_tol)


@dataclass(frozen=True)
class MatchVerdict:
    status: str
    reason: str = ""
    target: float = 0.0
    actual: float = 0.0
    minimum: float = 0.0


def request_match(
    *,
    actual_requests: int,
    target_requests: int,
    min_mandatory: int,
    is_reference: bool = False,
) -> MatchVerdict:
    if is_reference:
        return MatchVerdict(
            STATUS_REFERENCE, "zhoda reference",
            target=float(target_requests), actual=float(actual_requests),
            minimum=float(min_mandatory),
        )
    if min_mandatory > target_requests:
        return MatchVerdict(
            STATUS_INFEASIBLE,
            f"mandatory {min_mandatory} calls exceed target {target_requests}",
            target=float(target_requests),
            actual=float(actual_requests),
            minimum=float(min_mandatory),
        )
    if actual_requests == target_requests:
        return MatchVerdict(
            STATUS_MATCHED, "request count equal",
            target=float(target_requests), actual=float(actual_requests),
            minimum=float(min_mandatory),
        )
    return MatchVerdict(
        STATUS_UNMATCHED,
        f"requests {actual_requests} != target {target_requests}",
        target=float(target_requests),
        actual=float(actual_requests),
        minimum=float(min_mandatory),
    )


def cost_match(
    *,
    actual_usd: float,
    target_usd: float | None,
    actual_tokens: int,
    target_tokens: int | None,
    min_usd: float = 0.0,
    min_tokens: int = 0,
    usd_unknown: bool = False,
    is_reference: bool = False,
) -> MatchVerdict:
    if is_reference:
        return MatchVerdict(
            STATUS_REFERENCE, "zhoda reference",
            target=float(target_usd or target_tokens or 0),
            actual=float(actual_usd if target_usd else actual_tokens),
            minimum=min_usd if target_usd else float(min_tokens),
        )
    if usd_unknown:
        return MatchVerdict(STATUS_UNMATCHED, "usd unknown — not a matched cost")
    if target_usd is not None and target_usd > 0:
        if min_usd > target_usd and not _within(min_usd, target_usd, USD_REL_TOL, USD_ABS_TOL):
            return MatchVerdict(
                STATUS_INFEASIBLE,
                f"min council usd {min_usd} exceeds target {target_usd}",
                target=target_usd, actual=actual_usd, minimum=min_usd,
            )
        if _within(actual_usd, target_usd, USD_REL_TOL, USD_ABS_TOL):
            return MatchVerdict(
                STATUS_MATCHED, "usd within tolerance",
                target=target_usd, actual=actual_usd, minimum=min_usd,
            )
        return MatchVerdict(
            STATUS_UNMATCHED,
            f"usd {actual_usd} vs target {target_usd} outside tolerance",
            target=target_usd, actual=actual_usd, minimum=min_usd,
        )
    if target_tokens is not None and target_tokens > 0:
        if min_tokens > target_tokens and not _within(
            float(min_tokens), float(target_tokens), TOKEN_REL_TOL, float(TOKEN_ABS_TOL),
        ):
            return MatchVerdict(
                STATUS_INFEASIBLE,
                f"min tokens {min_tokens} exceed target {target_tokens}",
                target=float(target_tokens),
                actual=float(actual_tokens),
                minimum=float(min_tokens),
            )
        if _within(
            float(actual_tokens), float(target_tokens), TOKEN_REL_TOL, float(TOKEN_ABS_TOL),
        ):
            return MatchVerdict(
                STATUS_MATCHED, "tokens within tolerance",
                target=float(target_tokens), actual=float(actual_tokens),
            )
        return MatchVerdict(
            STATUS_UNMATCHED,
            f"tokens {actual_tokens} vs target {target_tokens}",
            target=float(target_tokens), actual=float(actual_tokens),
        )
    return MatchVerdict(STATUS_UNMATCHED, "no cost target")
