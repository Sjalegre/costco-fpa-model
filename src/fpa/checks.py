"""Integrity checks over the model and the data it was built from.

A financial model fails quietly. Change one formula and the output is still a
grid of plausible numbers; nothing crashes. These checks exist so that the
failure is loud, and they are grouped by what a failure would actually mean:

* **sourcing**  -- the figures that went in are not what the filings say
* **identity**  -- the statements do not reconcile to each other
* **plausibility** -- the model produced numbers no business could post

The balance-sheet identity is the load-bearing one. Nothing in the forecast is
plugged: cash is an output of the cash-flow statement, and the balance sheet is
assembled independently. If the two disagree, the model is wrong, and this is
where that shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .concepts import Line, VintageFix
from .drivers import Drivers
from .statements import YearState

ERROR = "error"
WARNING = "warning"
INFO = "info"

SOURCING = "sourcing"
IDENTITY = "identity"
PLAUSIBILITY = "plausibility"

# A balance sheet reported in whole dollars should reconcile to the dollar.
# One cent of tolerance absorbs binary floating point, nothing more.
BALANCE_TOLERANCE = 0.01


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    family: str
    severity: str
    passed: bool
    expected: str
    observed: str
    detail: str | None = None
    checked_at: datetime = None  # type: ignore[assignment]

    @property
    def blocking(self) -> bool:
        return not self.passed and self.severity == ERROR


def _result(check_id, family, severity, passed, expected, observed, detail=None):
    return CheckResult(
        check_id=check_id, family=family, severity=severity, passed=passed,
        expected=expected, observed=observed, detail=detail,
        checked_at=datetime.now(timezone.utc),
    )


def check_splices(lines: dict[str, Line]) -> list[CheckResult]:
    """Every spliced line must agree with itself on its overlap years."""
    out = []
    for name, line in lines.items():
        for evidence in line.evidence:
            out.append(_result(
                f"splice.{name}.{evidence.fallback_tag}", SOURCING,
                ERROR if not evidence.agrees else INFO,
                evidence.agrees,
                "the two tags agree on every year they share",
                evidence.describe(),
                detail=f"{evidence.preferred_tag} <- {evidence.fallback_tag}",
            ))
        if not line.values:
            out.append(_result(
                f"line.{name}.present", SOURCING, ERROR, False,
                "at least one reported year", "no data under any configured tag",
            ))
    return out


def check_gaps(lines: dict[str, Line], required: list[str]) -> list[CheckResult]:
    """No required line may have a hole in the middle of its history."""
    out = []
    for name in required:
        line = lines.get(name)
        if line is None:
            continue
        out.append(_result(
            f"gaps.{name}", SOURCING, ERROR, not line.gaps,
            "no interior gaps",
            f"{len(line.gaps)} missing years: {line.gaps}" if line.gaps
            else f"{len(line.years)} continuous years",
        ))
    return out


def check_vintages(fixes: list[VintageFix]) -> list[CheckResult]:
    """Report, rather than hide, any year realigned to a single filing."""
    return [_result(
        "sourcing.vintage_alignment", SOURCING, INFO, not fixes,
        "every year already consistent across filings",
        f"{len(fixes)} year(s) realigned"
        + (": " + "; ".join(f.describe() for f in fixes) if fixes else ""),
        detail="a restated line paired with an un-restated one breaks the identity",
    )]


def check_balance(states: dict[int, YearState] | list[YearState],
                  label: str) -> list[CheckResult]:
    """Assets must equal liabilities plus equity plus minority interest."""
    items = states.values() if isinstance(states, dict) else states
    worst = None
    for state in items:
        if worst is None or abs(state.balance_gap) > abs(worst.balance_gap):
            worst = state
    if worst is None:
        return []
    passed = abs(worst.balance_gap) <= BALANCE_TOLERANCE
    return [_result(
        f"identity.balance_sheet.{label}", IDENTITY, ERROR, passed,
        f"|assets - (liabilities + equity + minority)| <= {BALANCE_TOLERANCE}",
        f"worst gap {worst.balance_gap:,.2f} in {worst.year}",
    )]


def check_cashflow(states: list[YearState], opening: YearState) -> list[CheckResult]:
    """The cash-flow statement must explain the movement in cash exactly."""
    worst_year, worst_gap = None, 0.0
    previous = opening
    for state in states:
        gap = state.change_in_cash - (state.cash - previous.cash)
        if abs(gap) > abs(worst_gap):
            worst_year, worst_gap = state.year, gap
        previous = state
    passed = abs(worst_gap) <= BALANCE_TOLERANCE
    return [_result(
        "identity.cash_flow", IDENTITY, ERROR, passed,
        "CFO + CFI + CFF equals the change in cash",
        "reconciles exactly" if passed
        else f"worst gap {worst_gap:,.2f} in {worst_year}",
    )]


def check_drivers(drivers: Drivers) -> list[CheckResult]:
    """Drivers measured from history must be economically possible."""
    out = []
    bounds = {
        "gross_margin": (0.0, 1.0),
        "sganda_ratio": (0.0, 1.0),
        "tax_rate": (0.0, 0.6),
        "payout_ratio": (-2.0, 3.0),
        "dso_days": (0.0, 180.0),
        "dio_days": (0.0, 365.0),
        "dpo_days": (0.0, 365.0),
        "capex_intensity": (0.0, 0.5),
        "revenue_growth": (-0.5, 1.0),
    }
    for name, (low, high) in bounds.items():
        value = getattr(drivers, name)
        out.append(_result(
            f"plausibility.driver.{name}", PLAUSIBILITY, ERROR,
            low <= value <= high,
            f"within [{low}, {high}]", f"{value:.4f}",
        ))
    return out


def check_forecast(states: list[YearState]) -> list[CheckResult]:
    """The forecast must describe a business that could exist."""
    out = []
    negative_revenue = [s.year for s in states if s.revenue <= 0]
    out.append(_result(
        "plausibility.revenue_positive", PLAUSIBILITY, ERROR,
        not negative_revenue, "revenue stays positive",
        "yes" if not negative_revenue else f"non-positive in {negative_revenue}",
    ))
    negative_cash = [s.year for s in states if s.cash < 0]
    out.append(_result(
        "plausibility.cash_not_negative", PLAUSIBILITY, WARNING,
        not negative_cash,
        "cash never goes negative",
        "yes" if not negative_cash
        else f"negative in {negative_cash} -- the model has no revolver, "
             f"so this means the plan is unfunded, not that cash is impossible",
    ))
    negative_equity = [s.year for s in states if s.equity <= 0]
    out.append(_result(
        "plausibility.equity_positive", PLAUSIBILITY, WARNING,
        not negative_equity, "equity stays positive",
        "yes" if not negative_equity else f"non-positive in {negative_equity}",
    ))
    return out


def summarise(results: list[CheckResult]) -> dict:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "blocking": sum(1 for r in results if r.blocking),
    }
