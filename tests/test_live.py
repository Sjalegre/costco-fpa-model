"""Assertions against the real filings.

Every figure the README quotes is checked here, so a claim that stops being
true fails the suite instead of going stale.

Skipped unless `FPA_LIVE=1`, because the rest of the suite is deliberately
offline and CI should not depend on the SEC being reachable:

    FPA_LIVE=1 pytest tests/test_live.py
"""

from __future__ import annotations

import os

import pytest

from fpa import checks
from fpa.pipeline import run

pytestmark = pytest.mark.skipif(
    os.environ.get("FPA_LIVE") != "1",
    reason="live SEC test; set FPA_LIVE=1 to run",
)


@pytest.fixture(scope="module")
def result():
    return run()


class TestSourcing:
    def test_eighteen_reported_years_with_no_gaps(self, result):
        years = sorted(result.history)
        assert len(years) == 18
        assert years == list(range(years[0], years[-1] + 1))

    def test_the_revenue_splice_is_verified_on_its_overlap(self, result):
        # `Revenues` and `RevenueFromContractWithCustomerExcludingAssessedTax`
        # both report fiscal 2016 to 2025 and agree in every one of them.
        evidence = result.lines["revenue"].evidence
        assert evidence, "revenue should be spliced from two tags"
        assert evidence[0].verified
        assert len(evidence[0].overlap_years) >= 10

    def test_every_splice_that_has_an_overlap_agrees(self, result):
        for name, line in result.lines.items():
            for ev in line.evidence:
                if ev.overlap_years:
                    assert ev.agrees, f"{name}: {ev.describe()}"

    def test_fiscal_2014_needed_a_vintage_realignment(self, result):
        # Total assets were restated in the 2016 filing while the claims side
        # was not. Without realignment the sheet misses by $362m.
        assert len(result.vintage_fixes) == 1
        fix = result.vintage_fixes[0]
        assert fix.year == 2014
        line, before, after = fix.changed[0]
        assert line == "total_assets"
        assert abs(after - before) == pytest.approx(362_000_000.0)


class TestIdentities:
    def test_every_reported_year_balances_exactly(self, result):
        worst = max(abs(s.balance_gap) for s in result.history.values())
        assert worst <= checks.BALANCE_TOLERANCE

    def test_every_forecast_year_balances_exactly(self, result):
        for scenario in result.scenarios.values():
            for state in scenario.states:
                assert abs(state.balance_gap) <= checks.BALANCE_TOLERANCE

    def test_the_forecast_cash_flow_reconciles(self, result):
        previous = result.last_actual
        for state in result.scenarios["base"].states:
            assert abs(state.change_in_cash - (state.cash - previous.cash)) <= 0.01
            previous = state

    def test_no_blocking_check_fails(self, result):
        assert result.summary["blocking"] == 0


class TestForecastAccuracy:
    def test_the_model_beats_the_naive_baseline_everywhere(self, result):
        # The claim the README leads with. If a future year breaks it, the
        # README is wrong and this is where it surfaces.
        for row in result.backtest.table():
            assert row["beats_naive"], f"{row['line']} at {row['horizon']}y"

    def test_one_year_revenue_error_stays_under_five_percent(self, result):
        assert result.backtest.mape("revenue", 1) < 0.05

    def test_net_income_is_biased_low(self, result):
        """The model's known weakness, asserted rather than buried.

        Averaging three years of margin misses Costco's steady margin
        expansion, so the forecast understates profit, and the understatement
        grows with the horizon.
        """
        bias_1 = result.backtest.bias("net_income", 1)
        bias_3 = result.backtest.bias("net_income", 3)
        assert bias_1 < 0
        assert bias_3 < bias_1

    def test_error_grows_with_horizon(self, result):
        for line in ("revenue", "net_income", "total_assets"):
            errors = [result.backtest.mape(line, h) for h in (1, 2, 3)]
            assert errors == sorted(errors), line


class TestSensitivity:
    def test_gross_margin_dominates_the_profit_forecast(self, result):
        assert result.tornado_net_income[0].driver == "gross_margin"

    def test_working_capital_moves_cash_but_not_profit(self, result):
        """Not a bug: inventory days change the balance sheet and the cash
        flow, never the income statement. Reporting both tornadoes is what
        keeps that legible."""
        by_driver = {r.driver: r for r in result.tornado_net_income}
        assert by_driver["dio_days"].swing == pytest.approx(0.0, abs=1.0)
        cash_by_driver = {r.driver: r for r in result.tornado_cash}
        assert cash_by_driver["dio_days"].swing > 0
