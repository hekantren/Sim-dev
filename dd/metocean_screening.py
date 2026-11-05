"""
Metocean screening for Morven from ERA5 time-series ZIPs (era5_morven_download.py
output or any ZIP the W90 app accepts).

What it reports, per calendar month:
  * mean / P90 Hs, mean 10 m and 100 m wind, fitted wind-shear exponent
  * for each operation limit set:
      start_ok_%   share of hours where limits hold at the start hour
                   (what the app currently checks)
      window_ok_%  share of hours from which the limits hold for the whole
                   window length L (persistence; what the operation needs)
      mean_wait_h  average wait from a random hour until such a window starts

Usage:
    python metocean_screening.py era5_morven/morven_north.zip --out screening_north
    python metocean_screening.py era5_morven/morven_south.zip --limits my_limits.json --hub 150

Limits JSON (list of objects):
    [{"name": "JUV jack-up", "hs": 2.0, "wind": 13.5, "hours": 6, "wind_ref": "adj10"}, ...]
wind_ref:  "adj10" = 0.9 x 10 m wind (the app's Adjusted_Windspeed)
           "u10"   = 10 m wind
           "hub"   = wind at --hub height (from 100 m wind if present, else 10 m),
                     power law with the fitted (or --alpha) exponent
"""
from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

# Defaults taken from the operations template (New_Input_for_simulations.xlsx).
# Window length = planned duration + contingency of the governing step(s).
DEFAULT_LIMITS = [
    {"name": "W90 WTG assembly/installation (Hs 6, 18 m/s)", "hs": 6.0, "wind": 18.0, "hours": 4, "wind_ref": "adj10"},
    {"name": "W90 WTG mating on foundation (Hs 4, 18 m/s)",  "hs": 4.0, "wind": 18.0, "hours": 3, "wind_ref": "adj10"},
    {"name": "W90 MP lowering/driving (Hs 2.5, 18 m/s)",     "hs": 2.5, "wind": 18.0, "hours": 15, "wind_ref": "adj10"},
    {"name": "JUV pre-load + jack-up (Hs 2, 13.5 m/s)",      "hs": 2.0, "wind": 13.5, "hours": 6, "wind_ref": "adj10"},
    {"name": "JUV blade install (8.25 m/s)",                 "hs": None, "wind": 8.25, "hours": 6, "wind_ref": "adj10"},
    {"name": "JUV blade install, limit at hub height",       "hs": None, "wind": 8.25, "hours": 6, "wind_ref": "hub"},
    {"name": "W90 blade rows if 12 m/s at hub height",       "hs": 6.0, "wind": 12.0, "hours": 4, "wind_ref": "hub"},
    {"name": "Floating MP install (Hs 1.5, 11 m/s)",         "hs": 1.5, "wind": 11.0, "hours": 10, "wind_ref": "adj10"},
    {"name": "FIV nacelle over-boarding (Hs 1.25, 10 m/s)",  "hs": 1.25, "wind": 10.0, "hours": 3, "wind_ref": "adj10"},
]

PATTERNS = {
    "time": r"^(valid_time|time|date|datetime)$",
    "u10": r"^(u10|10m_u_component_of_wind)$",
    "v10": r"^(v10|10m_v_component_of_wind)$",
    "u100": r"^(u100|100u|100m_u_component_of_wind)$",
    "v100": r"^(v100|100v|100m_v_component_of_wind)$",
    "gust": r"^(fg10|10fg|i10fg|10m_wind_gust_since_previous_post_processing)$",
    "hs": r"^(swh|significant_height_of_combined_wind_waves_and_swell)$",
    "tm": r"^(mwp|mean_wave_period)$",
    "dir": r"^(mwd|mean_wave_direction)$",
}


def load_zip(path: str) -> pd.DataFrame:
    frames = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.lower().endswith(".csv"):
                frames.append(pd.read_csv(io.BytesIO(zf.read(name))))
    if not frames:
        raise SystemExit(f"no CSV files in {path}")
    out = None
    for df in frames:
        ren = {}
        for c in df.columns:
            for k, pat in PATTERNS.items():
                if re.match(pat, str(c).strip().lower()):
                    ren[c] = k
        df = df.rename(columns=ren)[[c for c in ren.values()]]
        df["time"] = pd.to_datetime(df["time"])
        out = df if out is None else out.merge(df, on="time", how="outer")
    out = out.sort_values("time").drop_duplicates("time").set_index("time")
    full = pd.date_range(out.index[0], out.index[-1], freq="h")
    missing = len(full) - len(out)
    out = out.reindex(full)
    out.attrs["missing_hours"] = int(missing)
    return out


