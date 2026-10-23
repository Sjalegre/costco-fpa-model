"""Tests for XBRL extraction, tag splicing and vintage alignment.

Each case reproduces, in miniature, something the real filings actually do.
The comments say which.
"""

from __future__ import annotations

import pytest

from fpa import concepts
from fpa.edgar import extract_annual, extract_annual_versions

# The config spelling, as `splice_line` consumes it.
EDGAR = {"form": "10-K", "min_period_days": 330, "max_period_days": 400,
         "first_year": 2008}
# The keyword spelling, as the extraction functions take it directly.
EXTRACT = {"form": "10-K", "min_days": 330, "max_days": 400, "first_year": 2008}


def fact(val, start, end, accn, filed, fy=None, fp="FY"):
    out = {"val": val, "end": end, "form": "10-K", "accn": accn, "filed": filed,
           "fp": fp}
    if start:
        out["start"] = start
    if fy:
        out["fy"] = fy
    return out


def facts_doc(tags: dict) -> dict:
    return {"facts": {"us-gaap": {
        tag: {"units": {"USD": rows}} for tag, rows in tags.items()
    }}}


class TestExtraction:
    def test_annual_period_is_kept_and_quarterly_is_not(self):
        doc = facts_doc({"Revenues": [
            fact(100, "2019-09-02", "2020-08-30", "a-1", "2020-10-01"),
            fact(25, "2019-09-02", "2019-11-24", "a-1", "2020-10-01"),  # a quarter
        ]})
        got = extract_annual(doc, "Revenues", kind="flow", **EXTRACT)
        assert list(got) == [2020]
        assert got[2020].value == 100

    def test_the_fp_field_is_not_used_to_find_the_annual_figure(self):
        """The trap that cost a whole year.

        Costco tags its fiscal 2019 annual revenue with `fp="Q4"`, not
        `fp="FY"`. Anything that filters on `fp == "FY"` silently drops the
        year and leaves a gap that looks like missing data.
        """
        doc = facts_doc({"Revenues": [
            fact(152, "2018-09-03", "2019-09-01", "a-19", "2019-10-01",
                 fy=2019, fp="Q4"),
        ]})
        got = extract_annual(doc, "Revenues", kind="flow", **EXTRACT)
        assert got[2019].value == 152

    def test_the_year_comes_from_the_period_not_from_the_filing(self):
        """A 10-K repeats prior years as comparatives, all tagged with the
        filing's own fiscal year. Keying on `fy` would file them all under one
        year and lose every earlier one."""
        doc = facts_doc({"Revenues": [
            fact(100, "2019-09-02", "2020-08-30", "a-22", "2022-10-01", fy=2022),
            fact(110, "2020-08-31", "2021-08-29", "a-22", "2022-10-01", fy=2022),
        ]})
        got = extract_annual(doc, "Revenues", kind="flow", **EXTRACT)
        assert sorted(got) == [2020, 2021]

    def test_stock_and_flow_are_distinguished(self):
        doc = facts_doc({"Assets": [
            fact(500, None, "2020-08-30", "a-1", "2020-10-01"),
            fact(999, "2019-09-02", "2020-08-30", "a-1", "2020-10-01"),
        ]})
        stock = extract_annual(doc, "Assets", kind="stock", **EXTRACT)
        assert stock[2020].value == 500

    def test_the_latest_filing_wins_for_a_single_line(self):
        doc = facts_doc({"Assets": [
            fact(100, None, "2014-08-31", "a-14", "2014-10-15"),
            fact(97, None, "2014-08-31", "a-16", "2016-10-12"),   # restated
        ]})
        assert extract_annual(doc, "Assets", kind="stock", **EXTRACT)[2014].value == 97

    def test_all_versions_are_kept_newest_first(self):
        doc = facts_doc({"Assets": [
            fact(100, None, "2014-08-31", "a-14", "2014-10-15"),
            fact(97, None, "2014-08-31", "a-16", "2016-10-12"),
        ]})
        versions = extract_annual_versions(doc, "Assets", kind="stock", **EXTRACT)
        assert [v.value for v in versions[2014]] == [97, 100]


