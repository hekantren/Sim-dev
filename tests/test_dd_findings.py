"""
Regression tests for the Morven due-diligence findings (dd/README.md).

The app's own functions are loaded without Streamlit (dd/reproduce_findings.load_app)
and compared with the corrected model in dd/sim_reference.py. Tests marked
"finding" fail on the current app and should turn green as the fixes land.

The operations workbook is not in git; point SIM_XLSX at it or place it at
dd/New_Input_for_simulations.xlsx.
"""
from __future__ import annotations

import io
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dd"))
import reproduce_findings as RF  # noqa: E402
import sim_reference as R  # noqa: E402

APP = Path(os.environ.get("SIM_APP", ROOT / "w90_monte_carlo_v8_simapp.py"))
XLSX = Path(os.environ.get("SIM_XLSX", ROOT / "dd" / "New_Input_for_simulations.xlsx"))

N, DOCK, SPEED, HTD = 50, 185.0, 10.0, 1 / 24
TH = DOCK / SPEED

pytestmark = pytest.mark.skipif(not XLSX.exists(), reason=f"workbook not found: {XLSX}")


@pytest.fixture(scope="module")
def app():
    return RF.load_app(str(APP))


@pytest.fixture(scope="module")
def src():
    data = XLSX.read_bytes()
    return lambda: io.BytesIO(data)


def _transits(tl):
    return int(tl["Description"].str.contains("Transit", case=False).sum())


# --------------------------------------------------------------------------- #
#  Reference model sanity (should always pass)
# --------------------------------------------------------------------------- #
def test_reference_w90_mp_no_weather(src):
    plan = R.build_plan(R.read_sheet(src(), "W90 MP"), 8, N)
    assert R.expected_hours(plan, TH) / 24 == pytest.approx(96.7, abs=0.05)


# --------------------------------------------------------------------------- #
#  Finding 1: transits per cycle, not per unit
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sheet,cap", [("W90 MP", 8), ("JUV MP", 6)])
def test_mp_transits_per_cycle(app, src, sheet, cap):
    tl = app["build_mp_timeline"](src(), sheet, cap, N, HTD)
    assert _transits(tl) == 2 * math.ceil(N / cap)


def test_w90_wtg_transits_per_cycle(app, src):
    tl = app["build_w90_timeline"](src(), "W90 WTG", 8, N, HTD, dock_distance=DOCK, transit_speed=SPEED)
    assert _transits(tl) == 2 * math.ceil(N / 8)


@pytest.mark.parametrize("sheet,cap,jackups", [("JUV WTG", 6, N), ("FIV WTG", 5, 0)])
def test_sequential_wtg_port_calls(app, src, sheet, cap, jackups):
    tl = app["build_sequential_wtg_timeline"](src(), sheet, cap, N, HTD, dock_distance=DOCK, transit_speed=SPEED)
    d = tl["Description"].str.lower()
    cycles = math.ceil(N / cap)
    assert d.str.contains("transit into port").sum() == cycles
    assert d.str.contains("transit to project").sum() == cycles
    assert d.str.contains("working height").sum() == jackups


# --------------------------------------------------------------------------- #
#  Finding 2: no-weather campaign length matches the corrected cycle structure
# --------------------------------------------------------------------------- #
NO_WEATHER_CASES = [
    # sheet, carry, kind, build_plan kwargs, corrected days (from sim_reference)
    ("W90 MP", 8, "mp", {}, 96.7),
    ("JUV MP", 6, "mp", {}, 110.2),
    ("W90 WTG", 8, "w90", dict(parallel_rule="max"), 85.5),
    ("JUV WTG", 6, "seq", dict(per_unit_extra=(16, 17, 18)), 158.5),
    ("FIV WTG", 5, "seq", {}, 158.0),
    ("W90 Jacket", 1, "jk", dict(barge_fed=True, infield_rows=(8,)), 85.6),
    ("JUV Jacket", 1, "jk", dict(barge_fed=True, infield_rows=(8,)), 103.9),
]


