# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit Monte Carlo simulator for offshore wind installation campaigns (foundations, then WTGs)
comparing the W90 vessel against jack-up (JUV) and floating (FIV) installation vessels. Users upload an
operations workbook (one sheet per vessel × phase) and ERA5 weather ZIPs; the app builds a step
timeline per case, draws random durations, and adds weather downtime by sampling historical start dates.

- `w90_monte_carlo_v8_simapp.py` — **the main app** (deploy target, see README). All fixes go here.
- `w90_monte_carlo_v8_app_FIXED (1).py` — older variant kept for reference. It lacks
  `_resolve_sheet_name`, so it crashes on the workbook's sheet names that have trailing spaces
  (e.g. `"W90 WTG "`). Don't port fixes into it unless asked.
- `dd/` — Morven due-diligence tooling. `dd/README.md` is **the plan for the fixes** (ordered list
  under "Porte rettelsene inn i appen"). Do them one at a time, one commit per fix.

## Commands

```powershell
python -m pip install -r requirements.txt
python -m streamlit run w90_monte_carlo_v8_simapp.py

# Show the known bugs: app's own functions vs corrected logic (no Streamlit needed)
python dd\reproduce_findings.py --app w90_monte_carlo_v8_simapp.py --xlsx dd\New_Input_for_simulations.xlsx
#   add --weather [--sims 120] for the synthetic-weather mechanism test (slow; not Morven results)

# ERA5 download (needs CDS token in ~/.cdsapirc) and workability screening
python dd\era5_morven_download.py --list
python dd\metocean_screening.py era5_morven\morven_north.zip --out screening_north --hub 150
```

There is no linter or test suite configured yet.

## Architecture of the app

The app is a single top-to-bottom Streamlit script; sections are delimited by `# ═══` banners and
numbered like the UI (1.x inputs in the sidebar/top, 1.5 workbook upload, 2.x weather, 3–5 run).
Pure functions are defined inline between the UI code, so importing the file runs Streamlit.

Pipeline per case (vessel × loading mode × phase):

1. **Workbook parsing** — `_excel_read` / `_resolve_sheet_name`, `_find_col`, `_norm_marker`,
   `_norm_bool`. Sheet rows carry `N`, `Seq Dur (hrs)`, `Tr (hrs)`, `Transit`, `Loading` /
   `Installation` START/END markers, `Oplim Waves (m)`, `Oplim Wind (m/s)` and `Parallell-1/2` flags.
2. **Timeline builders** — expand the sheet into one row per executed step for the whole campaign:
   `build_mp_timeline`, `build_jacket_timeline` (foundations), `build_w90_timeline` (parallel
   loading), `build_sequential_wtg_timeline` (JUV/FIV). Transit rows use
   `_mode_transit_distance(mode, ...)` / transit speed instead of sheet hours.
3. **Durations** — `add_duration_simulations_w90` / `add_duration_simulations_sequential` draw
   uniform durations between `Seq Dur` and `Tr`.
4. **Weather** — `load_all_weather` merges project / barge / yard ZIPs; wind is
   `Adjusted_Windspeed = 0.9 × |u10,v10|`. `get_seasonal_eligible_indices` picks start hours around
   the start date; `simulate_weather_impacts_foundation`, `simulate_weather_impacts_w90`,
   `simulate_sequential_wtg_weather` step through the timeline and wait for workable weather
   (nested `wait` helpers). Loading steps use loading-location weather (barge or yard by mode).
5. **Output** — `build_project_summary`, `plot_*`, `build_simulation_excel_export`,
   `render_advanced_weather_intelligence`.

Results are kept in `st.session_state` so that touching widgets after "Run All Simulations" does
not wipe them (a `st.button` is only true on the click rerun).

`htd = 1/24` converts hours to days; despite their names, columns like `Seq_Duration_Hours` in the
W90 timeline hold days.

## Due-diligence reference (`dd/`)

- `dd/sim_reference.py` is the corrected, Streamlit-free model: five-block cycle
  (pre / load × k / mid / install × k / post via `build_plan`), parallel pairs take the longer
  duration and stricter limits, full-window weather check in `simulate(window=True)`, start indices
  drawn once and shared by all cases, day-first `parse_start_date`.
- `dd/reproduce_findings.py` loads the app by AST: it execs only the functions/constants named in
  `KEEP` / `CONSTS`. **Renaming or removing any of those app functions breaks it** — update `KEEP`
  in the same change.
- Known no-weather targets (N=50, dock 185 nm, 10 kn) from the corrected model, e.g. W90 MP 96.7 days
  (app currently 163.0). These are the expected values for regression tests against `sim_reference`.
- The synthetic weather in `reproduce_findings.py` is only a mechanism test; use ERA5 for Morven.

## Data handling

Never commit project workbooks, weather ZIPs or API keys (the repo may be public, see README);
`.gitignore` excludes `*.xlsx`, `*.xls` and `*.zip`. The DD scripts and tests read the operations
template from a local, untracked `dd/New_Input_for_simulations.xlsx` (or `$env:SIM_XLSX`).
