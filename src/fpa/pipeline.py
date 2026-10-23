"""Run the whole model end to end.

Kept apart from the CLI so a notebook, a test or another script can get the
same object back without going through argument parsing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import backtest as bt
from . import checks, concepts, drivers, scenarios
from .config import load_config
from .edgar import Client, Provenance
from .statements import REQUIRED, YearState, build_history

# The lines that must come from one filing at a time. Mixing a restated asset
# with an un-restated liability breaks the balance sheet by exactly the
# restatement -- see `concepts.align_vintages`.
BALANCE_SHEET_GROUP = [
    "total_assets", "total_liabilities", "equity", "minority_interest",
    "cash", "receivables", "inventory", "payables", "ppe_net", "long_term_debt",
]


@dataclass
class Run:
    config: dict
    lines: dict
    history: dict[int, YearState]
    base_drivers: drivers.Drivers
    driver_years: list[int]
    scenarios: dict[str, scenarios.Scenario]
    backtest: bt.BacktestResult
    tornado_net_income: list[scenarios.SensitivityRow]
    tornado_cash: list[scenarios.SensitivityRow]
    checks: list[checks.CheckResult]
    vintage_fixes: list = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)

    @property
    def last_actual(self) -> YearState:
        return self.history[max(self.history)]

    @property
    def summary(self) -> dict:
        return checks.summarise(self.checks)


def run(config: dict | None = None) -> Run:
    cfg = config or load_config()
    client = Client(
        user_agent=cfg["edgar"]["user_agent"],
        base_url=cfg["edgar"]["base_url"],
    )
    facts = client.company_facts(cfg["company"]["cik"])

    lines = concepts.build_lines(facts, cfg)
    years = concepts.common_years(lines, REQUIRED)
    if not years:
        raise RuntimeError("no year has every required line")
    fixes = concepts.align_vintages(lines, BALANCE_SHEET_GROUP, years)

    history = build_history(lines, years)
    lookback = cfg["drivers"]["lookback_years"]
    horizon = cfg["drivers"]["forecast_years"]
    base, driver_years = drivers.base_case(history, lookback)
    last = history[max(history)]

    built = scenarios.build(last, base, cfg, horizon)
    result = bt.run(
        history,
        lookback=lookback,
        horizons=cfg["backtest"]["horizons"],
        min_history=cfg["backtest"]["min_history_years"],
    )

    collected: list[checks.CheckResult] = []
    collected += checks.check_splices(lines)
    collected += checks.check_gaps(lines, REQUIRED)
    collected += checks.check_vintages(fixes)
    collected += checks.check_balance(history, "reported")
    collected += checks.check_balance(built["base"].states, "forecast")
    collected += checks.check_cashflow(built["base"].states, last)
    collected += checks.check_drivers(base)
    collected += checks.check_forecast(built["base"].states)

    return Run(
        config=cfg,
        lines=lines,
        history=history,
        base_drivers=base,
        driver_years=driver_years,
        scenarios=built,
        backtest=result,
        tornado_net_income=scenarios.tornado(last, base, cfg, horizon, "net_income"),
        # Working-capital and capex drivers move cash without touching the
        # income statement, so a net-income tornado shows them as zero. That is
        # correct, and it is also why a second tornado on cash is reported:
        # together they say where each assumption actually lands.
        tornado_cash=scenarios.tornado(last, base, cfg, horizon, "cash"),
        checks=collected,
        vintage_fixes=fixes,
        provenance=client.records,
    )


def report_context(run_result: Run) -> dict:
    """Shape a Run into what the HTML template expects."""
    cfg = run_result.config
    history = run_result.history
    base = run_result.scenarios["base"]
    statements = [history[y] for y in sorted(history)] + list(base.states)
    worst = max(abs(s.balance_gap) for s in statements)

    def tornado_block(label, rows):
        return {
            "label": label,
            "rows": rows,
            "max_swing": max((r.swing_pct for r in rows), default=0.0),
        }

    return {
        "company": f"{cfg['company']['name']} ({cfg['company']['ticker']})",
        "first_year": min(history),
        "last_actual": max(history),
        "history_years": len(history),
        "forecast_years": cfg["drivers"]["forecast_years"],
        "summary": run_result.summary,
        "checks": run_result.checks,
        "balance_exact": worst <= checks.BALANCE_TOLERANCE,
        "worst_balance": f"{worst:,.2f}",
        "revenue_mape": f"{run_result.backtest.mape('revenue', 1):.1%}",
        "naive_mape": f"{run_result.backtest.naive_mape('revenue', 1):.1%}",
        "backtest": run_result.backtest.table(),
        "backtest_origins": len(run_result.backtest.origins),
        "scenarios": [
            {"label": s.label, "drivers": s.drivers.to_dict()}
            for s in run_result.scenarios.values()
        ],
        "driver_names": list(run_result.base_drivers.to_dict()),
        "statements": statements,
        "tornadoes": [
            tornado_block("terminal net income", run_result.tornado_net_income),
            tornado_block("terminal cash", run_result.tornado_cash),
        ],
        "sources": run_result.provenance,
    }