def _app_no_weather_days(app, src, sheet, cap, kind):
    if kind in ("mp", "jk"):
        # foundations have no separate no-weather simulator: take the expected
        # hours of each timeline row (transit time or mid-range of Lo/Hi)
        tl = (app["build_mp_timeline"](src(), sheet, cap, N, HTD) if kind == "mp"
              else app["build_jacket_timeline"](src(), sheet, N, HTD))
        mid = 0.5 * (tl["Lo_Hours"] + tl["Hi_Hours"])
        return float(np.where(tl["Transit"], TH, mid).sum()) / 24
    if kind == "w90":
        tl = app["build_w90_timeline"](src(), sheet, cap, N, HTD, dock_distance=DOCK, transit_speed=SPEED)
        dur = app["add_duration_simulations_w90"](tl, src(), sheet, simulations=200, seed=42, htd=HTD,
                                                  transit_distance=DOCK, transit_speed=SPEED)
    else:
        tl = app["build_sequential_wtg_timeline"](src(), sheet, cap, N, HTD, dock_distance=DOCK,
                                                  transit_speed=SPEED)
        dur = app["add_duration_simulations_sequential"](tl, src(), sheet, simulations=200, dock_distance=DOCK,
                                                         transit_speed=SPEED, seed=42, htd=HTD)
    return float(app["_sim_totals"](dur).mean())


@pytest.mark.parametrize("sheet,cap,kind,kw,days", NO_WEATHER_CASES, ids=[c[0] for c in NO_WEATHER_CASES])
def test_reference_no_weather_days(src, sheet, cap, kind, kw, days):
    plan = R.build_plan(R.read_sheet(src(), sheet), cap, N, **kw)
    assert R.expected_hours(plan, TH) / 24 == pytest.approx(days, abs=0.05)


@pytest.mark.parametrize("sheet,cap,kind,kw,days", NO_WEATHER_CASES, ids=[c[0] for c in NO_WEATHER_CASES])
def test_app_no_weather_days(app, src, sheet, cap, kind, kw, days):
    # sampled durations for W90/sequential WTG -> allow 1 % around the reference mean
    assert _app_no_weather_days(app, src, sheet, cap, kind) == pytest.approx(days, rel=0.01)


# --------------------------------------------------------------------------- #
#  Finding 3: start date is read day-first
# --------------------------------------------------------------------------- #
def test_start_date_day_first(app):
    times = pd.date_range("2000-01-01", "2002-12-31 23:00", freq="h").to_numpy("datetime64[ns]")
    idx = app["get_seasonal_eligible_indices"](times, "1-7-2027")
    months = set(pd.DatetimeIndex(times[idx]).month)
    assert months <= {6, 7}, f"'1-7-2027' should mean 1 July, eligible months were {sorted(months)}"


@pytest.mark.parametrize("text", ["1-7-2027", "01.07.2027", "1/7/2027", "2027-07-01"])
def test_parse_start_date_formats(app, text):
    assert app["parse_start_date"](text) == pd.Timestamp("2027-07-01")


# --------------------------------------------------------------------------- #
#  Finding 4: weather limits must hold for the whole operation (persistence)
# --------------------------------------------------------------------------- #
def _step_timeline(durations, wave_lim=2.0):
    n = len(durations)
    return pd.DataFrame({
        "N": range(1, n + 1), "Sequence": range(1, n + 1), "Inventory": 0, "WTG_Left": 0,
        "Cycles_Left": 0, "Weather_Restricted": True, "Phase": "installation",
        "Transit": False, "Lo_Hours": durations, "Hi_Hours": durations,
        "Wave_Limit": wave_lim, "Wind_Limit": np.inf, "Loading_Weather": False})


def _toy_weather(hs):
    t = pd.date_range("2030-01-01", periods=len(hs), freq="h")
    return pd.DataFrame({"time": t, "swh": hs, "Adjusted_Windspeed": 5.0})


