"""
Reference implementation of the fixes proposed in the Morven due-diligence note
for the W90 Monte Carlo app (w90_monte_carlo_v8_simapp.py).

It reads the same operations workbook as the app (New_Input_for_simulations.xlsx)
and is deliberately independent of Streamlit, so it can be unit-tested and then
ported into the app function by function.

Fixes implemented
-----------------
1. Cycle structure (all vessels, MP and WTG sheets):
       pre   rows before Loading START          -> once per cycle
       load  Loading START .. Loading END       -> once per unit carried
       mid   rows between Loading END and
             Installation START (e.g. transit)  -> once per cycle
       inst  Installation START .. END          -> once per unit installed
       post  rows after Installation END        -> once per cycle
   Rows can be moved into the per-unit installation block with
   `per_unit_extra` (e.g. JUV pre-load / jack-up at each location).
2. Parallel loading (W90): each Parallell-1/Parallell-2 pair takes the LONGER
   duration and the STRICTER limits of the two rows.
3. Weather check over the whole operation (persistence): a weather-restricted
   step starts only when the limits hold for ceil(duration) consecutive hours.
   `window=False` reproduces the app's start-hour-only check.
4. Common random numbers: start dates are drawn once and shared by every case;
   durations use a separate random stream.
5. Optional alpha factor (OPWF = alpha * OPLIM) and wind-height correction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

HOURS_PER_DAY = 24.0


# --------------------------------------------------------------------------- #
#  Workbook reading
# --------------------------------------------------------------------------- #
def _marker(x) -> str:
    s = str(x).strip().upper()
    return "" if s in {"", "FALSE", "NAN", "NONE"} else s


def _flag(x) -> bool:
    return str(x).strip().upper() == "TRUE"


def read_sheet(src, sheet_name: str) -> pd.DataFrame:
    """Return a normalised operations table for one workbook sheet."""
    xl = pd.ExcelFile(src)
    real = next((s for s in xl.sheet_names if s.strip() == sheet_name.strip()), sheet_name)
    raw = pd.read_excel(xl, sheet_name=real)
    raw.columns = [str(c).strip() for c in raw.columns]
    desc_col = next(c for c in raw.columns if c.startswith("Description"))
    p_cols = [c for c in raw.columns if c.startswith("Parallell")]
    df = pd.DataFrame({
        "N": pd.to_numeric(raw["N"], errors="coerce"),
        "desc": raw[desc_col].astype(str).str.strip(),
        "seq": pd.to_numeric(raw["Seq Dur (hrs)"], errors="coerce"),
        "tr": pd.to_numeric(raw["Tr (hrs)"], errors="coerce"),
        "transit": raw["Transit"].map(_flag),
        "L": raw["Loading"].map(_marker),
        "I": raw["Installation"].map(_marker),
        "wave": pd.to_numeric(raw["Oplim Waves (m)"], errors="coerce").fillna(np.inf),
        "wind": pd.to_numeric(raw["Oplim Wind (m/s)"], errors="coerce").fillna(np.inf),
        "p1": raw[p_cols[0]].map(_flag) if len(p_cols) > 0 else False,
        "p2": raw[p_cols[1]].map(_flag) if len(p_cols) > 1 else False,
    })
    df = df.dropna(subset=["N"]).reset_index(drop=True)
    df["N"] = df["N"].astype(int)
    df["seq"] = df["seq"].fillna(df["tr"])
    df["tr"] = df["tr"].fillna(df["seq"])
    df["lo"] = df[["seq", "tr"]].min(axis=1)
    df["hi"] = df[["seq", "tr"]].max(axis=1)
    return df


# --------------------------------------------------------------------------- #
#  Campaign plan
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    n: str
    desc: str
    lo: float          # hours
    hi: float          # hours
    wave: float        # Hs limit (m)
    wind: float        # wind limit (m/s, same reference as the weather series)
    transit: bool
    loading: bool      # True -> loading-location weather, else project weather
    block: str
    unit: int = 0


def _step(r, block, unit=0, loading=False) -> Step:
    return Step(str(r.N), r.desc, float(r.lo), float(r.hi), float(r.wave), float(r.wind),
                bool(r.transit), loading, block, unit)


def _blocks(df: pd.DataFrame):
    idx = lambda col, m: list(df.index[df[col].eq(m)])
    ls, le = idx("L", "START"), idx("L", "END")
    is_, ie = idx("I", "START"), idx("I", "END")
    if not is_ and ie:                      # JUV/FIV MP sheets: END where START belongs
        is_, ie = [ie[0]], [ie[-1]]
    if not ls:                              # jacket sheets: no loading block
        i0, i1 = is_[0], ie[-1]
        return df.iloc[:i0], df.iloc[0:0], df.iloc[0:0], df.iloc[i0:i1 + 1], df.iloc[i1 + 1:]
    l0, l1, i0, i1 = ls[0], le[0], is_[0], ie[-1]
    return df.iloc[:l0], df.iloc[l0:l1 + 1], df.iloc[l1 + 1:i0], df.iloc[i0:i1 + 1], df.iloc[i1 + 1:]


def build_plan(df: pd.DataFrame, carry_cap: int, n_units: int, *,
               per_unit_extra=(), parallel_rule: str = "max",
               barge_fed: bool = False, infield_rows=()) -> list[Step]:
    """Build the step list for a whole campaign.

    barge_fed:     jacket-style sheets where the vessel stays in the field; the
                   rows before the install block run once (mobilisation).
    infield_rows:  sheet N's that are flagged Transit but are in-field moves;
                   they keep their own sheet hours instead of the transit time.
    """
    df = df.copy()
    if len(infield_rows):
        df.loc[df["N"].isin(infield_rows), "transit"] = False
    pre, load, mid, inst, post = _blocks(df)
    extra = df[df["N"].isin(per_unit_extra)]
    mid = mid[~mid["N"].isin(per_unit_extra)]
    # mid rows before the first transit row happen in port -> loading weather
    mid_tr = np.flatnonzero(mid["transit"].to_numpy())
    mid_port = set(mid.index[: mid_tr[0]]) if mid_tr.size else set()

    if barge_fed:
        # vessel stays in the field: mobilise once, then install unit by unit;
        # rows after the install block (reposition) are per-unit in-field moves
        plan = [_step(r, "pre") for r in pre.itertuples()]
        for u in range(int(n_units)):
            plan += [_step(r, "inst", u) for r in extra.itertuples()]
            plan += [_step(r, "inst", u) for r in inst.itertuples()]
            plan += [_step(r, "post", u) for r in post.itertuples()]
        return plan

    # per-unit loading steps (pairs for parallel loading)
    if len(load) and load["p1"].any() and load["p2"].any():
        a, b = load[load["p1"]], load[load["p2"]]
        once_rows = load[~(load["p1"] | load["p2"])]
        unit_load = []
        for k in range(max(len(a), len(b))):
            ra = a.iloc[k] if k < len(a) else None
            rb = b.iloc[k] if k < len(b) else None
            rows = [r for r in (ra, rb) if r is not None]
            if parallel_rule == "p1" and ra is not None:
                lo, hi, wv, wd = ra.lo, ra.hi, ra.wave, ra.wind
            else:  # "max": critical path of the pair, strictest limits
                lo, hi = max(r.lo for r in rows), max(r.hi for r in rows)
                wv, wd = min(r.wave for r in rows), min(r.wind for r in rows)
            unit_load.append(Step("+".join(str(int(r.N)) for r in rows),
                                  " || ".join(r.desc for r in rows)[:120],
                                  float(lo), float(hi), float(wv), float(wd),
                                  False, True, "load"))
    else:
        once_rows = load.iloc[0:0]
        unit_load = [_step(r, "load", loading=True) for r in load.itertuples()]

    plan: list[Step] = []
    left = int(n_units)
    while left > 0:
        k = min(int(carry_cap), left)
        plan += [_step(r, "pre", loading=not r.transit) for r in pre.itertuples()]
        for u in range(k):
            plan += [Step(**{**s.__dict__, "unit": u}) for s in unit_load]
        plan += [_step(r, "load-once", loading=True) for r in once_rows.itertuples()]
        plan += [_step(r, "mid", loading=(i in mid_port)) for i, r in zip(mid.index, mid.itertuples())]
        for u in range(k):
            plan += [_step(r, "inst", u) for r in extra.itertuples()]
            plan += [_step(r, "inst", u) for r in inst.itertuples()]
        plan += [_step(r, "post") for r in post.itertuples()]
        left -= k
    return plan


def expected_hours(plan: list[Step], transit_hours: float) -> float:
    return sum(transit_hours if s.transit else 0.5 * (s.lo + s.hi) for s in plan)


# --------------------------------------------------------------------------- #
#  Weather
# --------------------------------------------------------------------------- #
@dataclass
class Weather:
    """Hourly series on a regular 1-hour time base (no gaps)."""
    time: np.ndarray            # datetime64[ns]
    hs: np.ndarray | None       # None -> waves not checked (yard)
    wind: np.ndarray
    _runs: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_frame(cls, df: pd.DataFrame, hs_col: str | None, wind_col: str) -> "Weather":
        d = df.copy()
        d["time"] = pd.to_datetime(d["time"])
        d = d.sort_values("time").drop_duplicates("time").set_index("time")
        full = pd.date_range(d.index[0], d.index[-1], freq="h")
        d = d.reindex(full).interpolate(limit=6)   # fill short gaps only
        hs = d[hs_col].to_numpy(float) if hs_col else None
        return cls(full.to_numpy("datetime64[ns]"), hs, d[wind_col].to_numpy(float))

    def runs(self, wave_lim: float, wind_lim: float, alpha: float = 1.0) -> np.ndarray:
        """run[t] = consecutive workable hours starting at t (limits * alpha)."""
        key = (wave_lim, wind_lim, alpha)
        if key not in self._runs:
            ok = self.wind <= wind_lim * alpha
            if self.hs is not None and np.isfinite(wave_lim):
                ok &= self.hs <= wave_lim * alpha
            ok &= np.isfinite(self.wind)
            r = np.zeros(len(ok) + 1, dtype=np.int32)
            for i in range(len(ok) - 1, -1, -1):  # backward cumulative run length
                r[i] = r[i + 1] + 1 if ok[i] else 0
            self._runs[key] = r[:-1]
        return self._runs[key]


def parse_start_date(s: str) -> pd.Timestamp:
    """'2027-07-01' (ISO) or '1-7-2027' / '01.07.2027' (day first) -> 1 July 2027.
    The app uses pd.Timestamp('1-7-2027'), which pandas reads month-first (7 January)."""
    s = str(s).strip()
    if len(s) >= 8 and s[:4].isdigit() and s[4] in "-/.":
        return pd.Timestamp(s)
    return pd.to_datetime(s, dayfirst=True)


def seasonal_start_indices(times: np.ndarray, start_date: str, n: int, seed: int = 42,
                           window_days: int = 7, runway_days: int = 1100) -> np.ndarray:
    """Draw n start hours within +/- window_days of start_date's day-of-year,
    leaving `runway_days` of weather after the start. Shared by all cases."""
    t = pd.DatetimeIndex(times)
    tdoy = parse_start_date(start_date).dayofyear
    circ = np.minimum(np.abs(t.dayofyear - tdoy), 365 - np.abs(t.dayofyear - tdoy))
    ok = (circ <= window_days) & (t <= t[-1] - pd.Timedelta(days=runway_days))
    elig = np.flatnonzero(ok)
    return np.random.default_rng(seed).choice(elig, size=n, replace=True)


def year_start_indices(times: np.ndarray, start_date: str, runway_days: int = 1100) -> np.ndarray:
    """One start per weather year (same calendar date): the honest sample size."""
    t = pd.DatetimeIndex(times)
    md = parse_start_date(start_date)
    out = []
    for y in range(t[0].year, t[-1].year + 1):
        s = pd.Timestamp(year=y, month=md.month, day=md.day)
        if t[0] <= s <= t[-1] - pd.Timedelta(days=runway_days):
            out.append(int(np.searchsorted(times, s.to_datetime64())))
    return np.array(out, dtype=int)


# --------------------------------------------------------------------------- #
#  Simulation
# --------------------------------------------------------------------------- #
def simulate(plan: list[Step], project: Weather, loading: Weather | None,
             start_idx: np.ndarray, transit_hours: float, *, window: bool = True,
             alpha: float = 1.0, seed: int = 7, max_wait_h: int = 5000,
             offset_h: np.ndarray | None = None) -> pd.DataFrame:
    """Return one row per simulation with active/waiting/total days.

    Both weather objects must share the same hourly time base.
    `offset_h` lets a WTG phase start where the foundation phase ended
    (same weather history).
    """
    if loading is not None and len(loading.time) != len(project.time):
        raise ValueError("project and loading weather must share one hourly time base")
    rng = np.random.default_rng(seed)
    n_t = len(project.time)
    res = []
    for si, s0 in enumerate(start_idx):
        t = float(s0) + (float(offset_h[si]) if offset_h is not None else 0.0)
        active = wait = 0.0
        for st in plan:
            dur = transit_hours if st.transit else float(rng.uniform(st.lo, st.hi))
            restricted = np.isfinite(st.wave) or np.isfinite(st.wind)
            if restricted:
                wx = loading if (st.loading and loading is not None) else project
                runs = wx.runs(st.wave, st.wind, alpha)
                i0 = int(math.floor(t + 1e-9))          # hour that contains t
                if i0 + 1 >= n_t:
                    raise RuntimeError("ran out of weather data")
                # start now if every hour touched by [t, t+dur) is workable
                need_now = max(1, math.ceil(t + dur - 1e-9) - i0) if window else 1
                if runs[i0] >= need_now:
                    w = 0.0
                else:                                   # wait for the next full-hour window
                    need = max(1, math.ceil(dur - 1e-9)) if window else 1
                    hit = np.empty(0, dtype=int)
                    for span in (72, 720, max_wait_h):
                        seg = runs[i0 + 1:min(n_t, i0 + 1 + span)]
                        hit = np.flatnonzero(seg >= need)
                        if hit.size:
                            break
                    if hit.size == 0:
                        raise RuntimeError(f"no window for step {st.n} within {max_wait_h} h")
                    w = (i0 + 1 + hit[0]) - t
                wait += w
                t += w
            active += dur
            t += dur
        res.append({"start": pd.Timestamp(project.time[int(s0)]),
                    "active_d": active / HOURS_PER_DAY, "wait_d": wait / HOURS_PER_DAY,
                    "total_d": (active + wait) / HOURS_PER_DAY,
                    "end_h": t})
    return pd.DataFrame(res)


def summarise(df: pd.DataFrame, label: str) -> dict:
    q = df["total_d"].quantile([0.1, 0.5, 0.9])
    return {"case": label, "n": len(df), "P10_d": round(q[0.1], 1), "P50_d": round(q[0.5], 1),
            "P90_d": round(q[0.9], 1), "mean_wait_d": round(df["wait_d"].mean(), 1),
            "WoW_%": round(100 * df["wait_d"].sum() / df["total_d"].sum(), 1)}


# --------------------------------------------------------------------------- #
#  Wind height
# --------------------------------------------------------------------------- #
def wind_at_height(z: float, u10=None, u100=None, alpha: float = 0.11):
    """Power-law extrapolation. Prefer ERA5 100 m wind when available.
    alpha ~0.10-0.14 offshore (site specific; check against site lidar data)."""
    if u100 is not None:
        return np.asarray(u100) * (z / 100.0) ** alpha
    return np.asarray(u10) * (z / 10.0) ** alpha
