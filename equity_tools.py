from date_tools import parse_date_series
import pandas as pd
import matplotlib.pyplot as plt
import re

def generate_equity_curve(df, date_format: str = "auto"):
    """
    Generate an equity curve from a MultiCharts Walk-Forward report.
    Automatically detects date format (UK vs US) in a robust way.
    Returns both the matplotlib figure and the detected date format string.
    """

    required_columns = ["End Out-of-Sample Data Interval", "OOS Net Profit"]
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

        # --- Date parsing (supports / . - and auto/override) ---
    parsed_dates, resolved = parse_date_series(
        df["End Out-of-Sample Data Interval"],
        date_format=date_format
    )
    df = df.copy()
    df["End Out-of-Sample Data Interval"] = parsed_dates
    df = df.dropna(subset=["End Out-of-Sample Data Interval"])

    date_format_str = {"dmy": "DD/MM/YYYY", "mdy": "MM/DD/YYYY", "ymd": "YYYY/MM/DD"}.get(resolved, "DD/MM/YYYY")

    # --- Clean and convert profit values ---
    df["OOS Net Profit"] = (
        df["OOS Net Profit"]
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace("[^0-9.\-]", "", regex=True)
        .replace("", "0")
        .astype(float)
    )

    # --- Sort and compute cumulative equity ---
    df = df.sort_values("End Out-of-Sample Data Interval").reset_index(drop=True)
    df["Equity"] = df["OOS Net Profit"].cumsum()

    # --- Plot the equity curve with zero line and peaks ---
    fig, ax = plt.subplots(figsize=(6.8, 4.2))

    ax.axhline(0, color="black", linewidth=0.8, linestyle="--", alpha=0.7)

    ax.plot(
        df["End Out-of-Sample Data Interval"],
        df["Equity"],
        color="black",
        linewidth=1.0
    )

    running_max = df["Equity"].cummax()
    peak_points = df[df["Equity"] == running_max]

    ax.scatter(
        peak_points["End Out-of-Sample Data Interval"],
        peak_points["Equity"],
        color="limegreen",
        s=10,
        zorder=3,
        label="Peaks"
    )

    ax.set_xlabel("End of Out-of-Sample Period", fontsize=7)
    ax.set_ylabel("Cumulative Equity", fontsize=7)
    ax.set_title("Walk-Forward Equity Curve (OOS Net Profit)", fontsize=8)
    ax.tick_params(axis="both", labelsize=6)
    ax.grid(True, linewidth=0.3, alpha=0.6)
    fig.tight_layout()

    return fig, date_format_str