def test_weather_window_covers_whole_step(app):
    # Hs is fine at the start hour but exceeds the limit in hours 1-2: a 3 h step
    # must wait for hours 3-5 instead of starting at hour 0.
    wx = _toy_weather([1.0, 3.0, 3.0] + [1.0] * 200)
    _, dwn, s = app["simulate_weather_impacts_foundation"](
        _step_timeline([3.0]), wx, None, None, transit_distance=1, transit_speed=1,
        simulations=1, eligible_indices=np.array([0]))
    assert s["Total_Downtime_Days"].iloc[0] * 24 == pytest.approx(3.0)


def test_no_hidden_time_between_steps(app):
    # Fractional durations in good weather: the calendar span must equal the
    # reported total (no silent rounding up to the next full hour per step).
    wx = _toy_weather([1.0] * 200)
    _, _, s = app["simulate_weather_impacts_foundation"](
        _step_timeline([1.5, 1.5, 1.5]), wx, None, None, transit_distance=1, transit_speed=1,
        simulations=1, eligible_indices=np.array([0]))
    span_h = (s["Finish_Date"].iloc[0] - s["Start_Date"].iloc[0]) / pd.Timedelta(hours=1)
    assert span_h == pytest.approx(s["Total_Project_Days"].iloc[0] * 24)
    assert s["Total_Downtime_Days"].iloc[0] == pytest.approx(0.0)


@pytest.mark.parametrize("mode,expected_wait_h", [(None, 0.0), ("Nearshore", 3.0), ("Offshore", 0.0)])
def test_foundation_loading_uses_loading_weather(app, mode, expected_wait_h):
    # Project waves are fine; the barge site is too rough for the first 3 hours.
    project = _toy_weather([1.0] * 200)
    barge = _toy_weather([3.0, 3.0, 3.0] + [1.0] * 197)
    tl = _step_timeline([2.0]).assign(Loading_Weather=True)
    _, _, s = app["simulate_weather_impacts_foundation"](
        tl, project, None, None, transit_distance=1, transit_speed=1, simulations=1,
        start_times=project["time"].to_numpy("datetime64[ns]")[:1],
        barge_weather_df=barge, loading_mode=mode)
    assert s["Total_Downtime_Days"].iloc[0] * 24 == pytest.approx(expected_wait_h)


# --------------------------------------------------------------------------- #
#  Finding 5: common random numbers - every case uses the same start dates
# --------------------------------------------------------------------------- #
def test_cases_share_start_dates(app):
    wx = _toy_weather(np.where(np.arange(2000) % 50 < 40, 1.0, 3.0))
    starts = app["draw_start_times"](wx["time"].to_numpy("datetime64[ns]"), np.arange(1000), 25, seed=42)
    seen = []
    for durations in ([3.0], [2.0, 5.0, 1.5, 4.0]):       # different cases, different draw counts
        _, _, s = app["simulate_weather_impacts_foundation"](
            _step_timeline(durations), wx, None, None, transit_distance=1, transit_speed=1,
            simulations=25, start_times=starts)
        seen.append(pd.to_datetime(s["Start_Date"]).to_numpy("datetime64[ns]"))
    np.testing.assert_array_equal(seen[0], starts)
    np.testing.assert_array_equal(seen[1], starts)


def test_wtg_phase_continues_foundation_weather_history(app):
    wx = _toy_weather([1.0] * 2000)
    starts = wx["time"].to_numpy("datetime64[ns]")[[10, 200, 400]]
    offset_days = np.array([1.0, 2.5, 4.0])
    tl = _step_timeline([2.0]).drop(columns=["Weather_Restricted", "Phase"])
    _, _, s = app["simulate_sequential_wtg_weather"](
        tl, wx, None, None, None, None, transit_distance=1, transit_speed=1, simulations=3,
        start_times=starts, mp_offset_days=offset_days, loading_mode="Offshore")
    expected = pd.to_datetime(starts) + pd.to_timedelta(offset_days, unit="D")
    np.testing.assert_array_equal(pd.to_datetime(s["WTG_Start_Date"]).to_numpy(), expected.to_numpy())
