"""
Download ERA5 hourly time series for the Morven sites and candidate loading
locations, in the same ZIP/CSV format the W90 Monte Carlo app accepts
("2.1b Upload ERA5 ZIPs manually").

Setup (once):
    pip install "cdsapi>=0.7.7"
    Accept the licence (CC-BY-4.0) at the bottom of the dataset's download form:
    https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels-timeseries
    Put your token in ~/.cdsapirc:
        url: https://cds.climate.copernicus.eu/api
        key: <PERSONAL-ACCESS-TOKEN>
    (or pass --key / set the CDSAPI_KEY environment variable)

Examples:
    python era5_morven_download.py                       # all default sites
    python era5_morven_download.py --sites morven_north morven_south
    python era5_morven_download.py --list

Compared with the app's request this also fetches 100 m wind and 10 m gusts.
The extra columns do not disturb the app (it still uses u10/v10), and the
screening script uses them for hub-height wind.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

DATASET = "reanalysis-era5-single-levels-timeseries"

# ERA5 0.25 deg grid points (the service uses the nearest grid point).
# Each point was checked to be over sea (EMODnet bathymetry) except wind-only yards.
SITES = {
    # project sites (wind + waves)
    "morven_north":  dict(lat=56.75, lon=-1.00, kind="project",
                          note="Morven North centre 56.7357N 0.9893W; ~70 m depth at grid point"),
    "morven_south":  dict(lat=56.50, lon=-0.75, kind="project",
                          note="Morven South centre 56.5432N 0.7072W; ~69 m depth at grid point"),
    # nearshore / barge-loading proxies (wind + waves) - open-water points, so waves
    # are CONSERVATIVE for a sheltered anchorage (ERA5 cannot resolve sheltering)
    "forth_mouth":   dict(lat=56.25, lon=-2.50, kind="barge",
                          note="Mouth of Firth of Forth (proxy for a Forth anchorage), ~43 m"),
    "moray_firth":   dict(lat=57.75, lon=-3.75, kind="barge",
                          note="Inner Moray Firth (proxy for Cromarty Firth / Nigg), ~35 m"),
    "app_default_barge": dict(lat=56.50, lon=-2.50, kind="barge",
                          note="Current app default barge point off Arbroath, ~29 m"),
    # yards (wind only)
    "yard_dundee":   dict(lat=56.50, lon=-3.00, kind="yard", note="Port of Dundee (land grid point)"),
    "yard_aberdeen": dict(lat=57.25, lon=-2.00, kind="yard", note="Aberdeen South Harbour area"),
}

WIND = ["10m_u_component_of_wind", "10m_v_component_of_wind",
        "100m_u_component_of_wind", "100m_v_component_of_wind",
        "10m_wind_gust_since_previous_post_processing"]
WAVES = ["significant_height_of_combined_wind_waves_and_swell",
         "mean_wave_period", "mean_wave_direction"]


def make_client(key: str | None):
    try:
        import cdsapi
    except ImportError:
        sys.exit('cdsapi is not installed: pip install "cdsapi>=0.7.7"')
    key = key or os.environ.get("CDSAPI_KEY")
    if key:
        return cdsapi.Client(url="https://cds.climate.copernicus.eu/api", key=key)
    return cdsapi.Client()  # reads ~/.cdsapirc


def check_zip(path: Path) -> dict:
    """Light sanity check: list CSVs, columns, rows, time span, missing values."""
    import pandas as pd
    info = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with zf.open(name) as fh:
                df = pd.read_csv(fh)
            tcol = next((c for c in ("valid_time", "time", "date") if c in df.columns), None)
            t = pd.to_datetime(df[tcol]) if tcol else None
            info[name] = {
                "columns": list(df.columns),
                "rows": int(len(df)),
                "first": str(t.min()) if t is not None else None,
                "last": str(t.max()) if t is not None else None,
                "nan_share": {c: round(float(df[c].isna().mean()), 4)
                              for c in df.columns if c not in (tcol, "latitude", "longitude")},
            }
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sites", nargs="*", default=list(SITES), help="site keys (default: all)")
    ap.add_argument("--start", default="1990-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default="era5_morven")
    ap.add_argument("--key", default=None, help="CDS personal access token (else ~/.cdsapirc)")
    ap.add_argument("--list", action="store_true", help="list sites and exit")
    a = ap.parse_args()

    if a.list:
        for k, s in SITES.items():
            print(f"{k:18s} {s['lat']:6.2f} {s['lon']:6.2f}  {s['kind']:7s} {s['note']}")
        return
    unknown = [s for s in a.sites if s not in SITES]
    if unknown:
        sys.exit(f"unknown site(s): {unknown}. Use --list.")

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    client = make_client(a.key)
    manifest = {}
    for key in a.sites:
        s = SITES[key]
        variables = WIND + (WAVES if s["kind"] in ("project", "barge") else [])
        req = {"variable": variables,
               "location": {"latitude": s["lat"], "longitude": s["lon"]},
               "date": [f"{a.start}/{a.end}"],
               "data_format": "csv"}
        target = out / f"{key}.zip"
        print(f"[{key}] requesting {len(variables)} variables at {s['lat']}, {s['lon']} ...", flush=True)
        t0 = time.time()
        client.retrieve(DATASET, req, str(target))
        info = check_zip(target)
        manifest[key] = {**s, "file": target.name, "request": req,
                         "seconds": round(time.time() - t0), "contents": info}
        for name, d in info.items():
            bad = {c: v for c, v in d["nan_share"].items() if v > 0.01}
            print(f"   {name}: {d['rows']} rows {d['first']} -> {d['last']}"
                  + (f"  WARNING missing values: {bad}" if bad else ""))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(f"done -> {out.resolve()}  (upload project/barge/yard ZIPs in the app, section 2.1b)")


if __name__ == "__main__":
    main()
