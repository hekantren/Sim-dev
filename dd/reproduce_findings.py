"""
Reproduce the code findings from the Morven due-diligence note by running the
app's OWN functions (loaded from the .py file, without Streamlit) on the
operations workbook, and compare with sim_reference.py.

    python reproduce_findings.py --app w90_monte_carlo_v8_simapp.py \
        --xlsx New_Input_for_simulations.xlsx [--weather] [--sims 120]

Prints:
  1. how many times each sheet row is executed by the app's timeline builders
     (shows transits per unit, dropped rows, repeated port calls)
  2. expected no-weather campaign days: app vs corrected cycle structure
  3. the start-date parsing issue
  4. with --weather: app vs reference on synthetic North-Sea-like weather
     (mechanism test only - NOT Morven results)
"""
from __future__ import annotations

import argparse
import ast
import io
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_reference as R  # noqa: E402

KEEP = {"_find_col", "_norm_marker", "_norm_bool", "_resolve_sheet_name", "_excel_read",
        "build_mp_timeline", "build_jacket_timeline", "simulate_weather_impacts_foundation",
        "get_seasonal_eligible_indices", "build_w90_timeline", "add_duration_simulations_w90",
        "build_sequential_wtg_timeline", "add_duration_simulations_sequential",
        "simulate_weather_impacts_w90", "simulate_sequential_wtg_weather", "_sim_totals",
        "_read_ops_sheet", "_sheet_blocks", "_plan_step", "_build_plan", "_plan_to_timeline",
        "_timeline_step_arrays", "_add_duration_simulations", "_wtg_plan_overrides",
        "_workable_runs", "_window_start"}
CONSTS = {"_TIME_COLS", "_U_COLS", "_V_COLS", "_WAVE_MARKERS", "_WIND_MARKERS", "_DROP_COLS",
          "_WAVE_HEIGHT_COLS", "_PLAN_OVERRIDES"}


