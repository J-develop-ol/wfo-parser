import streamlit as st
import pandas as pd
import os
import re
from io import StringIO
from equity_tools import generate_equity_curve

st.set_page_config(page_title="Equity Curve Viewer", layout="wide")

st.markdown(
    """
    <style>
        div.block-container {
            padding-top: 1rem;
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("📈 MultiCharts Equity Curve Viewer")
st.write("Upload a MultiCharts CSV file **or** paste the CSV data directly below:")

# --- File uploader ---
uploaded_file = st.file_uploader("Upload your MultiCharts CSV file", type="csv")

# --- Session state priming ---
if "pasted_data" not in st.session_state:
    st.session_state["pasted_data"] = ""
if "clear_paste" not in st.session_state:
    st.session_state["clear_paste"] = False

# If Clear was requested on the *previous* run, clear before rendering the widget now
if st.session_state.clear_paste:
    st.session_state.pasted_data = ""
    st.session_state.clear_paste = False

# --- Text area for pasted CSV data (bound to session state) ---
pasted_data = st.text_area(
    "Or paste your CSV data here (tab-delimited is fine):",
    key="pasted_data",
    height=200,
)

# --- Show the Clear button only if there is pasted text ---
if st.session_state.pasted_data.strip():
    clear_col, _ = st.columns([1, 5])
    with clear_col:
        if st.button("🧹 Clear pasted data"):
            st.session_state.clear_paste = True
            st.rerun()

df = None
title_name = None

# --- Robust reader for MultiCharts exports ---
def read_multicharts_csv(text_or_file):
    """Handles tab/comma delimiters and ignores MultiCharts footer summary rows."""
    if isinstance(text_or_file, str):
        raw_text = text_or_file.strip()
        # Drop footer lines beginning with "Number of"
        lines = [line for line in raw_text.splitlines() if not line.startswith("Number of")]
        if not lines:
            return pd.DataFrame()
        text = "\n".join(lines)
        delimiter = "\t" if "\t" in lines[0] else ","
        return pd.read_csv(StringIO(text), sep=delimiter)
    else:
        # Uploaded file: let pandas sniff the delimiter
        return pd.read_csv(text_or_file, sep=None, engine="python")

# --- Process the data ---
try:
    if uploaded_file is not None:
        df = read_multicharts_csv(uploaded_file)
        file_name = os.path.basename(uploaded_file.name)
        base_name = os.path.splitext(file_name)[0]

    elif pasted_data.strip() != "":
        df = read_multicharts_csv(pasted_data)
        base_name = "Pasted CSV Data"
        # Request an auto-clear on the *next* run (don’t mutate the bound key now)
        st.session_state.clear_paste = True

    if df is not None and not df.empty:
        # --- Extract info and generate chart ---
        match = re.search(r"([A-Z]{3,6})\s.*?(\d+\s*Minute)", base_name)
        if match:
            symbol = match.group(1)
            timeframe = match.group(2)
            title_name = f"{symbol} — {timeframe}"
        else:
            title_name = base_name

        fig, date_format_str = generate_equity_curve(df)

        st.markdown(f"### 📄 {title_name}")
        st.caption(f"🕒 Detected date format: {date_format_str}")

        col1, col2, col3 = st.columns([1, 3, 1])
        with col2:
            st.pyplot(fig, use_container_width=False)

    else:
        st.info("Upload a CSV file or paste CSV data to generate the equity curve.")

except Exception as e:
    st.error(f"Error processing data: {e}")
