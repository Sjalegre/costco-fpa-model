"""Backtest the forecast against what actually happened.

A model that has never been scored is a statement of opinion. This walks the
history forward: stand at year T, fit the drivers on everything up to and
including T, forecast T+1 to T+3, and compare each forecast against the figures
the company went on to report. The parameters never see the years they are
judged on.

The error is reported next to a deliberately stupid baseline -- next year looks
exactly like this year. A driver model that cannot beat "nothing changes" is
not adding information, and saying so is the point of measuring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import drivers as drv
from .statements import YearState

# Lines the backtest scores. Revenue is the model's primary output; net income
# is what compounds the errors in every margin driver; total assets is where a
# wrong working-capital assumption shows up.
SCORED = ["revenue", "net_income", "total_assets"]


@dataclass
class Prediction:
    origin: int
    horizon: int
    target_year: int
    line: str
    predicted: float
    actual: float
    naive: float

    @property
    def error_pct(self) -> float:
        return (self.predicted - self.actual) / self.actual if self.actual else 0.0

    @property
    def naive_error_pct(self) -> float:
        return (self.naive - self.actual) / self.actual if self.actual else 0.0


@dataclass
class BacktestResult:
    predictions: list[Prediction] = field(default_factory=list)
    origins: list[int] = field(default_factory=list)

    def mape(self, line: str, horizon: int | None = None) -> float:
        """Mean absolute percentage error of the model."""
        picked = self._pick(line, horizon)
        return (
            sum(abs(p.error_pct) for p in picked) / len(picked) if picked else float("nan")
        )

    def naive_mape(self, line: str, horizon: int | None = None) -> float:
        picked = self._pick(line, horizon)
        return (
            sum(abs(p.naive_error_pct) for p in picked) / len(picked)
            if picked else float("nan")
        )

    def bias(self, line: str, horizon: int | None = None) -> float:
        """Mean signed error. Tells optimism apart from imprecision."""
        picked = self._pick(line, horizon)
        return (
            sum(p.error_pct for p in picked) / len(picked) if picked else float("nan")
        )

    def beats_naive(self, line: str, horizon: int | None = None) -> bool:
        return self.mape(line, horizon) < self.naive_mape(line, horizon)

    def _pick(self, line: str, horizon: int | None) -> list[Prediction]:
        return [
            p for p in self.predictions
            if p.line == line and (horizon is None or p.horizon == horizon)
        ]

    def table(self) -> list[dict]:
        """One row per line and horizon, for the report."""
        rows = []
        horizons = sorted({p.horizon for p in self.predictions})
        for line in SCORED:
            for horizon in horizons:
                picked = self._pick(line, horizon)
                if not picked:
                    continue
                rows.append({
                    "line": line,
                    "horizon": horizon,
                    "n": len(picked),
                    "mape": self.mape(line, horizon),
                    "naive_mape": self.naive_mape(line, horizon),
                    "bias": self.bias(line, horizon),
                    "beats_naive": self.beats_naive(line, horizon),
                })
        return rows


def run(
    history: dict[int, YearState],
    *,
    lookback: int,
    horizons: list[int],
    min_history: int = 5,
) -> BacktestResult:
    """Walk every origin year that has enough history behind and ahead of it."""
    years = sorted(history)
    result = BacktestResult()
    max_horizon = max(horizons)

    for index, origin in enumerate(years):
        # Need `min_history` years behind to measure drivers, and at least one
        # year ahead to score against.
        if index + 1 < min_history:
            continue
        if origin + 1 > years[-1]:
            continue

        in_sample = {y: history[y] for y in years[: index + 1]}
        try:
            fitted, _ = drv.base_case(in_sample, lookback)
        except ValueError:
            continue

        projected = drv.project(history[origin], fitted, max_horizon)
        result.origins.append(origin)

        for state in projected:
            horizon = state.year - origin
            if horizon not in horizons or state.year not in history:
                continue
            actual = history[state.year]
            for line in SCORED:
                result.predictions.append(Prediction(
                    origin=origin,
                    horizon=horizon,
                    target_year=state.year,
                    line=line,
                    predicted=getattr(state, line),
                    actual=getattr(actual, line),
                    # The baseline: the last reported year, repeated.
                    naive=getattr(history[origin], line),
                ))
    return result