def prepare(df: pd.DataFrame, hub: float, alpha: float | None) -> tuple[pd.DataFrame, float]:
    d = pd.DataFrame(index=df.index)
    d["hs"] = df.get("hs")
    d["u10"] = np.hypot(df["u10"], df["v10"])
    d["adj10"] = 0.9 * d["u10"]
    if "u100" in df and "v100" in df:
        d["u100"] = np.hypot(df["u100"], df["v100"])
        m = (d["u10"] > 5) & (d["u100"] > 0)
        fit = float(np.nanmedian(np.log(d.loc[m, "u100"] / d.loc[m, "u10"]) / np.log(10.0)))
        a = alpha if alpha is not None else fit
        d["hub"] = d["u100"] * (hub / 100.0) ** a
    else:
        fit = float("nan")
        a = alpha if alpha is not None else 0.11
        d["hub"] = d["u10"] * (hub / 10.0) ** a
    if "gust" in df:
        d["gust10"] = df["gust"]
    if "tm" in df:
        d["tm"] = df["tm"]
    return d, (a if alpha is not None else fit)


def runs_forward(ok: np.ndarray) -> np.ndarray:
    r = np.zeros(len(ok) + 1, dtype=np.int32)
    for i in range(len(ok) - 1, -1, -1):
        r[i] = r[i + 1] + 1 if ok[i] else 0
    return r[:-1]


def wait_to_window(runs: np.ndarray, need: int) -> np.ndarray:
    """hours from each hour until the next hour with runs >= need (inf if none)."""
    nxt = np.full(len(runs) + 1, np.inf)
    for i in range(len(runs) - 1, -1, -1):
        nxt[i] = 0.0 if runs[i] >= need else nxt[i + 1] + 1.0
    return nxt[:-1]


def screen(d: pd.DataFrame, limits: list[dict]) -> pd.DataFrame:
    month = d.index.month
    rows = []
    for L in limits:
        w = d[L.get("wind_ref", "adj10")].to_numpy(float)
        ok = np.isfinite(w) & (w <= L["wind"])
        if L.get("hs") is not None:
            hs = d["hs"].to_numpy(float)
            ok &= np.isfinite(hs) & (hs <= L["hs"])
        runs = runs_forward(ok)
        need = max(1, int(np.ceil(L["hours"])))
        win = runs >= need
        wait = wait_to_window(runs, need)
        for mth in range(1, 13):
            sel = month == mth
            rows.append({"limit": L["name"], "month": mth,
                         "start_ok_%": round(100 * ok[sel].mean(), 1),
                         "window_ok_%": round(100 * win[sel].mean(), 1),
                         "mean_wait_h": round(float(np.mean(np.minimum(wait[sel], 24 * 60))), 1)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip")
    ap.add_argument("--limits", default=None, help="JSON file with limit sets")
    ap.add_argument("--hub", type=float, default=150.0, help="reference height for wind_ref='hub' (m)")
    ap.add_argument("--alpha", type=float, default=None, help="power-law exponent (default: fitted u100/u10)")
    ap.add_argument("--out", default="screening")
    a = ap.parse_args()

    raw = load_zip(a.zip)
    d, alpha = prepare(raw, a.hub, a.alpha)
    limits = json.loads(Path(a.limits).read_text()) if a.limits else DEFAULT_LIMITS
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    clim = d.groupby(d.index.month).agg(
        hs_mean=("hs", "mean"), hs_p90=("hs", lambda s: s.quantile(0.9)),
        u10_mean=("u10", "mean"), hub_mean=("hub", "mean"))
    clim.index.name = "month"
    clim = clim.round(2)
    tab = screen(d, limits)
    wide = tab.pivot(index="limit", columns="month", values="window_ok_%")

    clim.to_csv(out / "monthly_climate.csv")
    tab.to_csv(out / "workability_long.csv", index=False)
    wide.to_csv(out / "window_workability_by_month.csv")
    summer = tab[tab.month.isin([4, 5, 6, 7, 8, 9])].groupby("limit")[["start_ok_%", "window_ok_%", "mean_wait_h"]].mean().round(1)
    summary = {
        "file": a.zip, "hours": int(len(d)), "missing_hours_filled": raw.attrs.get("missing_hours"),
        "period": [str(d.index[0]), str(d.index[-1])],
        "annual_mean_hs": round(float(d["hs"].mean()), 2) if "hs" in d else None,
        "wind_shear_alpha_used": round(alpha, 3) if np.isfinite(alpha) else None,
        "hub_height_m": a.hub,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    with open(out / "report.md", "w") as fh:
        fh.write(f"# Metocean screening\n\n```\n{json.dumps(summary, indent=2)}\n```\n\n")
        fh.write("## Monthly climate\n\n" + summary_md(clim) + "\n\n")
        fh.write("## Apr-Sep average: start-hour check vs. full-window check\n\n" + summary_md(summer) + "\n\n")
        fh.write("## Window workability (% of hours a full window starts), by month\n\n" + summary_md(wide) + "\n")
    print(json.dumps(summary, indent=2))
    print(summer.to_string())
    print(f"\nwritten to {out.resolve()}")


def summary_md(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown()
    except ImportError:  # tabulate missing
        return "```\n" + df.to_string() + "\n```"


if __name__ == "__main__":
    main()
