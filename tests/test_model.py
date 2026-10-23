"""Tests for the forecast engine and the accounting identities.

These run offline against synthetic statements. A model's correctness is a
property of its arithmetic, not of the company it is pointed at, and a test
suite that needs the SEC to be reachable cannot tell a broken formula from a
failed download.
"""

from __future__ import annotations

import pytest

from fpa import checks, drivers
from fpa.statements import YearState


def make_year(year: int = 2020, **overrides) -> YearState:
    """A small, internally consistent balance sheet to project from."""
    state = YearState(
        year=year,
        revenue=1_000.0,
        cost_of_sales=880.0,
        sganda=80.0,
        operating_income=40.0,
        interest_expense=5.0,
        tax_expense=9.0,
        net_income=26.0,
        cash=100.0,
        receivables=20.0,
        inventory=120.0,
        other_assets=30.0,
        ppe_net=400.0,
        payables=140.0,
        other_liabilities=60.0,
        long_term_debt=200.0,
        equity=270.0,
        minority_interest=0.0,
        depreciation=40.0,
        capex=50.0,
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    state.pretax_income = state.net_income + state.tax_expense
    state.total_assets = (
        state.cash + state.receivables + state.inventory
        + state.other_assets + state.ppe_net
    )
    state.total_liabilities = (
        state.payables + state.other_liabilities + state.long_term_debt
    )
    state.equity = (
        state.total_assets - state.total_liabilities - state.minority_interest
    )
    return state


BASE = drivers.Drivers(
    revenue_growth=0.08, gross_margin=0.12, sganda_ratio=0.08,
    interest_rate=0.025, tax_rate=0.25, dso_days=5.0, dio_days=30.0,
    dpo_days=35.0, other_assets_ratio=0.03, other_liabilities_ratio=0.06,
    capex_intensity=0.05, depreciation_rate=0.10, payout_ratio=0.5,
)


class TestBalanceSheet:
    def test_opening_year_balances(self):
        assert make_year().balance_gap == pytest.approx(0.0, abs=1e-9)

    def test_every_forecast_year_balances(self):
        # The load-bearing test. Cash comes out of the cash-flow statement and
        # the balance sheet is assembled from the drivers independently; if the
        # two disagree by a cent, this fails.
        for state in drivers.project(make_year(), BASE, 10):
            assert state.balance_gap == pytest.approx(0.0, abs=1e-6), state.year

    def test_minority_interest_is_part_of_the_identity(self):
        # Costco's fiscal 2021 sheet misses by exactly its $514m minority
        # stake if that stake is left out of the claims side.
        with_minority = make_year(minority_interest=50.0)
        assert with_minority.balance_gap == pytest.approx(0.0, abs=1e-9)
        with_minority.minority_interest = 0.0
        assert with_minority.balance_gap == pytest.approx(50.0)

    def test_minority_interest_carries_through_the_forecast(self):
        for state in drivers.project(make_year(minority_interest=50.0), BASE, 3):
            assert state.minority_interest == 50.0
            assert state.balance_gap == pytest.approx(0.0, abs=1e-6)


class TestCashFlow:
    def test_cash_flow_explains_the_movement_in_cash(self):
        previous = make_year()
        for state in drivers.project(previous, BASE, 5):
            assert state.change_in_cash == pytest.approx(
                state.cash - previous.cash, abs=1e-6
            )
            previous = state

    def test_cash_is_derived_not_assumed(self):
        """Changing a working-capital driver must move cash.

        If cash were a plug chosen to make the sheet balance, a change in
        inventory days would be absorbed silently and this would not move.
        """
        base = drivers.project(make_year(), BASE, 1)[0]
        slower = drivers.project(make_year(), BASE.shocked(dio_days=10.0), 1)[0]
        assert slower.cash < base.cash
        assert slower.inventory > base.inventory


class TestDrivers:
    def test_measure_and_project_round_trip_on_the_balance_sheet(self):
        """Drivers measured between two years must rebuild the second one.

        Only the lines the model actually forecasts are checked. Reported
        operating income includes items this model does not carry -- other
        income, impairments -- so it is deliberately not asserted here, and
        that limitation is stated in the README rather than hidden behind a
        loose tolerance.
        """
        first = make_year(2020)
        second = drivers.project(first, BASE, 1)[0]
        measured = drivers.measure(first, second)
        rebuilt = drivers.project_year(first, measured, second.year)

        for line in ("revenue", "cost_of_sales", "sganda", "receivables",
                     "inventory", "payables", "ppe_net", "equity", "cash",
                     "total_assets", "net_income"):
            assert getattr(rebuilt, line) == pytest.approx(
                getattr(second, line), rel=1e-9
            ), line

    def test_shock_rejects_an_unknown_driver(self):
        with pytest.raises(ValueError, match="unknown driver"):
            BASE.shocked(profit_margin_magic=0.1)

    def test_shock_is_additive_and_leaves_the_original_alone(self):
        shocked = BASE.shocked(gross_margin=0.01)
        assert shocked.gross_margin == pytest.approx(0.13)
        assert BASE.gross_margin == pytest.approx(0.12)

    def test_growth_compounds_over_the_horizon(self):
        states = drivers.project(make_year(), BASE, 3)
        assert states[2].revenue == pytest.approx(1000.0 * 1.08 ** 3)


class TestChecks:
    def test_balance_check_catches_a_broken_sheet(self):
        broken = make_year()
        broken.total_assets += 1_000.0
        results = checks.check_balance([broken], "test")
        assert not results[0].passed
        assert results[0].blocking

    def test_balance_check_passes_on_a_sound_sheet(self):
        assert checks.check_balance([make_year()], "test")[0].passed

    def test_cash_flow_check_catches_an_unexplained_movement(self):
        previous = make_year()
        states = drivers.project(previous, BASE, 2)
        states[0].cash += 500.0          # money from nowhere
        assert not checks.check_cashflow(states, previous)[0].passed

    def test_driver_bounds_reject_an_impossible_margin(self):
        results = checks.check_drivers(BASE.shocked(gross_margin=5.0))
        failed = [r for r in results if not r.passed]
        assert any("gross_margin" in r.check_id for r in failed)

    def test_negative_cash_is_a_warning_not_a_failure(self):
        """An unfunded plan is a finding, not a bug.

        The model has no revolver, so a plan that runs out of money shows up as
        negative cash. That is information about the plan; failing the run
        would wrongly suggest the arithmetic is broken.
        """
        starved = drivers.project(
            make_year(cash=1.0), BASE.shocked(capex_intensity=0.40), 2
        )
        results = checks.check_forecast(starved)
        cash_check = next(r for r in results if "cash" in r.check_id)
        assert not cash_check.passed
        assert not cash_check.blocking
