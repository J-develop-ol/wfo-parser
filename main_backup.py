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
from datetime import datetime
from typing import Dict, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

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

def parse_wfo_csv(csv_bytes: bytes, encoding: str = "utf-8") -> str:
    import pandas as pd
    import io
    from collections import defaultdict

    text = csv_bytes.decode(encoding, errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]

    if not lines:
        raise HTTPException(status_code=400, detail="Input is empty.")

    # Detect delimiter from header line
    header = lines[0]
    if "\t" in header:
        delim = "\t"
    elif ";" in header and "," not in header:
        delim = ";"
    else:
        delim = ","

    # Keep only the rectangular table portion (ignore footer like "Number of profitable runs")
    expected_cols = header.count(delim) + 1
    table_lines = [header]

    for ln in lines[1:]:
        # Only accept rows that match the header column count
        if ln.count(delim) + 1 != expected_cols:
            break
        table_lines.append(ln)

    table_text = "\n".join(table_lines)
    df = pd.read_csv(io.StringIO(table_text), sep=delim)
    df.columns = df.columns.str.strip()

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
        start_dt = pd.to_datetime(str(row[start_col]).split()[0], dayfirst=True, errors="raise")
        end_dt = pd.to_datetime(str(row[end_col]).split()[0], dayfirst=True, errors="raise")

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
