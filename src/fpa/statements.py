"""Historical financial statements, assembled from the spliced lines.

Two derived items do the work that makes a three-statement model close.

A real balance sheet has dozens of lines this model does not forecast
individually -- deferred taxes, leases, goodwill, accrued payroll. Rather than
pretend they do not exist or model each one, they are collapsed into
`other_assets` and `other_liabilities`, defined as whatever the reported totals
leave over:

    other_assets      = total_assets      - cash - receivables - inventory - ppe
    other_liabilities = total_liabilities - payables - long_term_debt

That keeps the identity exact by construction on history, and leaves one
honest driver per side to carry everything else forward.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from .concepts import Line

# Lines the model cannot run without.
REQUIRED = [
    "revenue", "cost_of_sales", "sganda", "operating_income", "tax_expense",
    "net_income", "total_assets", "total_liabilities", "equity", "cash",
    "inventory", "receivables", "payables", "ppe_net", "long_term_debt",
    "depreciation", "capex",
]


@dataclass
class YearState:
    """One fiscal year of fully linked statements."""

    year: int

    # Income statement
    revenue: float = 0.0
    cost_of_sales: float = 0.0
    sganda: float = 0.0
    operating_income: float = 0.0
    interest_expense: float = 0.0
    pretax_income: float = 0.0
    tax_expense: float = 0.0
    net_income: float = 0.0

    # Balance sheet
    cash: float = 0.0
    receivables: float = 0.0
    inventory: float = 0.0
    other_assets: float = 0.0
    ppe_net: float = 0.0
    total_assets: float = 0.0
    payables: float = 0.0
    other_liabilities: float = 0.0
    long_term_debt: float = 0.0
    total_liabilities: float = 0.0
    equity: float = 0.0
    minority_interest: float = 0.0

    # Cash flow
    depreciation: float = 0.0
    capex: float = 0.0
    distributions: float = 0.0
    cfo: float = 0.0
    cfi: float = 0.0
    cff: float = 0.0

    is_forecast: bool = False

    @property
    def gross_profit(self) -> float:
        return self.revenue - self.cost_of_sales

    @property
    def gross_margin(self) -> float:
        return self.gross_profit / self.revenue if self.revenue else 0.0

    @property
    def balance_gap(self) -> float:
        """Assets minus the full claim on them. Must be zero.

        Minority interest belongs on this side of the identity: it is a claim
        on the company's assets that `StockholdersEquity` does not include.
        Omitting it makes the sheet fail to balance by exactly the minority
        stake -- which is how it was found.
        """
        return self.total_assets - (
            self.total_liabilities + self.equity + self.minority_interest
        )

    @property
    def change_in_cash(self) -> float:
        return self.cfo + self.cfi + self.cff

    def to_dict(self) -> dict:
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        out["gross_profit"] = self.gross_profit
        out["gross_margin"] = self.gross_margin
        out["balance_gap"] = self.balance_gap
        return out


def build_history(lines: dict[str, Line], years: list[int]) -> dict[int, YearState]:
    """Assemble reported statements for the years every required line covers."""
    history: dict[int, YearState] = {}
    for year in years:
        def v(name: str) -> float:
            return float(lines[name].values.get(year, 0.0))

        state = YearState(
            year=year,
            revenue=v("revenue"),
            cost_of_sales=v("cost_of_sales"),
            sganda=v("sganda"),
            operating_income=v("operating_income"),
            interest_expense=v("interest_expense"),
            tax_expense=v("tax_expense"),
            net_income=v("net_income"),
            cash=v("cash"),
            receivables=v("receivables"),
            inventory=v("inventory"),
            ppe_net=v("ppe_net"),
            total_assets=v("total_assets"),
            payables=v("payables"),
            long_term_debt=v("long_term_debt"),
            total_liabilities=v("total_liabilities"),
            equity=v("equity"),
            minority_interest=v("minority_interest"),
            depreciation=v("depreciation"),
            capex=v("capex"),
            cfo=v("cfo"),
            cfi=v("cfi"),
            cff=v("cff"),
        )
        # Pre-tax income is reported inconsistently across filings, so it is
        # derived from two figures that are not: net income and tax expense.
        state.pretax_income = state.net_income + state.tax_expense
        state.other_assets = state.total_assets - (
            state.cash + state.receivables + state.inventory + state.ppe_net
        )
        state.other_liabilities = state.total_liabilities - (
            state.payables + state.long_term_debt
        )
        history[year] = state

    # Distributions to shareholders -- dividends, buybacks, issuance and other
    # comprehensive income netted together -- are what reconciles retained
    # earnings. Naming it `distributions` rather than `dividends` keeps the
    # label honest about what it actually contains.
    ordered = sorted(history)
    for previous, current in zip(ordered, ordered[1:]):
        history[current].distributions = (
            history[current].net_income
            - (history[current].equity - history[previous].equity)
        )
    return history
