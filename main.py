"""
WFO Parser – FastAPI web app
---------------------------------
This app lets users upload a MultiCharts WFO CSV OR paste the CSV text and see the generated PowerLanguage
code directly on the page, with an option to also download the .txt file.

HOW TO RUN LOCALLY
1) Create & activate a virtual env (recommended)
   python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
2) Install deps
   pip install fastapi uvicorn[standard] python-multipart pandas
3) Start the server
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
4) Open http://127.0.0.1:8000 in your browser
"""

from __future__ import annotations

import io
import logging
import secrets
from datetime import datetime
from typing import Dict, Optional

import matplotlib
matplotlib.use("Agg")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from equity_tools import generate_equity_curve

# ====== SECURITY: set your production domain(s) here ======
CORS_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    # "https://yourdomain.com",
    # "https://app.yourdomain.com",
]
# ==========================================================

# Step 3 – logging
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="WFO Parser", version="1.2.0")
LAST_RESULT = {}  # token -> dict(payload)
@app.get("/health")
def health():
    return {"status": "ok"}

# ----------------------------
# Combined UI (one page + tabs)
# ----------------------------

def html_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

def fig_to_img_html(fig) -> str:
    import io
    import base64

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"<img alt='Equity curve' style='width:100%; max-height:50vh; height:auto; object-fit:contain; border-radius:12px; border:1px solid var(--line);' src='data:image/png;base64,{data}' />"