def load_app(path: str) -> dict:
    """exec only the pure functions/constants of the Streamlit app."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in KEEP:
            node.decorator_list = []
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTS
                                                  for t in node.targets):
            body.append(node)
    ns = {"math": math, "np": np, "pd": pd, "io": io}
    exec(compile(ast.Module(body=body, type_ignores=[]), path, "exec"), ns)
    missing = KEEP - set(ns)
    if missing:
        raise SystemExit(f"functions not found in app: {sorted(missing)}")
    return ns


def synthetic_weather(seed=1, start="1990-01-01", end="2025-12-31 23:00") -> pd.DataFrame:
    """Toy hourly weather: annual mean Hs ~1.85 m, North-Sea-like season and persistence."""
    t = pd.date_range(start, end, freq="h"); n = len(t); rng = np.random.default_rng(seed)

    def ar1(tau, eps):
        phi = np.exp(-1 / tau); s = np.sqrt(1 - phi ** 2); x = np.empty(n); x[0] = eps[0]
        for i in range(1, n):
            x[i] = phi * x[i - 1] + s * eps[i]
        return x
    wind = ar1(18.0, rng.standard_normal(n)); swell = ar1(60.0, rng.standard_normal(n))
    sea = ar1(10.0, wind); sea = (sea - sea.mean()) / sea.std()
    wav = 0.8 * sea + 0.6 * swell; wav /= wav.std()
    m = t.month.to_numpy() - 1
    hs = np.array([2.3, 2.1, 1.9, 1.5, 1.25, 1.05, 0.95, 1.05, 1.45, 1.8, 2.1, 2.2])[m] * np.exp(0.5 * wav)
    u10 = np.clip(np.array([10.5, 10.0, 9.4, 8.0, 7.0, 6.5, 6.3, 6.8, 8.0, 9.3, 10.0, 10.4])[m]
                  * (1 + 0.42 * wind), 0.3, None)
    return pd.DataFrame({"time": t, "swh": hs, "u10": u10})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--n", type=int, default=50, help="units (turbines/foundations)")
    ap.add_argument("--dock", type=float, default=185.0, help="yard distance, nm (app default 185)")
    ap.add_argument("--speed", type=float, default=10.0)
    ap.add_argument("--weather", action="store_true")
    ap.add_argument("--sims", type=int, default=120)
    a = ap.parse_args()

    ns = load_app(a.app)
    xb = Path(a.xlsx).read_bytes(); src = lambda: io.BytesIO(xb)
    htd, N, D, S = 1 / 24, a.n, a.dock, a.speed

    print(f"=== 1. Rows executed by the app's builders (N={N}) ===")
    for sh, cap in [("W90 MP", 8), ("JUV MP", 6)]:
        tl = ns["build_mp_timeline"](src(), sh, cap, N, htd)
        tr = tl["Description"].str.contains("Transit", case=False).sum()
        print(f"{sh:8s} carry {cap}: {tr} transit rows for {N} units "
              f"(correct: {2 * math.ceil(N / cap)})")
    tl = ns["build_w90_timeline"](src(), "W90 WTG", 8, N, htd, dock_distance=D, transit_speed=S)
    print(f"W90 WTG  carry 8: transit rows = "
          f"{tl['Description'].str.contains('Transit', case=False).sum()} (correct: {2 * math.ceil(N / 8)})")
    for sh, cap in [("JUV WTG", 6), ("FIV WTG", 5)]:
        tl = ns["build_sequential_wtg_timeline"](src(), sh, cap, N, htd, dock_distance=D, transit_speed=S)
        d = tl["Description"].str.lower()
        print(f"{sh}  carry {cap}: 'transit into port' x{d.str.contains('transit into port').sum()}, "
              f"'transit to project' x{d.str.contains('transit to project').sum()}, "
              f"'jack-up to working height' x{d.str.contains('working height').sum()} "
              f"(correct: {math.ceil(N / cap)} / {math.ceil(N / cap)} / {N if 'JUV' in sh else 0})")
    for sh in ["W90 Jacket", "JUV Jacket"]:
        tl = ns["build_jacket_timeline"](src(), sh, N, htd)
        raw = ns["_excel_read"](src(), sh); raw.columns = [str(c).strip() for c in raw.columns]
        print(f"{sh}: {raw['Transit'].map(ns['_norm_bool']).sum() * N} full transits for {N} jackets "
              f"(barge-fed: 1 mobilisation)")

    print("\n=== 2. Expected no-weather days: app vs corrected structure ===")
    th = D / S
    rows = []
    for sh, cap, kind, kw in [("W90 MP", 8, "mp", {}), ("JUV MP", 6, "mp", {}),
                              ("W90 WTG", 8, "w90", dict(parallel_rule="max")),
                              ("JUV WTG", 6, "seq", dict(per_unit_extra=(16, 17, 18))),
                              ("FIV WTG", 5, "seq", {}),
                              ("W90 Jacket", 1, "jk", dict(barge_fed=True, infield_rows=(8,))),
                              ("JUV Jacket", 1, "jk", dict(barge_fed=True, infield_rows=(8,)))]:
        df = R.read_sheet(src(), sh)
        if kind in ("mp", "jk"):
            tl = (ns["build_mp_timeline"](src(), sh, cap, N, htd) if kind == "mp"
                  else ns["build_jacket_timeline"](src(), sh, N, htd))
            mid = 0.5 * (tl["Lo_Hours"] + tl["Hi_Hours"])
            app_d = float(np.where(tl["Transit"], th, mid).sum()) / 24
        elif kind == "w90":
            tl = ns["build_w90_timeline"](src(), sh, cap, N, htd, dock_distance=D, transit_speed=S)
            dur = ns["add_duration_simulations_w90"](tl, src(), sh, simulations=200, seed=42, htd=htd,
                                                     transit_distance=D, transit_speed=S)
            app_d = float(ns["_sim_totals"](dur).mean())
        else:
            tl = ns["build_sequential_wtg_timeline"](src(), sh, cap, N, htd, dock_distance=D, transit_speed=S)
            dur = ns["add_duration_simulations_sequential"](tl, src(), sh, simulations=200, dock_distance=D,
                                                            transit_speed=S, seed=42, htd=htd)
            app_d = float(ns["_sim_totals"](dur).mean())
        fixed = R.expected_hours(R.build_plan(df, cap, N, **kw), th) / 24
        rows.append({"sheet": sh, "carry": cap, "app_days": round(app_d, 1), "corrected_days": round(fixed, 1)})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n=== 3. Start date parsing ===")
    for s in ["1-7-2027", "15-7-2027"]:
        print(f"app pd.Timestamp('{s}') -> {pd.Timestamp(s).date()} ; dayfirst -> {R.parse_start_date(s).date()}")

    if not a.weather:
        return
    print("\n=== 4. Synthetic weather (mechanism test, not Morven) ===")
    wx = synthetic_weather()
    proj = pd.DataFrame({"time": wx.time, "swh": wx.swh, "Adjusted_Windspeed": 0.9 * wx.u10})
    barge = pd.DataFrame({"time": wx.time, "swh": 0.5 * wx.swh, "Adjusted_Windspeed": 0.9 * wx.u10})
    yard = pd.DataFrame({"time": wx.time, "Adjusted_Windspeed": 0.9 * wx.u10})
    elig = ns["get_seasonal_eligible_indices"](proj["time"].to_numpy("datetime64[ns]"), "2033-04-01")
    out = []
    tl = ns["build_w90_timeline"](src(), "W90 WTG", 8, N, htd, dock_distance=D, transit_speed=S)
    dur = ns["add_duration_simulations_w90"](tl, src(), "W90 WTG", simulations=a.sims, seed=42, htd=htd,
                                             transit_distance=D, transit_speed=S)
    _, _, s = ns["simulate_weather_impacts_w90"](dur, proj, barge, yard, src(), "W90 WTG", transit_distance=D,
                                                 transit_speed=S, simulations=a.sims, eligible_indices=elig,
                                                 seed=42, htd=htd, loading_mode="Nearshore")
    out.append({"case": "app | W90 nearshore", "P50_days": round(s["Total_Project_Days"].median(), 1)})
    tl = ns["build_sequential_wtg_timeline"](src(), "JUV WTG", 6, N, htd, dock_distance=D, transit_speed=S)
    dur = ns["add_duration_simulations_sequential"](tl, src(), "JUV WTG", simulations=a.sims, dock_distance=D,
                                                    transit_speed=S, seed=42, htd=htd)
    _, _, s = ns["simulate_sequential_wtg_weather"](dur, proj, barge, yard, src(), "JUV WTG", transit_distance=D,
                                                    transit_speed=S, simulations=a.sims, eligible_indices=elig,
                                                    seed=42, htd=htd, loading_mode="Yard")
    out.append({"case": "app | JUV yard", "P50_days": round(s["Total_Project_Days"].median(), 1)})
    P = R.Weather.from_frame(proj, "swh", "Adjusted_Windspeed")
    B = R.Weather.from_frame(barge, "swh", "Adjusted_Windspeed")
    Y = R.Weather.from_frame(yard, None, "Adjusted_Windspeed")
    starts = R.seasonal_start_indices(P.time, "2033-04-01", a.sims)
    for lab, sh, cap, lw, kw in [("W90 nearshore", "W90 WTG", 8, B, dict(parallel_rule="max")),
                                 ("JUV yard", "JUV WTG", 6, Y, dict(per_unit_extra=(16, 17, 18)))]:
        plan = R.build_plan(R.read_sheet(src(), sh), cap, N, **kw)
        for win in (False, True):
            res = R.simulate(plan, P, lw, starts, th, window=win)
            out.append({"case": f"fixed {'window' if win else 'start-only'} | {lab}",
                        "P50_days": round(res["total_d"].median(), 1)})
    print(pd.DataFrame(out).to_string(index=False))


if __name__ == "__main__":
    main()
