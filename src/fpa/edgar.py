"""SEC EDGAR XBRL client.

Two things about this API decide whether the numbers that come out of it are
right, and both are easy to get wrong silently:

1. **The SEC refuses anonymous clients.** Without a `User-Agent` naming a real
   contact, every request returns 403. That is their published fair-access
   policy, not an obstacle to route around.

2. **`fy` and `fp` describe the filing, not the fact.** A 10-K repeats two
   prior years as comparatives, and every one of those facts carries the
   *filing's* fiscal year. Worse, a company's own annual figure is not always
   tagged `fp=FY`: Costco's FY2019 revenue is tagged `fp=Q4`. Filtering on
   `fp == "FY"` therefore drops a complete year and leaves a gap that looks
   like missing data rather than a bad query.

   Every figure here is matched on the fact's own `start`/`end` dates instead.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import requests

_RETRY_STATUS = {429, 500, 502, 503, 504}


class EdgarError(RuntimeError):
    """Raised when a filing cannot be retrieved."""


@dataclass(frozen=True)
class Fact:
    """One reported value, with the period it actually covers."""

    value: float
    start: date | None
    end: date
    form: str
    accession: str
    filed: date | None

    @property
    def fiscal_year(self) -> int:
        """The year the period ends in.

        For a company whose fiscal year ends in late August, the period ending
        2019-09-01 is fiscal 2019, and that is what this returns. It is derived
        from the fact's own dates, never from the filing's `fy` field.
        """
        return self.end.year

    @property
    def days(self) -> int | None:
        return (self.end - self.start).days if self.start else None


@dataclass
class Provenance:
    source: str
    url: str
    fetched_at: datetime
    http_status: int
    content_length: int
    sha256: str


@dataclass
class Client:
    user_agent: str
    base_url: str = "https://data.sec.gov/api/xbrl"
    timeout: int = 120
    max_retries: int = 3
    records: list[Provenance] = field(default_factory=list)
    _session: requests.Session = field(default_factory=requests.Session, repr=False)
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.user_agent or "@" not in self.user_agent:
            # Failing here is kinder than letting every request 403 with a
            # message about rate limits that does not say what is wrong.
            raise ValueError(
                "SEC requires a User-Agent identifying a real contact, "
                "e.g. 'Name name@example.com'"
            )
        self._session.headers.update({"User-Agent": self.user_agent})

    def company_facts(self, cik: int) -> dict:
        """Fetch and cache one company's full XBRL fact set."""
        if cik in self._cache:
            return self._cache[cik]
        url = f"{self.base_url}/companyfacts/CIK{cik:010d}.json"
        payload = self._get(f"companyfacts:{cik}", url)
        document = payload.json()
        self._cache[cik] = document
        return document

    def _get(self, source: str, url: str) -> requests.Response:
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last = exc
                if attempt == self.max_retries:
                    break
                time.sleep(2 ** attempt)
                continue
            if response.status_code in _RETRY_STATUS and attempt < self.max_retries:
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 403:
                raise EdgarError(
                    f"{url}: HTTP 403. The SEC rejected the User-Agent "
                    f"{self.user_agent!r}; it must name a real contact."
                )
            if response.status_code != 200:
                raise EdgarError(f"{url}: HTTP {response.status_code}")
            self.records.append(
                Provenance(
                    source=source,
                    url=url,
                    fetched_at=datetime.now(timezone.utc),
                    http_status=response.status_code,
                    content_length=len(response.content),
                    sha256=hashlib.sha256(response.content).hexdigest(),
                )
            )
            return response
        raise EdgarError(f"{url}: unreachable after {self.max_retries} attempts") from last


def extract_annual(
    facts: dict,
    tag: str,
    *,
    kind: str,
    form: str = "10-K",
    min_days: int = 330,
    max_days: int = 400,
    first_year: int = 2008,
) -> dict[int, Fact]:
    """Pull one tag's annual series, keyed by the year its period ends in.

    `kind` is "flow" for income-statement and cash-flow items, which cover a
    period, or "stock" for balance-sheet items, which are measured at an
    instant. The distinction is not cosmetic: a flow fact with no `start` is a
    point-in-time value that would be nonsense as a year's revenue, and a stock
    fact with a `start` is a movement rather than a balance.

    Where the same year appears in several filings -- the original 10-K and
    then as a comparative in later ones -- the latest filing wins, because a
    restated figure supersedes the one it restates.

    That rule is right for a single line and wrong for a set of them. See
    `extract_annual_versions` and `concepts.align_vintages`: taking the newest
    version of each line independently can pair a restated asset with an
    un-restated liability, and the balance sheet then fails to balance by
    exactly the restatement.
    """
    taxonomy = facts.get("facts", {}).get("us-gaap", {})
    if tag not in taxonomy:
        return {}

    chosen: dict[int, Fact] = {}
    for unit_facts in taxonomy[tag]["units"].values():
        for raw in unit_facts:
            if raw.get("form") != form:
                continue
            end = date.fromisoformat(raw["end"])
            start = date.fromisoformat(raw["start"]) if raw.get("start") else None

            if kind == "flow":
                if start is None:
                    continue
                if not min_days < (end - start).days < max_days:
                    continue
            elif start is not None:
                continue

            if end.year < first_year:
                continue

            fact = Fact(
                value=float(raw["val"]),
                start=start,
                end=end,
                form=raw["form"],
                accession=raw.get("accn", ""),
                filed=date.fromisoformat(raw["filed"]) if raw.get("filed") else None,
            )
            previous = chosen.get(end.year)
            if previous is None or _is_newer(fact, previous):
                chosen[end.year] = fact
    return chosen


def extract_annual_versions(
    facts: dict,
    tag: str,
    *,
    kind: str,
    form: str = "10-K",
    min_days: int = 330,
    max_days: int = 400,
    first_year: int = 2008,
) -> dict[int, list[Fact]]:
    """Every reported version of a tag's annual series, newest filing first.

    The same fiscal year is reported once in its own 10-K and again as a
    comparative in the two that follow. Usually all three agree; when they do
    not, the company restated, and which version to use is a decision that has
    to be made across lines at once rather than line by line.
    """
    taxonomy = facts.get("facts", {}).get("us-gaap", {})
    if tag not in taxonomy:
        return {}

    collected: dict[int, list[Fact]] = {}
    for unit_facts in taxonomy[tag]["units"].values():
        for raw in unit_facts:
            if raw.get("form") != form:
                continue
            end = date.fromisoformat(raw["end"])
            start = date.fromisoformat(raw["start"]) if raw.get("start") else None
            if kind == "flow":
                if start is None or not min_days < (end - start).days < max_days:
                    continue
            elif start is not None:
                continue
            if end.year < first_year:
                continue
            collected.setdefault(end.year, []).append(
                Fact(
                    value=float(raw["val"]),
                    start=start,
                    end=end,
                    form=raw["form"],
                    accession=raw.get("accn", ""),
                    filed=date.fromisoformat(raw["filed"]) if raw.get("filed") else None,
                )
            )

    for year, versions in collected.items():
        versions.sort(key=lambda f: (f.filed or date.min, f.accession), reverse=True)
    return collected


def _is_newer(candidate: Fact, incumbent: Fact) -> bool:
    if candidate.filed and incumbent.filed:
        return candidate.filed > incumbent.filed
    return candidate.accession > incumbent.accession
