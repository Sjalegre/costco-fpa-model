# Does the model know anything?

A driver-based three-statement financial model of Costco, built from SEC XBRL
filings, that measures its own forecast error instead of asserting its own
competence.

Most published FP&A models project 5% growth forever and are never compared
against what happened. This one is walked forward over thirteen origin years
and scored against the figures the company went on to report — next to a
deliberately stupid baseline, so "better than nothing" has to be earned rather
than assumed.

**→ [Open the live model report](https://sjalegre.github.io/costco-fpa-model/)**

This is a modelling exercise. **It is not investment advice** and contains no
view on whether to own the stock.

## What it found

Every number below is produced by the pipeline on each run and asserted by the
test suite.

**The model beats the naive baseline on all nine line-and-horizon
combinations.** One-year-ahead revenue error is **3.96%** against the
baseline's 7.76%; at three years it is 12.71% against 21.74%.

**And it is biased low on profit, by a growing amount: −8.95% at one year,
−13.99% at three.** Averaging three years of margin misses Costco's steady
margin expansion. That is a real weakness of the method, it is in the report,
and a test asserts it stays true rather than quietly improving away.

**Filtering on the SEC's `fp` field silently deletes a year.** Costco tags its
fiscal 2019 annual revenue as `fp="Q4"`, not `fp="FY"`. Any extraction that
trusts that field drops fiscal 2019 entirely and leaves a gap that looks like
missing data. Matching on each fact's own period dates instead gives **19
continuous years with no gaps.**

**Taking the newest version of every line independently breaks the balance
sheet.** Costco restated fiscal 2014 total assets in its 2016 filing
(33,024m → 32,662m) and never restated that year's liabilities or equity.
Pairing the restated asset with the un-restated claims makes the sheet miss by
exactly the **$362m** reclassification — and it looks like a modelling bug, not
a sourcing one. The fix is to take every balance-sheet line for a year from a
single filing.

**The accounting identity is not `Assets = Liabilities + Equity`.** It misses by
**$514m** in fiscal 2021 and **$5m** in 2022, falling to zero from 2023.
`StockholdersEquity` excludes minority interest; Costco bought out the
non-controlling stake in its Taiwanese subsidiary in between. The identity the
model enforces includes it.

**Four financial lines live under more than one XBRL tag,** and each splice is
verified on the years where both tags report:

| Line | Tags spliced | Overlap | Result |
| --- | --- | --- | --- |
| Revenue | `Revenues` ← `RevenueFromContractWithCustomerExcludingAssessedTax` | 10 years | agree exactly |
| Cost of sales | `CostOfGoodsSold` ← `CostOfGoodsAndServicesSold` | 2 years | agree exactly |
| Cash flow (×3) | headline ← `...ContinuingOperations` | 4 years | agree exactly |

## Nothing is plugged

This is what makes the checks real rather than decorative.

Cash is an **output** of the cash-flow statement. The balance sheet is
assembled independently from the drivers. The two are never reconciled by
forcing a difference into a line — so if the cash-flow logic and the
balance-sheet logic disagree by a cent, the sheet stops balancing and the run
fails.

Worst balance gap across 18 reported and 3 forecast years: **0.00**.

## How it works

```
SEC EDGAR XBRL companyfacts        19 years of 10-K facts, all versions
            │
     extract by period dates       never by the filing's fy/fp fields
            │
     splice tags  ──────────────►  verified on overlap years
            │
     align vintages ────────────►  one filing per year, per statement
            │
   historical statements           other assets / other liabilities derived
            │                      so the identity holds exactly on history
     measure drivers               growth, margins, DSO/DIO/DPO, capex,
            │                      depreciation, tax, payout
            ├──────────────► forecast ──► scenarios ──► tornado
            └──────────────► walk-forward backtest vs naive baseline
```

Drivers are **measured, never chosen**: the base case is the plain mean of the
last three years. A weighted or trend-fitted average would encode a view, and
the backtest would then be scoring that view rather than the method.

## Quick start

```bash
pip install -r requirements.txt
python -m fpa build
```

Fetches the filings live, builds the model, runs 38 checks and writes
`data/reports/model-report.html`. Takes about ten seconds.

```bash
python -m fpa build --publish-docs   # also write docs/index.html for Pages
python -m fpa export                 # CSVs for Excel or Power BI
pytest                               # 28 offline tests
FPA_LIVE=1 pytest tests/test_live.py # 14 more, against the real filings
```

`build` exits non-zero if any check fails at `error` severity.

The SEC **requires a `User-Agent` naming a real contact** and returns 403
without one. It is set in `config/model.yml`; change it to your own before
running.

## The checks

38 checks in three families, chosen so a failure says *what kind* of thing went
wrong.

| Family | What a failure means | Examples |
| --- | --- | --- |
| **Sourcing** | the inputs are not what the filings say | a splice disagrees on an overlap year; a required line has an interior gap; a year was realigned across filings |
| **Identity** | the statements do not reconcile | assets ≠ liabilities + equity + minority interest; CFO+CFI+CFF ≠ the movement in cash |
| **Plausibility** | the output describes an impossible business | gross margin outside [0,1]; negative revenue; a tax rate above 60% |

Severity decides what happens: **error** fails the run, **warning** is recorded
and continues, **info** is a measurement with no pass/fail meaning. The current
run passes 37 of 38; the one finding is the 2014 vintage realignment, reported
at info level because it is a fact about the filings, not a defect in the model.

Negative cash is deliberately a **warning**, not an error. The model has no
revolver, so a plan that runs out of money is information about the plan — not
evidence that the arithmetic is broken.

## What the forecast rests on

Shocking each driver on its own, measured on terminal-year net income:

| Driver | Swing | |
| --- | --- | --- |
| `gross_margin` ±0.01 | **53.6%** | ████████████ |
| `sganda_ratio` ±0.005 | 26.8% | ██████ |
| `revenue_growth` ±0.02 | 11.2% | ██ |
| `dio_days`, `dpo_days`, `capex_intensity` | 0.0% | |

The zeros are correct, not broken: working capital and capex move cash and the
balance sheet without touching the income statement. That is why the report
also runs the tornado on terminal cash, where they are exactly the drivers that
matter. One tornado alone would mislead in either direction.

Costco earns 12.8 cents of gross profit per dollar of revenue. At that margin,
a single point of gross margin is worth more than two points of growth — which
the tornado says plainly and a growth-focused narrative would miss.

## Limitations

Stated plainly, because a model's boundaries are part of its output.

* **Consolidated figures only.** `companyfacts` returns no segment or product
  breakdown, so membership fees — Costco's most predictable revenue line and
  the obvious driver to model — are **not available** through this API. The
  model runs on consolidated drivers instead. Getting membership revenue would
  mean parsing the statement R-files or the 10-K text.
* **Reported operating income is not reproduced.** The model computes it as
  revenue − cost of sales − SG&A. The filings' figure includes items the model
  does not carry, so the two differ slightly. Net income is modelled from the
  computed figure, which is one source of the low bias.
* **Debt is held flat and there is no revolver.** A funding shortfall shows as
  negative cash rather than as borrowing.
* **The backtest has 13 origin years, not 130.** Thirteen overlapping
  three-year windows from one company is thin evidence. It is enough to say
  the method beats a naive baseline here; it is not enough to say it would
  anywhere else.
* **One company, one sector.** Nothing here generalises to a business with
  different working-capital dynamics.
* **The driver window was not itself validated.** Three years is a reasonable
  default, not an optimised one — and optimising it against the backtest would
  turn the out-of-sample measurement into an in-sample one.
* **Restatements are handled, not resolved.** The model takes the newest
  internally consistent filing for each year. Where a company restates its
  statements piecemeal across several filings, no choice is fully satisfying.

## Layout

```
config/model.yml      company, tags, splices, drivers, scenarios — no URLs in code
src/fpa/
  edgar.py            SEC client; extraction by period, with provenance
  concepts.py         tag splicing, overlap verification, vintage alignment
  statements.py       historical statements; derived other assets/liabilities
  drivers.py          driver measurement and the forecast engine
  scenarios.py        scenarios and one-at-a-time sensitivity
  backtest.py         walk-forward scoring against the naive baseline
  checks.py           integrity checks
  report.py           self-contained HTML report
  pipeline.py         the whole run, as one object
tests/                42 tests; 28 need no network
docs/index.html       the generated report, served by GitHub Pages
```

## Tests

```bash
pytest                                # 28, fully offline
FPA_LIVE=1 pytest tests/test_live.py  # 14 more, against the live filings
```

Three are worth singling out:

* `test_every_forecast_year_balances` — the load-bearing one. Cash comes from
  the cash-flow statement and the balance sheet is built separately; if they
  disagree by a cent, this fails.
* `test_mixing_vintages_breaks_the_identity` / `test_alignment_restores_the_identity`
  — reproduce the 2014 restatement in miniature and prove the fix works.
* `test_the_fp_field_is_not_used_to_find_the_annual_figure` — a regression
  guard on the bug that deleted a year.

---

Built by Sergio Alegre. Data from
[SEC EDGAR](https://www.sec.gov/edgar/sec-api-documentation). Not investment advice.
