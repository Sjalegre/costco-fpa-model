"""Render the model as a self-contained HTML report.

One file, no external assets, so it opens from disk or from GitHub Pages
without a build step. Every figure comes from the objects the run produced;
nothing is hard-coded.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ company }} - driver-based FP&amp;A model</title>
<style>
  :root {
    --bg:#fff; --fg:#16191d; --muted:#5c6672; --line:#e3e7ec; --zebra:#f7f9fb;
    --pass:#17795e; --pass-bg:#e6f4ef; --warn:#8a5a00; --warn-bg:#fdf3e0;
    --fail:#b02a1f; --fail-bg:#fdecea; --accent:#1f4e79; --bar:#4a86c5;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14171a; --fg:#e8ecf1; --muted:#9aa5b1; --line:#2a2f36;
      --zebra:#191d21; --pass:#4cc79f; --pass-bg:#16302a; --warn:#e0a33c;
      --warn-bg:#33280f; --fail:#f1786c; --fail-bg:#3a1d1a; --accent:#7fb2e5;
      --bar:#5d9bd8; }
  }
  *{box-sizing:border-box}
  body{margin:0;padding:2rem 1.25rem 4rem;background:var(--bg);color:var(--fg);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  .wrap{max-width:1100px;margin:0 auto}
  h1{font-size:1.6rem;margin:0 0 .25rem;letter-spacing:-.01em}
  h2{font-size:1.05rem;margin:2.5rem 0 .7rem}
  .sub{color:var(--muted);margin:0 0 1.8rem;font-size:.9rem}
  .cards{display:grid;gap:.75rem;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
  .card{border:1px solid var(--line);border-radius:8px;padding:.9rem 1rem}
  .card .n{font-size:1.55rem;font-weight:650;line-height:1.1}
  .card .l{color:var(--muted);font-size:.76rem;text-transform:uppercase;
    letter-spacing:.04em;margin-top:.2rem}
  .card.ok .n{color:var(--pass)} .card.bad .n{color:var(--fail)}
  .scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
  table{border-collapse:collapse;width:100%;font-size:.86rem}
  th,td{text-align:right;padding:.5rem .7rem;border-bottom:1px solid var(--line);
    white-space:nowrap}
  th:first-child,td:first-child{text-align:left}
  th{font-weight:600;color:var(--muted);font-size:.72rem;text-transform:uppercase;
    letter-spacing:.04em}
  tbody tr:nth-child(even){background:var(--zebra)}
  tbody tr:last-child td{border-bottom:none}
  tr.fc td{color:var(--accent);font-style:italic}
  code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em}
  .pill{display:inline-block;padding:.08rem .45rem;border-radius:4px;font-size:.72rem;
    font-weight:650}
  .pill.pass{color:var(--pass);background:var(--pass-bg)}
  .pill.error{color:var(--fail);background:var(--fail-bg)}
  .pill.warning{color:var(--warn);background:var(--warn-bg)}
  .pill.info{color:var(--muted);background:var(--line)}
  .note{border-left:3px solid var(--accent);padding:.55rem 0 .55rem .9rem;
    color:var(--muted);margin:1rem 0;font-size:.89rem}
  .bar{height:12px;background:var(--bar);border-radius:2px;display:inline-block;
    vertical-align:middle}
  footer{margin-top:3rem;color:var(--muted);font-size:.79rem;border-top:1px solid var(--line);
    padding-top:1rem}
</style>
</head>
<body><div class="wrap">
  <h1>{{ company }}</h1>
  <p class="sub">Driver-based three-statement model &middot; {{ first_year }}&ndash;{{ last_actual }} reported,
    {{ forecast_years }} years forecast &middot; generated {{ generated_at }}</p>

  <div class="cards">
    <div class="card"><div class="n">{{ history_years }}</div><div class="l">Years reported</div></div>
    <div class="card {{ 'ok' if summary.blocking == 0 else 'bad' }}">
      <div class="n">{{ summary.passed }}/{{ summary.total }}</div><div class="l">Checks passed</div></div>
    <div class="card {{ 'ok' if balance_exact else 'bad' }}">
      <div class="n">{{ worst_balance }}</div><div class="l">Worst balance gap</div></div>
    <div class="card"><div class="n">{{ revenue_mape }}</div><div class="l">Revenue error, 1yr</div></div>
    <div class="card"><div class="n">{{ naive_mape }}</div><div class="l">Naive baseline</div></div>
  </div>

  <div class="note">
    Nothing in the forecast is plugged. Cash is an output of the cash-flow
    statement and the balance sheet is assembled independently, so the identity
    check below is a real test of the model rather than a restatement of it.
    This is a modelling exercise, not investment advice.
  </div>

  <h2>Integrity checks</h2>
  <div class="scroll"><table>
    <thead><tr><th>Status</th><th>Check</th><th>Expected</th><th>Observed</th></tr></thead>
    <tbody>
    {% for c in checks %}
      <tr>
        <td>{% if c.passed %}<span class="pill pass">pass</span>
            {% else %}<span class="pill {{ c.severity }}">{{ c.severity }}</span>{% endif %}</td>
        <td><code>{{ c.check_id }}</code>
          {% if c.detail %}<div style="color:var(--muted)">{{ c.detail }}</div>{% endif %}</td>
        <td style="text-align:left">{{ c.expected }}</td>
        <td style="text-align:left">{{ c.observed }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table></div>

  <h2>Forecast accuracy, measured</h2>
  <p class="sub">Stand at year T, fit drivers on history up to T, forecast forward, score
    against what the company went on to report. {{ backtest_origins }} origin years.
    The baseline is &ldquo;next year looks exactly like this year&rdquo;.</p>
  <div class="scroll"><table>
    <thead><tr><th>Line</th><th>Horizon</th><th>n</th><th>Model MAPE</th>
      <th>Naive MAPE</th><th>Bias</th><th>Beats naive</th></tr></thead>
    <tbody>
    {% for r in backtest %}
      <tr><td>{{ r.line }}</td><td>{{ r.horizon }}y</td><td>{{ r.n }}</td>
        <td>{{ '%.2f'|format(r.mape*100) }}%</td>
        <td>{{ '%.2f'|format(r.naive_mape*100) }}%</td>
        <td>{{ '%+.2f'|format(r.bias*100) }}%</td>
        <td>{% if r.beats_naive %}<span class="pill pass">yes</span>
            {% else %}<span class="pill warning">no</span>{% endif %}</td></tr>
    {% endfor %}
    </tbody>
  </table></div>

  <h2>Drivers</h2>
  <div class="scroll"><table>
    <thead><tr><th>Driver</th>{% for s in scenarios %}<th>{{ s.label }}</th>{% endfor %}</tr></thead>
    <tbody>
    {% for name in driver_names %}
      <tr><td><code>{{ name }}</code></td>
        {% for s in scenarios %}<td>{{ '%.4f'|format(s.drivers[name]) }}</td>{% endfor %}</tr>
    {% endfor %}
    </tbody>
  </table></div>

  <h2>Statements</h2>
  <p class="sub">US$ millions. Italic rows are forecast.</p>
  <div class="scroll"><table>
    <thead><tr><th>Year</th><th>Revenue</th><th>Gross margin</th><th>Operating income</th>
      <th>Net income</th><th>Cash</th><th>Total assets</th><th>Equity</th>
      <th>CFO</th><th>Capex</th><th>Balance gap</th></tr></thead>
    <tbody>
    {% for s in statements %}
      <tr class="{{ 'fc' if s.is_forecast else '' }}">
        <td>{{ s.year }}</td>
        <td>{{ '{:,.0f}'.format(s.revenue/1e6) }}</td>
        <td>{{ '%.2f'|format(s.gross_margin*100) }}%</td>
        <td>{{ '{:,.0f}'.format(s.operating_income/1e6) }}</td>
        <td>{{ '{:,.0f}'.format(s.net_income/1e6) }}</td>
        <td>{{ '{:,.0f}'.format(s.cash/1e6) }}</td>
        <td>{{ '{:,.0f}'.format(s.total_assets/1e6) }}</td>
        <td>{{ '{:,.0f}'.format(s.equity/1e6) }}</td>
        <td>{{ '{:,.0f}'.format(s.cfo/1e6) }}</td>
        <td>{{ '{:,.0f}'.format(s.capex/1e6) }}</td>
        <td>{{ '%.2f'|format(s.balance_gap) }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table></div>

  {% for t in tornadoes %}
  <h2>What the forecast rests on &mdash; {{ t.label }}</h2>
  <div class="scroll"><table>
    <thead><tr><th>Driver</th><th>Shock</th><th>Down</th><th>Up</th><th>Swing</th><th></th></tr></thead>
    <tbody>
    {% for r in t.rows %}
      <tr><td><code>{{ r.driver }}</code></td>
        <td>&plusmn;{{ r.shock }}</td>
        <td>{{ '{:,.0f}'.format(r.low_value/1e6) }}</td>
        <td>{{ '{:,.0f}'.format(r.high_value/1e6) }}</td>
        <td>{{ '%.1f'|format(r.swing_pct*100) }}%</td>
        <td style="width:40%"><span class="bar"
          style="width:{{ (r.swing_pct / t.max_swing * 100) if t.max_swing else 0 }}%"></span></td></tr>
    {% endfor %}
    </tbody>
  </table></div>
  {% endfor %}

  <h2>Sources</h2>
  <div class="scroll"><table>
    <thead><tr><th>Source</th><th>Status</th><th>Bytes</th><th>SHA-256</th></tr></thead>
    <tbody>
    {% for s in sources %}
      <tr><td><code>{{ s.source }}</code></td><td>{{ s.http_status }}</td>
        <td>{{ '{:,}'.format(s.content_length) }}</td>
        <td><code style="color:var(--muted)">{{ s.sha256[:16] }}</code></td></tr>
    {% endfor %}
    </tbody>
  </table></div>

  <footer>Built from SEC EDGAR XBRL company facts. Every figure on this page is
  computed by the run that generated it. Not investment advice.</footer>
</div></body></html>
"""


def render(context: dict, output_path: Path) -> Path:
    html = Environment(autoescape=True).from_string(_TEMPLATE).render(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        **context,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
