"""Splice each financial line from the several XBRL tags that report it.

No line in a long filing history lives under one tag. Accounting standards
change, companies restate, and the SEC taxonomy is revised: Costco's revenue is
`Revenues` through fiscal 2025 and `RevenueFromContractWithCustomerExcludingAssessedTax`
from 2016, its cost of sales moves from `CostOfGoodsSold` to
`CostOfGoodsAndServicesSold` in 2016, and two years of its cash-flow statement
exist only under the `...ContinuingOperations` variant.

Stitching those together is unavoidable. Doing it *blindly* is the mistake: a
splice assumes the two tags mean the same thing, and that assumption is
testable wherever both tags report the same year. This module performs the
splice and returns the evidence for it, so a disagreement surfaces as a failed
check rather than as a step in a chart that nobody can explain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .edgar import Fact, extract_annual, extract_annual_versions


@dataclass
class SpliceEvidence:
    """What was found when two tags were compared on the years they share."""

    line: str
    preferred_tag: str
    fallback_tag: str
    overlap_years: list[int]
    disagreements: list[tuple[int, float, float]] = field(default_factory=list)

    @property
    def agrees(self) -> bool:
        return not self.disagreements

    @property
    def verified(self) -> bool:
        """Whether the splice rests on evidence rather than on assumption."""
        return bool(self.overlap_years) and self.agrees

    def describe(self) -> str:
        if not self.overlap_years:
            return (
                f"{self.preferred_tag} and {self.fallback_tag} never report the "
                f"same year; the splice is unverified"
            )
        if self.agrees:
            return (
                f"agree on all {len(self.overlap_years)} overlapping years "
                f"({min(self.overlap_years)}-{max(self.overlap_years)})"
            )
        worst = max(self.disagreements, key=lambda d: abs(d[1] - d[2]))
        return (
            f"disagree in {len(self.disagreements)} of {len(self.overlap_years)} "
            f"overlapping years; largest gap {abs(worst[1] - worst[2]):,.0f} in {worst[0]}"
        )


@dataclass
class Line:
    """One spliced financial line."""

    name: str
    kind: str
    values: dict[int, float]
    source_tag: dict[int, str]
    evidence: list[SpliceEvidence] = field(default_factory=list)
    missing_tags: list[str] = field(default_factory=list)
    # Every reported version of each year, newest filing first, so a later
    # pass can choose a consistent vintage across several lines at once.
    versions: dict[int, list[Fact]] = field(default_factory=dict)

    @property
    def years(self) -> list[int]:
        return sorted(self.values)

    @property
    def gaps(self) -> list[int]:
        if not self.values:
            return []
        span = range(min(self.values), max(self.values) + 1)
        return [y for y in span if y not in self.values]


def splice_line(
    facts: dict,
    name: str,
    spec: dict,
    edgar_cfg: dict,
    tolerance: float = 0.0,
) -> Line:
    """Build one line from its candidate tags, in order of preference.

    Tags are listed newest-preferred first. Each is extracted independently and
    then merged: the preferred tag wins every year it covers, and a later tag
    fills only the years the earlier ones left empty. Before filling, the two
    are compared on every year they share.
    """
    kind = spec["kind"]
    series: list[tuple[str, dict[int, Fact]]] = []
    missing: list[str] = []
    for tag in spec["tags"]:
        found = extract_annual(
            facts,
            tag,
            kind=kind,
            form=edgar_cfg.get("form", "10-K"),
            min_days=edgar_cfg.get("min_period_days", 330),
            max_days=edgar_cfg.get("max_period_days", 400),
            first_year=edgar_cfg.get("first_year", 2008),
        )
        if found:
            series.append((tag, found))
        else:
            missing.append(tag)

    values: dict[int, float] = {}
    source: dict[int, str] = {}
    evidence: list[SpliceEvidence] = []
    versions: dict[int, list[Fact]] = {}

    for index, (tag, found) in enumerate(series):
        if index > 0:
            previous_tag, previous = series[index - 1]
            shared = sorted(set(previous) & set(found))
            disagreements = [
                (year, previous[year].value, found[year].value)
                for year in shared
                if abs(previous[year].value - found[year].value) > tolerance
            ]
            evidence.append(
                SpliceEvidence(
                    line=name,
                    preferred_tag=previous_tag,
                    fallback_tag=tag,
                    overlap_years=shared,
                    disagreements=disagreements,
                )
            )
        all_versions = extract_annual_versions(
            facts,
            tag,
            kind=kind,
            form=edgar_cfg.get("form", "10-K"),
            min_days=edgar_cfg.get("min_period_days", 330),
            max_days=edgar_cfg.get("max_period_days", 400),
            first_year=edgar_cfg.get("first_year", 2008),
        )
        for year, fact in found.items():
            # First tag to claim a year keeps it; the list is preference-ordered.
            if year not in values:
                values[year] = fact.value
                source[year] = tag
                versions[year] = all_versions.get(year, [fact])

    return Line(
        name=name,
        kind=kind,
        values=values,
        source_tag=source,
        evidence=evidence,
        missing_tags=missing,
        versions=versions,
    )


def build_lines(facts: dict, config: dict) -> dict[str, Line]:
    """Splice every line declared in the configuration."""
    tolerance = config.get("splice", {}).get("tolerance_abs", 0.0)
    edgar_cfg = config["edgar"]
    return {
        name: splice_line(facts, name, spec, edgar_cfg, tolerance)
        for name, spec in config["lines"].items()
    }


@dataclass
class VintageFix:
    """One year whose figures were realigned to a single filing."""

    year: int
    accession: str
    changed: list[tuple[str, float, float]]  # (line, before, after)

    def describe(self) -> str:
        parts = ", ".join(
            f"{line} {before:,.0f} -> {after:,.0f}"
            for line, before, after in self.changed
        )
        return f"{self.year}: realigned to {self.accession} ({parts})"


def align_vintages(
    lines: dict[str, Line], group: list[str], years: list[int]
) -> list[VintageFix]:
    """Force one filing's version of every line in `group`, per year.

    Taking the newest version of each line independently is correct per line
    and wrong per statement. Costco restated its fiscal 2014 total assets in
    the 2016 filing -- 33,024m to 32,662m -- but never restated the liabilities
    or equity of that year. Pairing the restated asset with the un-restated
    claims makes the balance sheet miss by exactly the 362m reclassification,
    and the error looks like a modelling bug rather than a sourcing one.

    This picks, for each year, the most recent filing that reports *every* line
    in the group, and takes all of them from it. Where no single filing covers
    the group, the year is left as it was and reported, because silently
    half-fixing it would be worse than leaving it visible.

    Mutates `lines` and returns what it changed.
    """
    fixes: list[VintageFix] = []
    for year in years:
        by_accession: dict[str, dict[str, Fact]] = {}
        for name in group:
            line = lines.get(name)
            if line is None:
                continue
            for fact in line.versions.get(year, []):
                by_accession.setdefault(fact.accession, {})[name] = fact

        present = [name for name in group if name in lines and year in lines[name].values]
        complete = {
            accession: facts
            for accession, facts in by_accession.items()
            if all(name in facts for name in present)
        }
        if not complete:
            continue

        def recency(item):
            facts = item[1]
            filed = max((f.filed for f in facts.values() if f.filed), default=None)
            return (filed or date.min, item[0])

        accession, facts = max(complete.items(), key=recency)
        changed = []
        for name in present:
            before = lines[name].values[year]
            after = facts[name].value
            if before != after:
                lines[name].values[year] = after
                changed.append((name, before, after))
        if changed:
            fixes.append(VintageFix(year=year, accession=accession, changed=changed))
    return fixes


def common_years(lines: dict[str, Line], required: list[str]) -> list[int]:
    """Years for which every required line has a value.

    The model can only run where all of its inputs exist. Computing this
    explicitly, rather than letting a missing year propagate as a NaN, means
    the usable window is a stated fact instead of whatever survived the
    arithmetic.
    """
    sets = [set(lines[name].values) for name in required if name in lines]
    if not sets:
        return []
    return sorted(set.intersection(*sets))
