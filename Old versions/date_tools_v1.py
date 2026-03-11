import re
import pandas as pd

_DATE_RE = re.compile(r"^\s*(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})\s*$")

def _normalize_date_token(s: str) -> str:
    s = (s or "").strip().strip('"').strip("'").strip()
    # keep only first whitespace-separated token (drops "18:30:00")
    s = s.split()[0] if s else ""
    # remove trailing commas/semicolons etc.
    s = re.sub(r"[,\;\)\]]+$", "", s)
    # normalize separators
    s = s.replace(".", "/").replace("-", "/")
    return s

def _infer_date_order(tokens: list[str]) -> str:
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
        a_vals.append(a_i)
        b_vals.append(b_i)
        c_vals.append(c_i)

    if not a_vals:
        return "dmy"

    year_first_hits = sum(1 for x in a_vals if x >= 1900)
    if year_first_hits >= max(1, len(a_vals) // 2):
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