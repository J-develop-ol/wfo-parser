"""
WFO Tools – FastAPI web app
Tab 1: WFO Tools (equity curve + PowerLanguage code — original behaviour)
Tab 2: Strategy Equity MA
"""

from __future__ import annotations

import io
import logging
import re
import secrets
from collections import defaultdict
from datetime import datetime
from typing import Optional

import matplotlib
matplotlib.use("Agg")

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from equity_tools import generate_equity_curve

CORS_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="WFO Tools", version="3.0.0")

LAST_RESULT: dict = {}
_downloads: dict[str, str] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def html_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

def fig_to_img_html(fig) -> str:
    import base64
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return (
        "<img alt='Equity curve' style='width:100%; max-height:50vh; height:auto; "
        "object-fit:contain; border-radius:12px; border:1px solid var(--line);' "
        f"src='data:image/png;base64,{data}' />"
    )

# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\s*(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})\s*$")

def _normalize_date_token(s: str) -> str:
    s = (s or "").strip().strip('"').strip("'").strip()
    s = re.sub(r"[,\;\)\]]+$", "", s)
    s = s.replace(".", "/").replace("-", "/")
    return s

def _infer_date_order(tokens: list[str]) -> str:
    a_vals, b_vals = [], []
    for t in tokens:
        t = _normalize_date_token(t)
        m = _DATE_RE.match(t)
        if not m:
            continue
        a, b, c = m.groups()
        try:
            a_i, b_i = int(a), int(b)
        except ValueError:
            continue
        a_vals.append(a_i)
        b_vals.append(b_i)
    if not a_vals:
        return "dmy"
    if sum(1 for x in a_vals if x >= 1900) >= max(1, len(a_vals) // 2):
        return "ymd"
    if any(x > 12 for x in a_vals):
        return "dmy"
    if any(x > 12 for x in b_vals):
        return "mdy"
    return "dmy"

def parse_date_series(series: pd.Series, date_format: str = "auto") -> tuple[pd.Series, str]:
    raw = series.astype(str).map(lambda x: x.split()[0])
    norm = raw.map(_normalize_date_token)
    resolved = date_format
    if date_format == "auto":
        resolved = _infer_date_order(norm.tolist())
    if resolved == "ymd":
        parsed = pd.to_datetime(norm, format="%Y/%m/%d", errors="raise")
    elif resolved == "mdy":
        parsed = pd.to_datetime(norm, format="%m/%d/%Y", errors="raise")
    else:
        parsed = pd.to_datetime(norm, format="%d/%m/%Y", errors="raise")
    return parsed, resolved

# ---------------------------------------------------------------------------
# CSV / DataFrame parsing
# ---------------------------------------------------------------------------

def parse_wfo_to_dataframe(csv_text: str) -> pd.DataFrame:
    text = (csv_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        raise ValueError("Input is empty.")
    header = lines[0]
    if "\t" in header:
        delim = "\t"
    elif ";" in header and "," not in header:
        delim = ";"
    else:
        delim = ","
    expected_cols = header.count(delim) + 1
    table_lines = [header]
    for ln in lines[1:]:
        if ln.count(delim) + 1 != expected_cols:
            break
        table_lines.append(ln)
    df = pd.read_csv(io.StringIO("\n".join(table_lines)), sep=delim)
    df.columns = df.columns.str.strip()
    if df.empty:
        raise ValueError("No valid table rows detected.")
    return df

# ---------------------------------------------------------------------------
# Tool logic
# ---------------------------------------------------------------------------

def tool1_make_equity_html(csv_text: str, date_format: str = "auto") -> str:
    df = parse_wfo_to_dataframe(csv_text)
    fig, date_fmt = generate_equity_curve(df, date_format=date_format)
    img_html = fig_to_img_html(fig)
    label = "Auto-detected date format" if date_format == "auto" else "Using selected date format"
    fmt_label = {"dmy": "DD/MM/YYYY", "mdy": "MM/DD/YYYY", "ymd": "YYYY/MM/DD"}.get(date_fmt, date_fmt)
    return f"<div class='status'>{label}: {fmt_label}</div>{img_html}"


def tool2_make_code(csv_text: str, date_format: str = "auto") -> str:
    df = parse_wfo_to_dataframe(csv_text)
    if df.empty:
        raise ValueError("No data rows found in the pasted table.")

    date_cols = [
        "Begin In-Sample Data Interval",
        "End In-Sample Data Interval",
        "Begin Out-of-Sample Data Interval",
        "End Out-of-Sample Data Interval",
    ]
    start_col = "Begin Out-of-Sample Data Interval"
    end_col   = "End Out-of-Sample Data Interval"

    missing = [c for c in date_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Could not find required date columns: {missing}")

    combined = pd.concat([df[c] for c in date_cols], ignore_index=True)
    if date_format == "auto":
        _, resolved = parse_date_series(combined, date_format="auto")
    else:
        resolved = date_format

    start_parsed, _ = parse_date_series(df[start_col], date_format=resolved)
    end_parsed,   _ = parse_date_series(df[end_col],   date_format=resolved)
    df = df.copy()
    df["_start_dt"] = start_parsed
    df["_end_dt"]   = end_parsed
    df = df.dropna(subset=["_start_dt", "_end_dt"])

    def to_pl_date(date_str: str) -> str:
        date_only = str(date_str).split()[0]
        day, month, year = date_only.split("/")
        yy = int(year) % 100
        prefix = "1" if int(year) >= 2000 else "0"
        return f"{prefix}{yy:02d}{int(month):02d}{int(day):02d}"

    excluded_exact  = {start_col, end_col, "Begin In-Sample Data Interval", "End In-Sample Data Interval"}
    excluded_starts = ("IS ", "OOS ")
    candidates = [c for c in df.columns if c not in excluded_exact and not c.startswith(excluded_starts)]

    def split_strategy_and_input(col_name: str):
        s = (col_name or "").strip().replace("\u00a0", " ")
        if not s or " " not in s:
            return "", s
        return s.rsplit(None, 1)

    groups = defaultdict(list)
    for col in candidates:
        if " " not in col:
            continue
        if pd.to_numeric(df[col], errors="coerce").notna().any():
            strategy_name, input_name = split_strategy_and_input(col)
            if strategy_name and input_name:
                groups[strategy_name].append(col)

    if not groups:
        raise ValueError("Could not find input parameter columns.")

    best_prefix, strategy_cols = max(groups.items(), key=lambda kv: len(kv[1]))
    clean_var_names = {col: split_strategy_and_input(col)[1] for col in strategy_cols}

    code_blocks = []
    all_inputs  = set(clean_var_names.values())
    prev_end_dt = None

    for _, row in df.iterrows():
        start_dt = row["_start_dt"]
        end_dt   = row["_end_dt"]
        if prev_end_dt is not None and start_dt <= prev_end_dt:
            start_dt = prev_end_dt + pd.Timedelta(days=1)
        start = to_pl_date(start_dt.strftime("%d/%m/%Y"))
        end   = to_pl_date(end_dt.strftime("%d/%m/%Y"))
        param_lines = [f"    {clean_var_names[col]} = {row[col]};" for col in strategy_cols]
        block = "if Date >= " + start + " and Date <= " + end + " then begin\n" + "\n".join(param_lines) + "\nend;"
        code_blocks.append(block)
        prev_end_dt = end_dt

    vars_block = "vars:\n    " + ", ".join(f"{n}(0)" for n in sorted(all_inputs)) + ";"
    return vars_block + "\n\n" + "\n\n".join(code_blocks)

# ---------------------------------------------------------------------------
# MA tool JS — plain string (not f-string) to avoid escaping issues
# ---------------------------------------------------------------------------

MA_JS = r"""
let chartInstance = null;

function parsePnL(raw) {
  const lines = raw.split('\n');
  const values = [], errors = [];
  lines.forEach((line, i) => {
    const trimmed = line.trim().replace(/,/g, '');
    if (trimmed === '') return;
    const num = parseFloat(trimmed);
    if (isNaN(num)) errors.push('Line ' + (i+1) + ': "' + line.trim() + '" is not a number');
    else values.push(num);
  });
  return { values, errors };
}

function buildEquityCurve(pnl) {
  let running = 0;
  return pnl.map(v => parseFloat((running += v).toFixed(2)));
}

function buildMA(curve, len) {
  return curve.map((_, i) => {
    if (i < len - 1) return null;
    const slice = curve.slice(i - len + 1, i + 1);
    return parseFloat((slice.reduce((a,b) => a+b, 0) / slice.length).toFixed(2));
  });
}

function formatNum(n) {
  const abs = Math.abs(n), sign = n >= 0 ? '+' : '';
  if (abs >= 1e6) return sign + (n/1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return sign + (n/1e3).toFixed(2) + 'K';
  return sign + n.toFixed(2);
}

function computeStats(pnl, curve) {
  const total = curve[curve.length-1];
  const trades = pnl.length;
  const winRate = trades > 0 ? ((pnl.filter(v=>v>0).length / trades)*100).toFixed(1) : '0.0';
  let maxDD = 0, peak = curve[0];
  curve.forEach(v => { if (v > peak) peak = v; if (peak - v > maxDD) maxDD = peak - v; });
  return { total, trades, winRate, maxDD };
}

function buildChart() {
  const raw = document.getElementById('pnl-input').value;
  const maLength = parseInt(document.getElementById('ma-length').value, 10);
  const errorEl = document.getElementById('error-msg');
  const section = document.getElementById('ma-chart-section');
  errorEl.style.display = 'none';

  if (!raw.trim()) {
    errorEl.textContent = 'Please paste some P&L data first.';
    errorEl.style.display = 'block';
    section.style.display = 'none';
    return;
  }

  const { values, errors } = parsePnL(raw);
  if (errors.length) {
    errorEl.textContent = 'Could not parse: ' + errors.slice(0,3).join(' | ') + (errors.length > 3 ? ' and ' + (errors.length-3) + ' more' : '');
    errorEl.style.display = 'block';
  }
  if (!values.length) {
    errorEl.textContent = 'No valid numbers found.';
    errorEl.style.display = 'block';
    section.style.display = 'none';
    return;
  }

  const effMA = Math.min(maLength, values.length);
  const curve = buildEquityCurve(values);
  const ma = buildMA(curve, effMA);
  const labels = curve.map((_, i) => i+1);
  const stats = computeStats(values, curve);

  renderStats(stats, effMA);
  section.style.display = 'flex';
  document.getElementById('ma-input-area').style.display = 'none';

  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }

  const ctx = document.getElementById('equity-chart').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 420);
  grad.addColorStop(0, 'rgba(45,107,255,0.15)');
  grad.addColorStop(1, 'rgba(45,107,255,0.0)');

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Equity curve', data: curve, borderColor: '#2d6bff', borderWidth: 2,
          backgroundColor: grad, fill: true, pointRadius: 0, pointHoverRadius: 4,
          pointHoverBackgroundColor: '#2d6bff', tension: 0.2 },
        { label: 'MA', data: ma, borderColor: '#f59e0b', borderWidth: 1.5,
          borderDash: [5,4], backgroundColor: 'transparent', fill: false,
          pointRadius: 0, pointHoverRadius: 4, tension: 0.3, spanGaps: false }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      onHover: (event, elements) => {
        if (!elements.length) return;
        const idx = elements[0].index;
        updateHoverCard(idx+1, curve[idx], ma[idx], effMA);
      },
      scales: {
        x: {
          grid: { color: 'rgba(0,0,0,0.05)' }, border: { color: '#e5e7eb' },
          ticks: { color: '#9ca3af', font: { size: 11, family: 'monospace' }, maxTicksLimit: 12, autoSkip: true },
          title: { display: true, text: 'Trade number', color: '#9ca3af', font: { size: 11, family: 'monospace' }, padding: { top: 8 } }
        },
        y: {
          grid: { color: 'rgba(0,0,0,0.05)' }, border: { color: '#e5e7eb' },
          ticks: {
            color: '#9ca3af', font: { size: 11, family: 'monospace' }, maxTicksLimit: 8,
            callback: function(v) { var s = v >= 0 ? '+' : ''; return Math.abs(v) >= 1000 ? s+(v/1000).toFixed(1)+'K' : s+v.toFixed(0); }
          },
          title: { display: true, text: 'Net profit / loss', color: '#9ca3af', font: { size: 11, family: 'monospace' }, padding: { bottom: 8 } }
        }
      },
      animation: { duration: 400, easing: 'easeOutQuart' }
    }
  });

  renderLegend(effMA);
  setTimeout(function() { renderSignalStrip(curve, ma); }, 50);
  document.getElementById('equity-chart').addEventListener('mouseleave', resetHoverCard);
}

function renderStats(stats, maLen) {
  var isPos = stats.total >= 0;
  document.getElementById('stats-row').innerHTML =
    '<div class="stat-card"><div class="stat-card__label">Net P&L</div>' +
    '<div class="stat-card__value ' + (isPos ? 'positive' : 'negative') + '">' + formatNum(stats.total) + '</div></div>' +
    '<div class="stat-card"><div class="stat-card__label">Trades</div>' +
    '<div class="stat-card__value">' + stats.trades.toLocaleString() + '</div></div>' +
    '<div class="stat-card"><div class="stat-card__label">Win rate</div>' +
    '<div class="stat-card__value">' + stats.winRate + '%</div></div>' +
    '<div class="stat-card"><div class="stat-card__label">Max drawdown</div>' +
    '<div class="stat-card__value negative">' + (stats.maxDD > 0 ? '-' : '') + stats.maxDD.toFixed(2) + '</div></div>' +
    '<div class="stat-card"><div class="stat-card__label">MA length</div>' +
    '<div class="stat-card__value">' + maLen + '</div></div>' +
    '<div class="stat-card stat-card--hover" id="hover-card">' +
    '<div class="stat-card__label">Hover trade</div>' +
    '<div class="stat-card__value stat-card__value--muted" id="hover-trade">\u2014</div>' +
    '<div class="hover-card__detail" id="hover-detail"></div></div>';
}

function updateHoverCard(n, eq, maV, maLen) {
  var t = document.getElementById('hover-trade');
  var d = document.getElementById('hover-detail');
  if (!t || !d) return;
  var hasSignal = maV !== null;
  var isAbove = hasSignal && eq >= maV;
  var dot = hasSignal ? (isAbove ? '\u{1F7E2}' : '\u{1F534}') : '\u26AA';
  t.textContent = dot + ' #' + n;
  t.className = 'stat-card__value';
  d.innerHTML = '<span>Eq: ' + (eq >= 0 ? '+' : '') + eq.toFixed(2) + '</span>' +
    '<span>MA(' + maLen + '): ' + (maV !== null ? (maV >= 0 ? '+' : '') + maV.toFixed(2) : '\u2014') + '</span>';
}

function resetHoverCard() {
  var t = document.getElementById('hover-trade');
  var d = document.getElementById('hover-detail');
  if (t) { t.textContent = '\u2014'; t.className = 'stat-card__value stat-card__value--muted'; }
  if (d) d.innerHTML = '';
}

function renderLegend(maLen) {
  var ex = document.getElementById('chart-legend');
  if (ex) ex.remove();
  var leg = document.createElement('div');
  leg.id = 'chart-legend'; leg.className = 'chart-legend';
  leg.innerHTML =
    '<span class="legend-item"><span class="legend-swatch" style="background:#2d6bff;"></span>Equity curve</span>' +
    '<span class="legend-item"><span class="legend-swatch legend-swatch--dashed"></span>MA (' + maLen + ')</span>';
  var wrap = document.querySelector('.chart-wrap');
  wrap.parentNode.insertBefore(leg, wrap);
}

function renderSignalStrip(curve, ma) {
  var ex = document.getElementById('signal-strip-wrap');
  if (ex) ex.remove();
  var wrap = document.querySelector('.chart-wrap');
  var container = document.createElement('div');
  container.id = 'signal-strip-wrap'; container.className = 'signal-strip-wrap';
  var label = document.createElement('span');
  label.className = 'signal-strip-label'; label.textContent = 'vs MA';
  container.appendChild(label);
  var canvas = document.createElement('canvas');
  canvas.id = 'signal-strip'; canvas.setAttribute('aria-hidden','true');
  container.appendChild(canvas);
  wrap.parentNode.insertBefore(container, wrap.nextSibling);
  var dpr = window.devicePixelRatio || 1, height = 10;
  var totalWidth = container.clientWidth - 48;
  canvas.style.width = totalWidth + 'px'; canvas.style.height = height + 'px';
  canvas.width = totalWidth * dpr; canvas.height = height * dpr;
  var ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
  var chartCanvas = document.getElementById('equity-chart');
  var scale = totalWidth / chartCanvas.offsetWidth;
  var xScale = chartInstance.scales.x;
  var n = curve.length;
  for (var i = 0; i < n; i++) {
    var px = xScale.getPixelForValue(i) * scale;
    var nextPx = i < n-1 ? xScale.getPixelForValue(i+1)*scale : px + (xScale.getPixelForValue(1)-xScale.getPixelForValue(0))*scale;
    ctx.fillStyle = ma[i] === null ? 'rgba(0,0,0,0.06)' : (curve[i] >= ma[i] ? '#16a34a' : '#dc2626');
    ctx.fillRect(px - (nextPx-px)/2, 0, Math.ceil(nextPx-px)+0.5, height);
  }
}

function clearMA() {
  document.getElementById('pnl-input').value = '';
  document.getElementById('error-msg').style.display = 'none';
  document.getElementById('ma-chart-section').style.display = 'none';
  document.getElementById('ma-input-area').style.display = 'block';
  ['signal-strip-wrap','chart-legend'].forEach(function(id) { var el = document.getElementById(id); if (el) el.remove(); });
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
  document.getElementById('pnl-input').focus();
}

document.addEventListener('DOMContentLoaded', function() {
  var ta = document.getElementById('pnl-input');
  if (ta) ta.addEventListener('keydown', function(e) { if ((e.ctrlKey||e.metaKey) && e.key==='Enter') buildChart(); });
});
"""

# ---------------------------------------------------------------------------
# Page renderer
# ---------------------------------------------------------------------------

def render_page(
    active_tool: str = "wfo",
    csv_text: str = "",
    date_format_val: str = "auto",
    status: str = "",
    equity_html: str = "",
    code_text: str = "",
    active_tab: str = "eq",
) -> HTMLResponse:

    status_block  = f"<div class='status'>{html_escape(status)}</div>" if status else ""
    equity_panel  = equity_html or "<div class='status'>No equity curve output yet.</div>"
    textarea_style = "display:none;" if (equity_html or code_text) else "min-height:110px;"

    if code_text:
        output_block = (
            "<div class='copyRow'><button id='copyBtn' type='button' class='smallBtn' onclick='copyCode()'>Copy code</button></div>"
            f"<pre id='code-output'>{html_escape(code_text)}</pre>"
        )
    else:
        output_block = equity_panel

    date_options = [
        ("auto", "Auto-detect (recommended)"),
        ("dmy",  "DD/MM/YYYY (day-first)"),
        ("mdy",  "MM/DD/YYYY (month-first)"),
        ("ymd",  "YYYY/MM/DD (year-first)"),
    ]
    date_opts_html = "".join(
        f"<option value='{v}'{' selected' if v == date_format_val else ''}>{l}</option>"
        for v, l in date_options
    )

    wfo_active = "active" if active_tool == "wfo" else ""
    ma_active  = "active" if active_tool == "ma"  else ""

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WFO Tools</title>
  <style>
    :root {{
      --bg:#f6f7fb; --card:#ffffff; --ink:#111827;
      --muted:#6b7280; --hint:#9ca3af; --line:#e5e7eb; --line2:#d1d5db;
      --accent:#2d6bff; --green:#16a34a; --red:#dc2626; --amber:#f59e0b;
      --font-ui: system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
      --font-mono: ui-monospace, Menlo, Consolas, monospace;
      --r-sm:8px; --r-md:12px; --r-lg:16px;
    }}
    *, *::before, *::after {{ box-sizing:border-box; margin:0; padding:0; }}
    html, body {{ background:var(--bg); color:var(--ink); font-family:var(--font-ui); font-size:14px; line-height:1.5; -webkit-font-smoothing:antialiased; }}
    .wrap {{ max-width:1100px; margin:0 auto; padding:24px 18px 48px; }}
    .page-title {{ font-size:22px; font-weight:900; margin-bottom:18px; }}
    .tab-bar {{ display:flex; gap:6px; border-bottom:2px solid var(--line); }}
    .tab-btn {{
      padding:9px 20px; border:1px solid transparent; border-bottom:none;
      border-radius:var(--r-sm) var(--r-sm) 0 0; background:transparent;
      color:var(--muted); font-family:var(--font-ui); font-size:13px; font-weight:800;
      cursor:pointer; position:relative; bottom:-2px; transition:color 0.15s, background 0.15s;
    }}
    .tab-btn:hover {{ color:var(--ink); background:var(--card); }}
    .tab-btn.active {{ background:var(--card); color:var(--accent); border-color:var(--line); border-bottom-color:var(--card); }}
    .panel {{ display:none; }}
    .panel.active {{ display:block; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:0 var(--r-lg) var(--r-lg) var(--r-lg); padding:20px; box-shadow:0 8px 30px rgba(0,0,0,.07); }}
    textarea {{ width:100%; min-height:60px; resize:vertical; padding:12px; border-radius:var(--r-md); border:1px solid var(--line); background:#fff; color:var(--ink); font-family:var(--font-mono); font-size:13px; box-sizing:border-box; }}
    .row {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:10px; }}
    .btn {{ border-radius:var(--r-md); padding:10px 14px; border:1px solid var(--line); cursor:pointer; font-weight:800; font-family:var(--font-ui); font-size:13px; transition:opacity 0.15s; }}
    .btnPrimary {{ background:var(--accent); color:white; border-color:var(--accent); }}
    .btnPrimary:hover {{ opacity:0.88; }}
    .btnGhost {{ background:transparent; color:var(--ink); }}
    .btnGhost:hover {{ background:var(--bg); }}
    .hint {{ color:var(--muted); font-size:13px; margin-top:8px; }}
    .status {{ margin-top:10px; padding:10px 12px; border-radius:var(--r-md); border:1px solid var(--line); background:#fff; color:var(--muted); font-size:13px; margin-bottom:16px; }}
    pre {{ white-space:pre-wrap; word-break:break-word; padding:12px; border-radius:var(--r-md); border:1px solid var(--line); background:#fff; color:var(--ink); font-family:var(--font-mono); font-size:13px; margin:0; }}
    .copyRow {{ display:flex; gap:10px; margin-top:10px; flex-wrap:wrap; margin-bottom:16px; }}
    .smallBtn {{ padding:8px 12px; border-radius:var(--r-md); border:1px solid var(--line); background:#fff; color:var(--ink); cursor:pointer; font-weight:800; }}
    select {{ padding:10px 12px; border-radius:var(--r-md); border:1px solid var(--line); background:#fff; font-family:var(--font-ui); font-weight:800; font-size:13px; }}
    .ma-controls {{ display:flex; flex-direction:column; gap:16px; }}
    .control-row {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; }}
    .label {{ font-size:13px; font-weight:800; color:var(--muted); }}
    .input-number {{ background:#fff; border:1px solid var(--line2); border-radius:var(--r-sm); color:var(--ink); font-family:var(--font-ui); font-size:14px; font-weight:800; padding:8px 12px; width:80px; text-align:center; }}
    .input-number:focus {{ outline:none; border-color:var(--accent); }}
    .error-msg {{ background:#fef2f2; border:1px solid #fecaca; border-radius:var(--r-sm); color:var(--red); font-size:13px; padding:10px 14px; margin-top:8px; }}
    .ma-chart-section {{ display:flex; flex-direction:row; gap:14px; align-items:flex-start; }}
    .stats-col {{ display:flex; flex-direction:column; gap:8px; flex-shrink:0; width:150px; }}
    .stat-card {{ background:var(--bg); border:1px solid var(--line); border-radius:var(--r-md); padding:10px 12px; min-height:70px; overflow:hidden; }}
    .stat-card--hover {{ border-style:dashed; }}
    .stat-card__label {{ font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted); margin-bottom:5px; }}
    .stat-card__value {{ font-family:var(--font-mono); font-size:17px; font-weight:500; }}
    .stat-card__value.positive {{ color:var(--green); }}
    .stat-card__value.negative {{ color:var(--red); }}
    .stat-card__value--muted {{ color:var(--hint); }}
    .hover-card__detail {{ display:flex; flex-direction:column; gap:2px; margin-top:4px; font-family:var(--font-mono); font-size:11px; color:var(--muted); }}
    .ma-chart-section {{ margin-top:16px; }}
    .chart-wrap {{ position:relative; width:100%; height:420px; }}
    .chart-legend {{ display:flex; gap:20px; font-size:12px; color:var(--muted); padding-bottom:2px; }}
    .legend-item {{ display:flex; align-items:center; gap:7px; }}
    .legend-swatch {{ width:18px; height:2px; border-radius:1px; flex-shrink:0; }}
    .legend-swatch--dashed {{ background:repeating-linear-gradient(90deg,#f59e0b 0px,#f59e0b 5px,transparent 5px,transparent 9px); }}
    .signal-strip-wrap {{ display:flex; align-items:center; gap:10px; margin-top:4px; }}
    .signal-strip-label {{ font-size:10px; font-weight:800; text-transform:uppercase; letter-spacing:0.07em; color:var(--hint); white-space:nowrap; width:28px; flex-shrink:0; text-align:right; }}
    #signal-strip {{ flex:1; border-radius:3px; display:block; }}
  </style>
</head>
<body>
<div class="wrap">
  <div class="tab-bar">
    <button class="tab-btn {wfo_active}" data-tab="wfo" onclick="switchTab('wfo')">&#9881;&#65039; WFO Tools</button>
    <button class="tab-btn {ma_active}"  data-tab="ma"  onclick="switchTab('ma')">&#12336;&#65039; Strategy Equity MA</button>
  </div>

  <!-- PANEL 1: WFO Tools — original layout preserved exactly -->
  <div id="panel-wfo" class="card panel {wfo_active}">
    <div style="color:var(--muted); margin-bottom:12px;">Paste your MultiCharts WFO CSV text, then choose an action.</div>
    <form method="post" action="/run">
      <textarea name="csv_text" placeholder="Paste WFO CSV text here..." style="{textarea_style}">{html_escape(csv_text)}</textarea>
      <div class="row" style="align-items:center; margin-top:10px;">
        <label for="date_format" class="hint" style="margin:0; font-weight:800;">Date format</label>
        <select name="date_format" id="date_format">{date_opts_html}</select>
        <span class="hint" style="margin:0;">If Auto-detect struggles with 01/02/2024, choose here.</span>
      </div>
      <div class="row">
        <button class="btn btnPrimary" type="submit" name="action" value="equity">Draw equity curve</button>
        <button class="btn btnGhost"   type="submit" name="action" value="code">Generate PowerLanguage code</button>
        <a href="/" style="text-decoration:none;"><button class="btn btnGhost" type="button">Clear / New CSV</button></a>
      </div>
      {status_block}
    </form>
    <div style="margin-top:12px;">{output_block}</div>
  </div>

  <!-- PANEL 2: Strategy Equity MA -->
  <div id="panel-ma" class="card panel {ma_active}">
    <div id="ma-input-area">
      <div style="color:var(--muted); margin-bottom:16px;">Paste trade P&amp;L values (one per line) to plot an equity curve with a moving average.</div>
      <div class="ma-controls">
        <div>
          <div class="label" style="margin-bottom:5px;">Paste P&amp;L data</div>
          <div class="hint" style="margin-bottom:8px; margin-top:0;">One value per line — positive or negative numbers.</div>
          <textarea id="pnl-input" placeholder="150.00&#10;-42.50&#10;320.00&#10;-88.00&#10;..." spellcheck="false" style="min-height:110px;"></textarea>
        </div>
        <div id="error-msg" class="error-msg" style="display:none;"></div>
      </div>
    </div>
    <div class="control-row" style="margin-top:10px;">
      <div style="display:flex; align-items:center; gap:10px;">
        <label class="label" for="ma-length">MA length</label>
        <input id="ma-length" type="number" class="input-number" value="30" min="2" max="500" step="1" />
      </div>
      <button class="btn btnPrimary" onclick="buildChart()">Plot chart</button>
      <button class="btn btnGhost"   onclick="clearMA()">Clear</button>
    </div>
    <div id="ma-chart-section" class="ma-chart-section" style="display:none;">
      <div class="stats-col" id="stats-row"></div>
      <div style="flex:1; min-width:0; display:flex; flex-direction:column; gap:8px;">
        <div class="chart-wrap">
          <canvas id="equity-chart" role="img" aria-label="Equity curve with moving average"></canvas>
        </div>
      </div>
    </div>
  </div>

</div>

<script>
function switchTab(name) {{
  ['wfo','ma'].forEach(function(t) {{
    document.getElementById('panel-' + t).classList.toggle('active', t === name);
  }});
  document.querySelectorAll('.tab-btn').forEach(function(btn) {{
    btn.classList.toggle('active', btn.getAttribute('data-tab') === name);
  }});
}}

async function copyCode() {{
  var el = document.getElementById('code-output');
  if (!el) return;
  await navigator.clipboard.writeText(el.innerText || '');
  var b = document.getElementById('copyBtn');
  if (b) {{ b.textContent = 'Copied \u2713'; setTimeout(function() {{ b.textContent = 'Copy code'; }}, 1400); }}
}}

(function() {{
  var KEY = 'wfo_date_format';
  var sel = document.getElementById('date_format');
  if (!sel) return;
  var saved = localStorage.getItem(KEY);
  if (saved) sel.value = saved;
  sel.addEventListener('change', function() {{ localStorage.setItem(KEY, sel.value); }});
  var form = sel.closest('form');
  if (form) form.addEventListener('submit', function() {{ localStorage.setItem(KEY, sel.value); }});
}})();

if (window.location.search.includes('t=')) {{
  window.history.replaceState({{}}, document.title, '/');
}}
</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>{MA_JS}</script>
</body>
</html>"""

    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(t: Optional[str] = None):
    if t and t in LAST_RESULT:
        return render_page(**LAST_RESULT[t])
    return render_page(status="Paste WFO CSV text above, then choose an action.")


@app.post("/run")
def run(
    csv_text: str = Form(""),
    action:   str = Form("equity"),
    date_format: str = Form("auto"),
):
    try:
        equity_html = ""
        code_text   = ""

        if action == "equity":
            equity_html = tool1_make_equity_html(csv_text, date_format)
            active_tab  = "eq"
            status      = "Equity curve generated."
        elif action == "code":
            code_text  = tool2_make_code(csv_text, date_format)
            active_tab = "code"
            status     = "PowerLanguage code generated."
        else:
            active_tab = "eq"
            status     = "Unknown action."

        payload = dict(
            active_tool="wfo",
            csv_text=csv_text,
            date_format_val=date_format,
            status=status,
            equity_html=equity_html,
            code_text=code_text,
            active_tab=active_tab,
        )

    except Exception as e:
        payload = dict(
            active_tool="wfo",
            csv_text=csv_text,
            date_format_val=date_format,
            status=f"Error: {str(e)}",
            equity_html="",
            code_text="",
            active_tab="eq",
        )

    token = secrets.token_urlsafe(16)
    LAST_RESULT[token] = payload
    return RedirectResponse(url=f"/?t={token}", status_code=303)


@app.get("/download/{uid}")
async def download(uid: str, name: str = "output.txt"):
    text = _downloads.pop(uid, None)
    if text is None:
        raise HTTPException(status_code=404, detail="File not found or expired.")
    return StreamingResponse(
        io.BytesIO(text.encode("utf-8")),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={name}", "Cache-Control": "no-store"},
    )


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/equity", response_class=HTMLResponse)
def legacy_equity():
    return RedirectResponse(url="/", status_code=301)

@app.get("/wfo", response_class=HTMLResponse)
def legacy_wfo():
    return RedirectResponse(url="/", status_code=301)
