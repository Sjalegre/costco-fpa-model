"""Driver decomposition and the forecast engine.

A driver-based model forecasts the *ratios* a business runs on and lets the
statements fall out of them, rather than extrapolating each line separately.
The difference matters: extrapolate revenue and inventory independently and
they drift apart until the implied inventory turn is impossible, and nothing in
the model notices.

Every driver here is measured from reported history. None is chosen to make a
forecast look good.

The forecast builds all three statements and lets cash fall out of the cash
flow statement. Nothing is plugged. That is what makes the balance-sheet check
in `checks.py` a real test: if the cash-flow logic and the balance-sheet logic
disagree, the sheet stops balancing and the run fails.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace

from .statements import YearState

DAYS = 365.0


@dataclass(frozen=True)
class Drivers:
    """The ratios one forecast year is built from."""

    revenue_growth: float = 0.0
    gross_margin: float = 0.0
    sganda_ratio: float = 0.0
    interest_rate: float = 0.0      # on opening long-term debt
    tax_rate: float = 0.0
    dso_days: float = 0.0           # receivables / revenue
    dio_days: float = 0.0           # inventory / cost of sales
    dpo_days: float = 0.0           # payables / cost of sales
    other_assets_ratio: float = 0.0       # of revenue
    other_liabilities_ratio: float = 0.0  # of revenue
    capex_intensity: float = 0.0    # of revenue
    depreciation_rate: float = 0.0  # of opening net PP&E
    payout_ratio: float = 0.0       # distributions / net income

    def shocked(self, **deltas: float) -> "Drivers":
        """Return a copy with additive shocks applied to named drivers."""
        known = {f.name for f in fields(self)}
        unknown = set(deltas) - known
        if unknown:
            raise ValueError(f"unknown driver(s): {sorted(unknown)}")
        return replace(self, **{k: getattr(self, k) + v for k, v in deltas.items()})

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _safe(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def measure(previous: YearState, current: YearState) -> Drivers:
    """Back out the drivers that turn `previous` into `current`.

    Needs both years because growth, depreciation and interest are all measured
    against an opening balance.
    """
    return Drivers(
        revenue_growth=_safe(current.revenue, previous.revenue) - 1.0,
        gross_margin=current.gross_margin,
        sganda_ratio=_safe(current.sganda, current.revenue),
        interest_rate=_safe(current.interest_expense, previous.long_term_debt),
        tax_rate=_safe(current.tax_expense, current.pretax_income),
        dso_days=_safe(current.receivables, current.revenue) * DAYS,
        dio_days=_safe(current.inventory, current.cost_of_sales) * DAYS,
        dpo_days=_safe(current.payables, current.cost_of_sales) * DAYS,
        other_assets_ratio=_safe(current.other_assets, current.revenue),
        other_liabilities_ratio=_safe(current.other_liabilities, current.revenue),
        capex_intensity=_safe(current.capex, current.revenue),
        depreciation_rate=_safe(current.depreciation, previous.ppe_net),
        payout_ratio=_safe(current.distributions, current.net_income),
    )


def historical(history: dict[int, YearState]) -> dict[int, Drivers]:
    """Measure drivers for every year that has a predecessor."""
    years = sorted(history)
    return {
        current: measure(history[previous], history[current])
        for previous, current in zip(years, years[1:])
    }


def average(measured: dict[int, Drivers], years: list[int]) -> Drivers:
    """Mean of each driver over the given years.

    A plain mean, deliberately. A weighted or trend-fitted average would be a
    modelling choice that quietly encodes a view, and the backtest would then
    be scoring that view rather than the method.
    """
    if not years:
        raise ValueError("no years to average over")
    picked = [measured[y] for y in years if y in measured]
    if not picked:
        raise ValueError(f"no measured drivers in {years}")
    names = [f.name for f in fields(Drivers)]
    return Drivers(**{
        name: sum(getattr(d, name) for d in picked) / len(picked)
        for name in names
    })


def base_case(history: dict[int, YearState], lookback: int) -> tuple[Drivers, list[int]]:
    """Average the last `lookback` years of measured drivers."""
    measured = historical(history)
    years = sorted(measured)[-lookback:]
    return average(measured, years), years


def project_year(previous: YearState, drivers: Drivers, year: int) -> YearState:
    """Build one forecast year from the prior year's balance sheet.

    Order matters and follows the causal chain: revenue, then the income
    statement, then the working-capital balances that revenue and cost imply,
    then the cash flow, and only then cash -- which is an output, never an
    input.
    """
    revenue = previous.revenue * (1.0 + drivers.revenue_growth)
    cost_of_sales = revenue * (1.0 - drivers.gross_margin)
    sganda = revenue * drivers.sganda_ratio
    depreciation = previous.ppe_net * drivers.depreciation_rate
    # Operating income is after depreciation, which is already inside cost of
    # sales and SG&A as reported. It is added back in the cash flow, not
    # subtracted twice here.
    operating_income = revenue - cost_of_sales - sganda
    interest_expense = previous.long_term_debt * drivers.interest_rate
    pretax_income = operating_income - interest_expense
    tax_expense = pretax_income * drivers.tax_rate
    net_income = pretax_income - tax_expense

    receivables = revenue * drivers.dso_days / DAYS
    inventory = cost_of_sales * drivers.dio_days / DAYS
    payables = cost_of_sales * drivers.dpo_days / DAYS
    other_assets = revenue * drivers.other_assets_ratio
    other_liabilities = revenue * drivers.other_liabilities_ratio

    capex = revenue * drivers.capex_intensity
    ppe_net = previous.ppe_net + capex - depreciation
    long_term_debt = previous.long_term_debt          # held flat by assumption
    # Carried forward unchanged. It is zero from 2023 on, and forecasting a
    # minority stake would mean forecasting a subsidiary this model cannot see.
    minority_interest = previous.minority_interest
    distributions = net_income * drivers.payout_ratio
    equity = previous.equity + net_income - distributions

    working_capital_change = (
        (receivables - previous.receivables)
        + (inventory - previous.inventory)
        + (other_assets - previous.other_assets)
        - (payables - previous.payables)
        - (other_liabilities - previous.other_liabilities)
    )
    cfo = net_income + depreciation - working_capital_change
    cfi = -capex
    cff = (long_term_debt - previous.long_term_debt) - distributions
    cash = previous.cash + cfo + cfi + cff

    total_assets = cash + receivables + inventory + other_assets + ppe_net
    total_liabilities = payables + other_liabilities + long_term_debt

    return YearState(
        year=year,
        revenue=revenue,
        cost_of_sales=cost_of_sales,
        sganda=sganda,
        operating_income=operating_income,
        interest_expense=interest_expense,
        pretax_income=pretax_income,
        tax_expense=tax_expense,
        net_income=net_income,
        cash=cash,
        receivables=receivables,
        inventory=inventory,
        other_assets=other_assets,
        ppe_net=ppe_net,
        total_assets=total_assets,
        payables=payables,
        other_liabilities=other_liabilities,
        long_term_debt=long_term_debt,
        total_liabilities=total_liabilities,
        equity=equity,
        minority_interest=minority_interest,
        depreciation=depreciation,
        capex=capex,
        distributions=distributions,
        cfo=cfo,
        cfi=cfi,
        cff=cff,
        is_forecast=True,
    )


def project(
    last_actual: YearState, drivers: Drivers, horizon: int
) -> list[YearState]:
    """Roll the model forward `horizon` years from the last reported year."""
    out: list[YearState] = []
    previous = last_actual
    for step in range(1, horizon + 1):
        current = project_year(previous, drivers, last_actual.year + step)
        out.append(current)
        previous = current
    return out
