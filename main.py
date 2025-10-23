"""
WFO Parser – FastAPI web app
---------------------------------
This app lets users upload a MultiCharts WFO CSV and see the generated PowerLanguage
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
from typing import Dict

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

app = FastAPI(title="WFO Parser", version="1.1.0")

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
    from collections import defaultdict
    import io

    f = io.StringIO(csv_bytes.decode(encoding, errors="replace"))
    df = pd.read_csv(f)
    df.columns = df.columns.str.strip()
    if df.empty:
        raise HTTPException(status_code=400, detail="CSV file is empty.")

    start_col = "Begin Out-of-Sample Data Interval"
    end_col = "End Out-of-Sample Data Interval"

    def to_pl_date(date_str: str) -> str:
        date_only = str(date_str).split()[0]
        day, month, year = date_only.split("/")
        yy = int(year) % 100
        prefix = "1" if int(year) >= 2000 else "0"
        return f"{prefix}{yy:02d}{int(month):02d}{int(day):02d}"

    excluded_starts = ("IS ", "OOS ")
    excluded_exact = {start_col, end_col, "Begin In-Sample Data Interval", "End In-Sample Data Interval"}
    candidates = [c for c in df.columns if c not in excluded_exact and not c.startswith(excluded_starts)]

    from collections import defaultdict
    groups = defaultdict(list)
    for col in candidates:
        if " " in col and pd.api.types.is_numeric_dtype(df[col]):
            prefix = col.split(" ", 1)[0]
            groups[prefix].append(col)

    if not groups:
        raise HTTPException(status_code=400, detail="Could not find numeric strategy columns with a prefix like 'Strategy Param'.")

    best_prefix, strategy_cols = max(groups.items(), key=lambda kv: len(kv[1]))
    clean_var_names = {col: col.split(" ", 1)[1].replace(" ", "_") for col in strategy_cols}

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

@app.get("/", response_class=HTMLResponse)
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

.input-row {{
  display: flex;
  justify-content: center;  /* centers horizontally */
  margin: 1rem 0;
}}

input[type=file] {{
  display: block;
  margin: 0 auto;            /* fallback centering */
}}


    textarea {{ width: 100%; height: 300px; margin-top: 1rem; padding: 0.75rem; font-family: monospace; border-radius: 8px; border: 1px solid #d1d5db; background: #f9fafb; }}
    button {{ background: black; color: white; border: none; padding: 0.75rem 1rem; border-radius: 8px; cursor: pointer; }}
    .hint {{ color: #6b7280; font-size: 0.9rem; }}
    input[type=file] {{ display: block; margin: 1rem 0; }}
    .footer {{ margin-top: 1.25rem; font-size: 0.85rem; color: #6b7280; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>WFO Parser</h1>
    <p class="hint">Upload your MultiCharts WFO CSV to generate PowerLanguage code.</p>
    <form id="form" method="post" action="/convert" enctype="multipart/form-data">
  <div class="input-row">
    <input type="file" name="file" accept=".csv" required />
  </div>
  <label><input type="checkbox" name="download" value="yes"> Also download .txt file</label><br><br>
  <button type="submit">Convert</button>
</form>

    <div id="output"></div>
    <div class="footer">Your file is processed in-memory and not stored on the server.</div>
  </div>
  <script>
  const form = document.getElementById('form');
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
async def convert(file: UploadFile = File(...), download: str | None = Form(None)):
    import io, uuid
    from fastapi.responses import HTMLResponse

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
  width: 95%;                  /* slightly narrower than container */
  max-width: 950px;            /* ensures even padding visually */
  height: 350px;
  border-radius: 8px;
  padding: 1rem 1.25rem;       /* comfortable inner spacing */
  border: 1px solid #d1d5db;
  font-family: monospace;
  background: #f9fafb;
  box-sizing: border-box;
  margin: 0 auto;              /* centers it perfectly */
  display: block;              /* ensures horizontal centering works */
  resize: vertical;            /* allows user to resize vertically */
  line-height: 1.5;
  font-size: 0.95rem;
  white-space: pre;
  overflow-wrap: normal;
  overflow-x: auto;
}}

.button-row {{
  display: flex;
  flex-direction: column;    /* stack buttons vertically */
  align-items: center;       /* center everything horizontally */
  gap: 1rem;
  margin-top: 1.5rem;
}}

.back-link {{
  text-align: center;   /* ensures the link is horizontally centered */
  margin-top: 1.5rem;   /* nice spacing below buttons */
}}

.back-link a {{
  color: #111827;
  text-decoration: none;
  font-size: 0.95rem;
}}

.back-link a:hover {{
  text-decoration: underline;
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
  min-width: 340px;     /* increased width */
  max-width: 340px;
  white-space: nowrap;  /* keeps text on one line */
  transition: background 0.2s ease, transform 0.1s ease;
}}

.btn.secondary {{
  background: #e5e7eb; /* light gray */
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
  <a class="btn secondary" href="/">← Back to upload another file</a>
</div>



  </div>

  <script>
  function copyCode() {{
    const code = document.getElementById('code');
    const msg = document.getElementById('msg');

    if (!navigator.clipboard) {{
      code.select();
      document.execCommand('copy');
    }} else {{
      navigator.clipboard.writeText(code.value);
    }}

    msg.textContent = '✓ Copied!';
    msg.classList.add('show');
    setTimeout(() => {{
      msg.classList.remove('show');
    }}, 2000);
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