def render_combined_page(
    csv_text: str = "",
    active_tab: str = "eq",        # "eq" or "code"
    status: str = "",
    equity_html: str = "",
    code_text: str = "",
    
) -> HTMLResponse:

    status_block = ""
    if status:
        status_block = f"<div class='status'>{html_escape(status)}</div>"

    # Panels (we'll hide/show with tabs)
    equity_panel = equity_html or "<div class='status'>No equity curve output yet.</div>"
    code_panel = f"<pre id='code-output'>{html_escape(code_text)}</pre>" if code_text else "<div class='status'>No code output yet.</div>"

    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WFO Tools</title>
  <style>
    :root {{ --bg:#f6f7fb; --card:#ffffff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; }}
    body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:var(--bg); color:var(--ink); }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 18px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px; box-shadow: 0 8px 30px rgba(0,0,0,.25); }}
    textarea {{ width:100%; min-height: 60px; resize: vertical; padding: 12px; border-radius: 12px;
               border:1px solid var(--line); background: #ffffff; color:var(--ink); font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; box-sizing: border-box; }}
    .row {{ display:flex; gap:10px; flex-wrap: wrap; align-items: center; margin-top: 10px; }}
    .btn {{ border-radius: 12px; padding: 10px 14px; border: 1px solid var(--line); cursor:pointer; font-weight: 800; }}
    .btnPrimary {{ background: #2d6bff; color: white; border-color: #2d6bff; }}
    .btnGhost {{ background: transparent; color: var(--ink); }}
    .hint {{ color:var(--muted); font-size: 13px; margin-top: 8px; }}
    .status {{ margin-top: 10px; padding: 10px 12px; border-radius: 12px; border:1px solid var(--line); background:#ffffff; color: var(--muted); font-size: 13px; margin-bottom: 16px; }}
    .tabs {{ display:flex; gap:10px; margin-top: 14px; }}
    .tab {{ padding: 8px 12px; border-radius: 999px; border:1px solid var(--line); background:#ffffff; color:var(--muted); cursor:pointer; font-weight:800; font-size:13px; user-select:none; }}
    .tab.active {{ background:#1a2440; color: var(--ink); border-color:#2a3a62; }}
    .panel {{ margin-top: 12px; }}
    pre {{ white-space: pre-wrap; word-break: break-word; padding: 12px; border-radius: 12px;
          border:1px solid var(--line); background: #ffffff; color: var(--ink);
          font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; margin:0; }}
    .copyRow {{ display:flex; gap:10px; margin-top:10px; flex-wrap:wrap; margin-bottom: 16px; }}
    .smallBtn {{ padding: 8px 12px; border-radius: 12px; border:1px solid var(--line); background:#ffffff; color: var(--ink); cursor:pointer; font-weight:800; }}
    a {{ color:#8fb0ff; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div style="display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;">
        <div>
          <div style="font-size:20px; font-weight:900; margin-bottom:4px;">WFO Tools</div>
          <div style="color:var(--muted);">Paste your MultiCharts WFO CSV text, then choose an action.</div>
        </div>
      </div>

      <form method="post" action="/run" style="margin-top:14px;">
        {
    f"<textarea name='csv_text' placeholder='Paste WFO CSV text here...' style='display:none;'>{html_escape(csv_text)}</textarea>"
    if (equity_html or code_text)
    else f"<textarea name='csv_text' placeholder='Paste WFO CSV text here...' style='min-height:110px;'>{html_escape(csv_text)}</textarea>"
}
        <div class="row" style="align-items:center;">
          <label for="date_format" class="hint" style="margin:0; font-weight:800;">
            Date format
          </label>
          <select name="date_format" id="date_format"
                  style="padding:10px 12px; border-radius:12px; border:1px solid var(--line); background:#fff; font-weight:800;">
            <option value="auto" selected>Auto-detect (recommended)</option>
            <option value="dmy">DD/MM/YYYY (day-first)</option>
            <option value="mdy">MM/DD/YYYY (month-first)</option>
            <option value="ymd">YYYY/MM/DD (year-first)</option>
          </select>
          <span class="hint" style="margin:0;">
            If Auto-detect struggles with 01/02/2024, choose here.
          </span>
        </div>

        <div class="row">
  <button class="btn btnPrimary" type="submit" name="action" value="equity">Draw equity curve</button>
  <button class="btn btnGhost" type="submit" name="action" value="code">Generate PowerLanguage code</button>

  <a href="/" style="text-decoration:none;">
    <button class="btn btnGhost" type="button">Clear / New CSV</button>
  </a>
</div>

        {status_block}
      </form>

<div class="panel">
  {
    (
      "<div class='copyRow'><button id='copyBtn' type='button' class='smallBtn' onclick='copyCode()'>Copy code</button></div>"
      + code_panel
    )
    if active_tab == "code"
    else equity_panel
  }
</div>

</div>
</div>

<script>
  async function copyCode(){{
    const el = document.getElementById('code-output');
    if(!el) return;
    const text = el.innerText || "";
    await navigator.clipboard.writeText(text);
    const b = document.getElementById('copyBtn');
    if(b){{
      b.textContent = "Copied ✓";
      setTimeout(()=>b.textContent="Copy code", 1200);
    }}
  }}

  // Remove ?t=... from URL after redirect (clean address bar)
  if (window.location.search.includes("t=")) {{
    window.history.replaceState({{}}, document.title, "/");
  }}

    (function () {{
    const KEY = "wfo_date_format";
    const select = document.getElementById("date_format");
    if (!select) return;

    // Restore last selection
    const saved = localStorage.getItem(KEY);
    if (saved) select.value = saved;

    // Save on change
    select.addEventListener("change", () => {{
      localStorage.setItem(KEY, select.value);
    }});

    // Also save on submit
    const form = select.closest("form");
    if (form) {{
      form.addEventListener("submit", () => {{
        localStorage.setItem(KEY, select.value);
      }});
    }}
  }})();

</script>

</body>
</html>
"""
    return HTMLResponse(html)

@app.get("/", response_class=HTMLResponse)
def combined_home(t: Optional[str] = None):
    if t and t in LAST_RESULT:
        payload = LAST_RESULT[t]   # <-- IMPORTANT: no pop()
        return render_combined_page(**payload)

    return render_combined_page(
        csv_text="",
        active_tab="eq",
        status="Paste WFO CSV text above, then choose an action.",
        equity_html="",
        code_text="",
    )

# ---- New shared CSV parser ----
def parse_wfo_to_dataframe(csv_text: str):
    import pandas as pd
    import io

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

import re
import pandas as pd

_DATE_RE = re.compile(r"^\s*(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})\s*$")

def _normalize_date_token(s: str) -> str:
    """Trim, remove obvious junk, and normalize separators to '/'."""
    s = (s or "").strip().strip('"').strip("'").strip()
    # remove trailing commas/semicolons etc.
    s = re.sub(r"[,\;\)\]]+$", "", s)
    # normalize separators
    s = s.replace(".", "/").replace("-", "/")
    return s

def _infer_date_order(tokens: list[str]) -> str:
    """
    Infer 'dmy', 'mdy', or 'ymd' from many date tokens.
    Returns 'dmy' by default if ambiguous.
    """
    a_vals, b_vals, c_vals = [], [], []

    for t in tokens:
        t = _normalize_date_token(t)
        m = _DATE_RE.match(t)
        if not m:
            continue
        a, b, c = m.groups()
        try:
            a_i, b_i, c_i = int(a), int(b), int(c)
        except ValueError:
            continue
        a_vals.append(a_i); b_vals.append(b_i); c_vals.append(c_i)

    if not a_vals:
        return "dmy"  # fallback

    # Year-first: first component looks like a year a lot
    # (simple heuristic: 4 digits and >= 1900)
    year_first_hits = sum(1 for x in a_vals if x >= 1900)
    if year_first_hits >= max(1, len(a_vals) // 2):
        return "ymd"

    # Disambiguate dmy vs mdy using >12 evidence
    if any(x > 12 for x in a_vals):
        return "dmy"
    if any(x > 12 for x in b_vals):
        return "mdy"

    # Fully ambiguous (all <=12) → default dmy
    return "dmy"

def parse_date_series(series: pd.Series, date_format: str = "auto") -> tuple[pd.Series, str]:
    """
    Parse a pandas Series of date strings into datetime64[ns].
    Returns (parsed_series, resolved_format).
    """
    raw = series.astype(str).map(lambda x: x.split()[0])  # keep first token if time ever appears
    norm = raw.map(_normalize_date_token)

    resolved = date_format
    if date_format == "auto":
        resolved = _infer_date_order(norm.tolist())

    if resolved == "ymd":
        parsed = pd.to_datetime(norm, format="%Y/%m/%d", errors="raise")
    elif resolved == "mdy":
        parsed = pd.to_datetime(norm, format="%m/%d/%Y", errors="raise")
    else:  # "dmy"
        parsed = pd.to_datetime(norm, format="%d/%m/%Y", errors="raise")

    return parsed, resolved

# ---- IMPORTANT: plug your existing logic into these two functions ----

def tool1_make_equity_html_from_csv(csv_text: str, date_format: str = "auto") -> str:
    df = parse_wfo_to_dataframe(csv_text)

    fig, date_fmt = generate_equity_curve(df, date_format=date_format)
    img_html = fig_to_img_html(fig)

    label = "Auto-detected date format" if date_format == "auto" else "Using selected date format"
    return f"<div class='status'>{label}: {date_fmt}</div>{img_html}"


def tool2_make_code_from_csv(csv_text: str, date_format: str = "auto") -> str:
    return parse_wfo_csv_text(csv_text, date_format=date_format)


@app.post("/run")
def run(
    csv_text: str = Form(""),
    action: str = Form("equity"),
    date_format: str = Form("auto"),
):

    try:
        # Decide what to run based on button
        equity_html = ""
        code_text = ""

        if action == "equity":
            equity_html = tool1_make_equity_html_from_csv(csv_text, date_format)
            active_tab = "eq"
            status = "Equity curve generated."
        elif action == "code":
            code_text = tool2_make_code_from_csv(csv_text, date_format)
            active_tab = "code"
            status = f"PowerLanguage code generated. Date format: {date_format}"
        else:
            active_tab = "eq"
            status = "Unknown action."

        payload = dict(
            csv_text=csv_text,
            active_tab=active_tab,
            status=status,
            equity_html=equity_html,
            code_text=code_text,
        )

        token = secrets.token_urlsafe(16)
        LAST_RESULT[token] = payload

        return RedirectResponse(url=f"/?t={token}", status_code=303)

    except Exception as e:

        payload = dict(
            csv_text=csv_text,
            active_tab="eq",
            status=f"Error: {str(e)}",
            equity_html="",
            code_text="",
        )

        token = secrets.token_urlsafe(16)
        LAST_RESULT[token] = payload

        return RedirectResponse(url=f"/?t={token}", status_code=303)
# Step 2 – root route (change path to /health so it doesn't override your main page)
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Utility: your parsing logic ----------

def parse_wfo_csv_text(csv_text: str, date_format: str = "auto") -> str:
    import pandas as pd
    from date_tools import parse_date_series
    from collections import defaultdict

    df = parse_wfo_to_dataframe(csv_text)

    if df.empty:
        raise HTTPException(status_code=400, detail="No data rows found in the pasted table.")

    start_col = "Begin Out-of-Sample Data Interval"
    end_col = "End Out-of-Sample Data Interval"

    if start_col not in df.columns or end_col not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not find required date columns. "
                "Make sure you copied the main WFO table including the Out-of-Sample interval columns."
            ),
        )

    # Auto-detect using BOTH start and end columns
    if date_format == "auto":
        combined = pd.concat([df[start_col], df[end_col]], ignore_index=True)
        _, resolved = parse_date_series(combined, date_format="auto")
    else:
        resolved = date_format

    start_parsed, _ = parse_date_series(df[start_col], date_format=resolved)
    end_parsed, _ = parse_date_series(df[end_col], date_format=resolved)

    df = df.copy()
    df["_start_dt"] = start_parsed
    df["_end_dt"] = end_parsed
    df = df.dropna(subset=["_start_dt", "_end_dt"])

    def to_pl_date(date_str: str) -> str:
        date_only = str(date_str).split()[0]
        day, month, year = date_only.split("/")
        yy = int(year) % 100
        prefix = "1" if int(year) >= 2000 else "0"
        return f"{prefix}{yy:02d}{int(month):02d}{int(day):02d}"

    excluded_starts = ("IS ", "OOS ")
    excluded_exact = {
        start_col,
        end_col,
        "Begin In-Sample Data Interval",
        "End In-Sample Data Interval",
        "Begin Out-of-Sample Data Interval",
        "End Out-of-Sample Data Interval",
    }

    candidates = [c for c in df.columns if c not in excluded_exact and not c.startswith(excluded_starts)]

    def split_strategy_and_input(col_name: str) -> tuple[str, str]:
        s = (col_name or "").strip().replace("\u00a0", " ")
        if not s:
            return "", ""
        if " " not in s:
            return "", s
        strategy, input_name = s.rsplit(None, 1)
        return strategy, input_name

    groups = defaultdict(list)
    for col in candidates:
        if " " not in col:
            continue

        # More robust than is_numeric_dtype: accept numeric-looking columns even if read as strings
        series_num = pd.to_numeric(df[col], errors="coerce")
        if series_num.notna().any():
            strategy_name, input_name = split_strategy_and_input(col)
            if not strategy_name or not input_name:
                continue
            groups[strategy_name].append(col)

    if not groups:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not find input parameter columns. "
                "Your paste must include the input columns (e.g. 'Smc0012 JnBarExit', 'Smc0012 JstopLoss')."
            ),
        )

    best_prefix, strategy_cols = max(groups.items(), key=lambda kv: len(kv[1]))
    clean_var_names = {col: split_strategy_and_input(col)[1] for col in strategy_cols}

    code_blocks = []
    all_inputs = set(clean_var_names.values())
    prev_end_dt = None

    for _, row in df.iterrows():
        start_dt = row["_start_dt"]
        end_dt = row["_end_dt"]

        if prev_end_dt is not None and start_dt <= prev_end_dt:
            start_dt = prev_end_dt + pd.Timedelta(days=1)

        start = to_pl_date(start_dt.strftime("%d/%m/%Y"))
        end = to_pl_date(end_dt.strftime("%d/%m/%Y"))

        param_lines = []
        for col in strategy_cols:
            value = row[col]
            param_name = clean_var_names[col]
            param_lines.append(f"    {param_name} = {value};")

        block = f"""if Date >= {start} and Date <= {end} then begin
{chr(10).join(param_lines)}
end;"""
        code_blocks.append(block)
        prev_end_dt = end_dt

    vars_block = "vars:\n    " + ", ".join(f"{name}(0)" for name in sorted(all_inputs)) + ";"
    final_code = vars_block + "\n\n" + "\n\n".join(code_blocks)
    return final_code

# ---------- Routes ----------

@app.get("/wfo", response_class=HTMLResponse)
async def index() -> str:
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WFO Parser</title>
  <style>
    html, body {{
      height: 100%;
      margin: 0;
      overflow: hidden;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      background: #f9fafb;
    }}

    /* make the page a flex container: centered horizontally, top-aligned vertically */
    body {{
      display: flex;
      justify-content: center;   /* center left/right */
      align-items: flex-start;   /* stick to the top */
      padding-top: 10vh;         /* roughly top third */
    }}

    .card {{
      width: 100%;
      max-width: 1000px;
      background: white;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.08);
      padding: 2.5rem;
      text-align: center;
    }}

    .mode-row {{
      display: flex;
      justify-content: center;
      gap: 1.25rem;
      margin: 1rem 0 0.25rem;
      flex-wrap: wrap;
    }}

    .input-row {{
      display: flex;
      justify-content: center;  /* centers horizontally */
      margin: 1rem 0;
    }}

    input[type=file] {{
      display: block;
      margin: 0 auto;            /* fallback centering */
    }}

    textarea {{
      width: 100%;
      height: 300px;
      margin-top: 1rem;
      padding: 0.75rem;
      font-family: monospace;
      border-radius: 8px;
      border: 1px solid #d1d5db;
      background: #f9fafb;
      box-sizing: border-box;
      resize: vertical;
      line-height: 1.4;
    }}

    button {{
      background: black;
      color: white;
      border: none;
      padding: 0.75rem 1rem;
      border-radius: 8px;
      cursor: pointer;
    }}

    .hint {{ color: #6b7280; font-size: 0.9rem; }}
    .footer {{ margin-top: 1.25rem; font-size: 0.85rem; color: #6b7280; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>WFO Parser</h1>
    <p class="hint">Paste your MultiCharts WFO CSV text to generate PowerLanguage code.</p>

    <form id="form" method="post" action="/convert" enctype="multipart/form-data">

      <div class="mode-row" style="display:none;">
  <label><input type="radio" name="input_mode" value="paste" checked> Paste CSV text</label>
  <label><input type="radio" name="input_mode" value="file"> Upload CSV</label>
</div>


      <div id="file_block" class="input-row" style="display:none;">
  <input type="file" name="file" accept=".csv" />
</div>

      <div id="date_format_row" style="margin-top: 0.75rem;">
        <label for="date_format" class="hint" style="display:block; margin-bottom: 0.25rem;">
          Date format
        </label>
        <select name="date_format" id="date_format"
                style="padding: 0.5rem; border-radius: 8px; border: 1px solid #d1d5db; background: #fff;">
          <option value="auto" selected>Auto-detect (recommended)</option>
          <option value="dmy">DD/MM/YYYY (day-first)</option>
          <option value="mdy">MM/DD/YYYY (month-first)</option>
          <option value="ymd">YYYY/MM/DD (year-first)</option>
        </select>
        <div class="hint" style="margin-top: 0.35rem;">
          If Auto-detect gets confused by dates like 01/02/2024, choose the correct format here.
        </div>
      </div>

      <div id="paste_block" style="display:none; margin-top: 0.25rem;">
        <textarea name="wfo_text" placeholder="Paste the WFO CSV content here (including the header row)..."></textarea>
      </div>

      <label><input type="checkbox" name="download" value="yes"> Also download .txt file</label><br><br>
      <button type="submit">Convert</button>
    </form>

    <div id="output"></div>
    <div class="footer">Your data is processed in-memory and not stored on the server.</div>
  </div>

  <script>
    const form = document.getElementById('form');
    const fileBlock = document.getElementById('file_block');
    const pasteBlock = document.getElementById('paste_block');

    function setMode(mode) {{
      fileBlock.style.display = (mode === "file") ? "flex" : "none";
      pasteBlock.style.display = (mode === "paste") ? "block" : "none";
    }}

    document.querySelectorAll('input[name="input_mode"]').forEach(r => {{
      r.addEventListener("change", () => {{
        const mode = document.querySelector('input[name="input_mode"]:checked').value;
        setMode(mode);
      }});
    }});

    setMode("paste");

    form.addEventListener('submit', async (e) => {{
      e.preventDefault();
      const formData = new FormData(form);
      const resp = await fetch('/convert', {{ method: 'POST', body: formData }});
      const html = await resp.text();
      document.open(); document.write(html); document.close();
    }});
  </script>
</body>
</html>
"""


@app.post("/convert", response_class=HTMLResponse)
async def convert(
    file: Optional[UploadFile] = File(None),
    wfo_text: Optional[str] = Form(None),
    download: Optional[str] = Form(None),
):
    import uuid
    from fastapi.responses import HTMLResponse

    file_provided = file is not None and (file.filename or "") != ""
    text_provided = wfo_text is not None and wfo_text.strip() != ""

    # Require exactly one input
    if not file_provided and not text_provided:
        raise HTTPException(status_code=400, detail="Please upload a CSV file or paste the WFO CSV text.")
    if file_provided and text_provided:
        raise HTTPException(status_code=400, detail="Please use either upload OR paste text, not both.")

    if text_provided:
        # Treat pasted content as CSV text
        content = wfo_text.replace("\\r\\n", "\\n").replace("\\r", "\\n").encode("utf-8")
        filename = "pasted_wfo.csv"
    else:
        filename = file.filename or "wfo.csv"
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        output_text = parse_wfo_csv(content)
    except Exception as e:
        return HTMLResponse(f"<h3 style='color:red'>Error: {e}</h3>", status_code=400)

    # If the user ticked the download box, generate a unique ID
    download_html = ""
    if download == "yes":
        uid = str(uuid.uuid4())
        _downloads[uid] = output_text          # store in memory temporarily
        base = filename.rsplit(".", 1)[0]
        out_name = f"{base}_wfo.txt"
        download_html = f"<a class='btn download' href='/download/{uid}?name={out_name}'>⬇️ Download .txt File</a>"

    # Show the result in a scrollable text box
    return f"""
<!doctype html>
<html lang="en">
<head><meta charset="utf-8" /><title>WFO Parser Result</title>
<style>
html, body {{
  height: 100%;
  margin: 0;
  overflow: hidden;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  background: #f9fafb;
}}

/* position the container near the top */
body {{
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 10vh;  /* about top third */
}}

.container {{
  width: 100%;
  max-width: 1000px;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.08);
  padding: 2.5rem;
  text-align: center;
}}

h2 {{
  margin-top: 0;
}}

textarea {{
  width: 95%;
  max-width: 950px;
  height: 350px;
  border-radius: 8px;
  padding: 1rem 1.25rem;
  border: 1px solid #d1d5db;
  font-family: monospace;
  background: #f9fafb;
  box-sizing: border-box;
  margin: 0 auto;
  display: block;
  resize: vertical;
  line-height: 1.5;
  font-size: 0.95rem;
  white-space: pre;
  overflow-wrap: normal;
  overflow-x: auto;
}}

.button-row {{
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  margin-top: 1.5rem;
}}

.btn {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  text-decoration: none;
  cursor: pointer;
  padding: 0.75rem 1.5rem;
  border: 0;
  border-radius: 6px;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  font-size: 1rem;
  font-weight: 500;
  min-width: 340px;
  max-width: 340px;
  white-space: nowrap;
  transition: background 0.2s ease, transform 0.1s ease;
}}

.btn.secondary {{
  background: #e5e7eb;
  color: #111827;
}}
.btn.secondary:hover {{
  background: #d1d5db;
}}

.btn:active {{
  transform: scale(0.98);
}}

.btn.copy {{
  background: #000;
  color: #fff;
}}
.btn.copy:hover {{
  background: #1f2937;
}}

.btn.download {{
  background: #2563eb;
  color: #fff;
}}
.btn.download:hover {{
  background: #1d4ed8;
}}

#msg {{
  color: green;
  margin-left: 10px;
  font-size: 0.9rem;
  opacity: 0;
  transition: opacity 0.5s ease;
}}
#msg.show {{ opacity: 1; }}

/* Mobile: stack full-width */
@media (max-width: 600px) {{
  .button-row {{ flex-direction: column; }}
  .btn {{
    width: 100%;
    min-width: 0;
    max-width: none;
  }}
}}
</style></head>
<body>
  <div class="container">
    <h2>Generated PowerLanguage Code</h2>
    <textarea id="code" readonly>{output_text}</textarea>
    <div class="button-row">
      <button class="btn copy" onclick="copyCode()">Copy to Clipboard</button>
      {download_html}
      <span id="msg"></span>
    </div>

    <div class="button-row">
      <a class="btn secondary" href="/">← Back to upload/paste another report</a>
    </div>
  </div>

 <script>
async function copyCode() {{
  const code = document.getElementById('code');
  const msg = document.getElementById('msg');
  const text = code.value;

  try {{
    await navigator.clipboard.writeText(text);
    msg.textContent = '✓ Copied!';
  }} catch (err) {{
    code.select();
    document.execCommand('copy');
    msg.textContent = '✓ Copied (fallback)';
  }}

  msg.classList.add('show');
  setTimeout(() => msg.classList.remove('show'), 2000);
}}
</script>
</body></html>
"""

# In-memory storage for temporary downloads
_downloads: dict[str, str] = {}

@app.get("/download/{uid}")
async def download(uid: str, name: str = "output.txt"):
    from fastapi.responses import StreamingResponse
    import io

    text = _downloads.pop(uid, None)
    if text is None:
        raise HTTPException(status_code=404, detail="File not found or expired.")

    file_stream = io.BytesIO(text.encode("utf-8"))
    headers = {
        "Content-Disposition": f"attachment; filename={name}",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(file_stream, media_type="text/plain", headers=headers)


@app.get("/healthz")
async def health() -> Dict[str, str]:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}

from fastapi.responses import HTMLResponse

@app.get("/equity", response_class=HTMLResponse)
def equity_page():
    return """
    <html>
      <head><title>Equity Curve Viewer</title></head>
      <body style="font-family: system-ui; padding: 20px;">
        <h1>Equity Curve Viewer (Tool 1)</h1>
        <p>This is a placeholder page. Next step: add the paste box + Draw button here.</p>
        <p><a href="/">Back to WFO Parser</a></p>
      </body>
    </html>
    """

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
def launcher():
    return """
    <html>
      <head>
        <title>WFO Tools</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body style="font-family: system-ui; padding: 24px; max-width: 900px; margin: 0 auto;">
        <h1 style="margin:0 0 8px;">WFO Tools</h1>
        <p style="margin:0 0 18px; color:#444;">
          Choose a tool. Most people start with the equity curve.
        </p>

        <div style="display:flex; gap:12px; flex-wrap:wrap;">
          <a href="/equity" style="text-decoration:none;">
            <button style="padding:12px 16px; border-radius:10px; border:0; background:#2d6bff; color:white; font-weight:700; cursor:pointer;">
              Tool 1: Equity Curve Viewer
            </button>
          </a>

          <a href="/wfo" style="text-decoration:none;">
            <button style="padding:12px 16px; border-radius:10px; border:1px solid #bbb; background:white; color:#111; font-weight:700; cursor:pointer;">
              Tool 2: WFO Parser
            </button>
          </a>
        </div>
      </body>
    </html>
    """
