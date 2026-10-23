"""Command-line entry point.

    python -m fpa build      # fetch, model, check, report
    python -m fpa export     # CSV of statements, drivers, backtest and checks
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import fields

from .config import DOCS_DIR, EXPORT_DIR, REPORT_DIR, ensure_dirs
from .pipeline import report_context, run
from .report import render
from .statements import YearState


def cmd_build(args: argparse.Namespace) -> int:
    ensure_dirs()
    print("fetching SEC filings ...")
    result = run()
    cfg = result.config

    print(f"\n{cfg['company']['name']} ({cfg['company']['ticker']})")
    print(f"  reported years      {min(result.history)}-{max(result.history)} "
          f"({len(result.history)})")
    print(f"  drivers averaged    {result.driver_years}")
    if result.vintage_fixes:
        print(f"  vintage realignments {len(result.vintage_fixes)}")
        for fix in result.vintage_fixes:
            print(f"    {fix.describe()}")

    worst = max(abs(s.balance_gap) for s in result.history.values())
    worst_fc = max(abs(s.balance_gap) for s in result.scenarios["base"].states)
    print(f"  worst balance gap   reported {worst:,.2f} | forecast {worst_fc:,.2f}")

    print("\n  forecast accuracy (model vs naive baseline)")
    for row in result.backtest.table():
        flag = "beats" if row["beats_naive"] else "LOSES TO"
        print(f"    {row['line']:13s} {row['horizon']}y  "
              f"MAPE {row['mape']:6.2%}  naive {row['naive_mape']:6.2%}  "
              f"bias {row['bias']:+6.2%}  {flag} naive")

    print("\n  scenarios (terminal year)")
    for scenario in result.scenarios.values():
        terminal = scenario.terminal
        print(f"    {scenario.label:9s} revenue {terminal.revenue/1e9:7.1f}B  "
              f"net income {terminal.net_income/1e9:6.2f}B  "
              f"cash {terminal.cash/1e9:6.2f}B")

    summary = result.summary
    print(f"\nchecks: {summary['total']} run, {summary['passed']} passed, "
          f"{summary['failed']} failed ({summary['blocking']} blocking)")
    for check in result.checks:
        if not check.passed:
            print(f"  [{check.severity:7}] {check.check_id}")
            print(f"            expected {check.expected}; observed {check.observed}")

    path = render(report_context(result), REPORT_DIR / "model-report.html")
    print(f"\nreport: {path}")
    if args.publish_docs:
        published = DOCS_DIR / "index.html"
        published.parent.mkdir(parents=True, exist_ok=True)
        published.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"published: {published}")

    if summary["blocking"]:
        print(f"\nFAILED: {summary['blocking']} blocking check(s)", file=sys.stderr)
    return 1 if summary["blocking"] else 0


def cmd_export(args: argparse.Namespace) -> int:
    ensure_dirs()
    result = run()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    rows = [result.history[y] for y in sorted(result.history)]
    rows += list(result.scenarios["base"].states)
    _write(EXPORT_DIR / "statements.csv",
           [f.name for f in fields(YearState)] + ["gross_profit", "gross_margin",
                                                  "balance_gap"],
           [s.to_dict() for s in rows])

    _write(EXPORT_DIR / "drivers.csv",
           ["scenario"] + list(result.base_drivers.to_dict()),
           [{"scenario": s.label, **s.drivers.to_dict()}
            for s in result.scenarios.values()])

    _write(EXPORT_DIR / "backtest.csv",
           ["line", "horizon", "n", "mape", "naive_mape", "bias", "beats_naive"],
           result.backtest.table())

    _write(EXPORT_DIR / "checks.csv",
           ["check_id", "family", "severity", "passed", "expected", "observed"],
           [{"check_id": c.check_id, "family": c.family, "severity": c.severity,
             "passed": c.passed, "expected": c.expected, "observed": c.observed}
            for c in result.checks])

    print(f"wrote 4 files to {EXPORT_DIR}")
    return 0


def _write(path, header, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpa")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="fetch, model, check and report")
    build.add_argument("--publish-docs", action="store_true",
                       help="also write docs/index.html for GitHub Pages")
    build.set_defaults(func=cmd_build)

    export = sub.add_parser("export", help="write CSVs for Excel or Power BI")
    export.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