class TestSplice:
    def _doc(self, new_vals, old_vals):
        return facts_doc({
            "NewTag": [fact(v, f"{y-1}-09-02", f"{y}-08-30", "a", "2026-10-01")
                       for y, v in new_vals.items()],
            "OldTag": [fact(v, f"{y-1}-09-02", f"{y}-08-30", "b", "2026-10-01")
                       for y, v in old_vals.items()],
        })

    def test_the_preferred_tag_wins_and_the_fallback_fills_the_rest(self):
        doc = self._doc({2018: 50, 2019: 60}, {2016: 30, 2017: 40, 2018: 50})
        line = concepts.splice_line(
            doc, "revenue", {"kind": "flow", "tags": ["NewTag", "OldTag"]}, EDGAR
        )
        assert line.values == {2016: 30, 2017: 40, 2018: 50, 2019: 60}
        assert line.source_tag[2016] == "OldTag"
        assert line.source_tag[2019] == "NewTag"
        assert line.gaps == []

    def test_an_agreeing_overlap_is_recorded_as_evidence(self):
        doc = self._doc({2018: 50, 2019: 60}, {2017: 40, 2018: 50})
        line = concepts.splice_line(
            doc, "revenue", {"kind": "flow", "tags": ["NewTag", "OldTag"]}, EDGAR
        )
        assert line.evidence[0].verified
        assert line.evidence[0].overlap_years == [2018]

    def test_a_disagreeing_overlap_is_reported_not_swallowed(self):
        # Two tags that claim the same year and differ mean the splice is
        # joining two different things. Silently preferring one would hide it.
        doc = self._doc({2018: 55, 2019: 60}, {2017: 40, 2018: 50})
        line = concepts.splice_line(
            doc, "revenue", {"kind": "flow", "tags": ["NewTag", "OldTag"]}, EDGAR
        )
        assert not line.evidence[0].agrees
        assert line.evidence[0].disagreements == [(2018, 55.0, 50.0)]

    def test_no_overlap_means_the_splice_is_unverified(self):
        doc = self._doc({2019: 60}, {2017: 40})
        line = concepts.splice_line(
            doc, "revenue", {"kind": "flow", "tags": ["NewTag", "OldTag"]}, EDGAR
        )
        assert not line.evidence[0].verified
        assert "unverified" in line.evidence[0].describe()


class TestVintageAlignment:
    def _restated_doc(self):
        """Costco's fiscal 2014 in miniature.

        Total assets were restated in the 2016 filing (100 -> 97); liabilities
        and equity were never restated and exist only in the 2015 filing.
        Taking the newest version of each line pairs the restated asset with
        the un-restated claims, and the sheet misses by the restatement.
        """
        return facts_doc({
            "Assets": [
                fact(100, None, "2014-08-31", "a-15", "2015-10-14"),
                fact(97, None, "2014-08-31", "a-16", "2016-10-12"),
            ],
            "Liabilities": [fact(60, None, "2014-08-31", "a-15", "2015-10-14")],
            "StockholdersEquity": [fact(40, None, "2014-08-31", "a-15", "2015-10-14")],
        })

    def _lines(self, doc):
        spec = {"kind": "stock"}
        return {
            "total_assets": concepts.splice_line(
                doc, "total_assets", {**spec, "tags": ["Assets"]}, EDGAR),
            "total_liabilities": concepts.splice_line(
                doc, "total_liabilities", {**spec, "tags": ["Liabilities"]}, EDGAR),
            "equity": concepts.splice_line(
                doc, "equity", {**spec, "tags": ["StockholdersEquity"]}, EDGAR),
        }

    def test_mixing_vintages_breaks_the_identity(self):
        lines = self._lines(self._restated_doc())
        assets = lines["total_assets"].values[2014]
        claims = lines["total_liabilities"].values[2014] + lines["equity"].values[2014]
        assert assets - claims == pytest.approx(-3.0)

    def test_alignment_restores_the_identity(self):
        lines = self._lines(self._restated_doc())
        group = ["total_assets", "total_liabilities", "equity"]
        fixes = concepts.align_vintages(lines, group, [2014])

        assets = lines["total_assets"].values[2014]
        claims = lines["total_liabilities"].values[2014] + lines["equity"].values[2014]
        assert assets - claims == pytest.approx(0.0)
        assert len(fixes) == 1
        assert fixes[0].changed == [("total_assets", 97.0, 100.0)]

    def test_a_year_already_consistent_is_left_alone(self):
        doc = facts_doc({
            "Assets": [fact(100, None, "2015-08-30", "a-15", "2015-10-14")],
            "Liabilities": [fact(60, None, "2015-08-30", "a-15", "2015-10-14")],
            "StockholdersEquity": [fact(40, None, "2015-08-30", "a-15", "2015-10-14")],
        })
        lines = self._lines(doc)
        assert concepts.align_vintages(
            lines, ["total_assets", "total_liabilities", "equity"], [2015]
        ) == []
