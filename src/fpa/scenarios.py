"""Scenarios and one-at-a-time sensitivity.

Scenarios answer "what if the world is different". Sensitivity answers the
question that usually matters more: of everything in this model, which
assumption is the forecast actually standing on?

Both shock the drivers, never the outputs. A scenario that edits revenue
directly is a wish; one that edits the growth driver propagates through cost of
sales, working capital, cash and the balance sheet the same way the base case
does, which is what makes the comparison meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass

from .drivers import Drivers, project
from .statements import YearState


@dataclass
class Scenario:
    key: str
    label: str
    description: str
    drivers: Drivers
    states: list[YearState]

    @property
    def terminal(self) -> YearState:
        return self.states[-1]


def build(
    last_actual: YearState,
    base: Drivers,
    config: dict,
    horizon: int,
) -> dict[str, Scenario]:
    """Run the base case and every configured variant."""
    out: dict[str, Scenario] = {}
    for key, spec in config["scenarios"].items():
        shocked = base.shocked(**spec.get("deltas", {}))
        out[key] = Scenario(
            key=key,
            label=spec.get("label", key),
            description=spec.get("description", ""),
            drivers=shocked,
            states=project(last_actual, shocked, horizon),
        )
    return out


@dataclass
class SensitivityRow:
    driver: str
    shock: float
    low_value: float
    high_value: float
    base_value: float

    @property
    def swing(self) -> float:
        """Total movement in the metric across the down and up shocks."""
        return abs(self.high_value - self.low_value)

    @property
    def swing_pct(self) -> float:
        return self.swing / abs(self.base_value) if self.base_value else 0.0


def tornado(
    last_actual: YearState,
    base: Drivers,
    config: dict,
    horizon: int,
    metric: str = "net_income",
) -> list[SensitivityRow]:
    """Shock each driver up and down on its own, holding the rest at base.

    One-at-a-time, deliberately. Shocking drivers jointly would measure the
    correlation structure as much as the model, and no such structure has been
    estimated here -- pretending otherwise would be the more sophisticated
    kind of wrong.
    """
    base_states = project(last_actual, base, horizon)
    base_value = getattr(base_states[-1], metric)

    rows: list[SensitivityRow] = []
    for driver, shock in config["sensitivity"]["shock"].items():
        down = project(last_actual, base.shocked(**{driver: -shock}), horizon)
        up = project(last_actual, base.shocked(**{driver: +shock}), horizon)
        rows.append(SensitivityRow(
            driver=driver,
            shock=shock,
            low_value=getattr(down[-1], metric),
            high_value=getattr(up[-1], metric),
            base_value=base_value,
        ))
    rows.sort(key=lambda r: r.swing, reverse=True)
    return rows
