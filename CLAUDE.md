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

Tests (`python -m pip install pytest`; need the local workbook, otherwise they skip):

```powershell
python -m pytest tests -q -rA
python -m pytest tests -q -k "W90 MP"                     # one sheet
python -m pytest "tests/test_dd_findings.py::test_start_date_day_first"
```

`tests/test_dd_findings.py` checks the app against `dd/sim_reference.py`. `test_reference_*` must
always pass; the other tests encode the DD findings and fail until each fix is ported. No linter is
configured.

## Architecture of the app

The app is a single top-to-bottom Streamlit script; sections are delimited by `# ═══` banners and
numbered like the UI (1.x inputs in the sidebar/top, 1.5 workbook upload, 2.x weather, 3–5 run).
Pure functions are defined inline between the UI code, so importing the file runs Streamlit.

Pipeline per case (vessel × loading mode × phase):

1. **Workbook parsing** — `_excel_read` / `_resolve_sheet_name` (tolerates trailing spaces in
   sheet names), then `_read_ops_sheet` normalises a sheet: `N`, `Seq Dur (hrs)` / `Tr (hrs)` →
   lo/hi, `Transit`, `Loading` / `Installation` START/END markers, `Oplim Waves (m)`,
   `Oplim Wind (m/s)` and `Parallell 1/2` flags.
2. **Campaign plan** — `_sheet_blocks` splits a sheet into pre / load / mid / inst / post and
   `_build_plan` (a port of `dd/sim_reference.build_plan`) repeats them per cycle or per unit;
   parallel pairs take the longer duration and stricter limits. `_PLAN_OVERRIDES` holds
   template workarounds per vessel (JUV rows 16–18 per turbine). `_plan_to_timeline` turns the
   plan into the timeline DataFrame. The four `build_*_timeline` functions are thin wrappers
   that keep their old signatures (`build_jacket_timeline` uses `barge_fed=True` and treats
   transit rows after Installation END as in-field moves).
3. **Timeline rows carry everything downstream needs**: `Lo_Hours`, `Hi_Hours`, `Transit`,
   `Wave_Limit`, `Wind_Limit`, `Loading_Weather`, `Weather_Restricted`, `Block`, plus display
   columns (`Sequence` = first sheet N, `Source_Sequences`, `Inventory`, `WTG_Left`, ...).
   Duration and weather simulators read these via `_timeline_step_arrays`; they no longer look
   rows up in the sheet (their `excel_src` / `sheet_name` args are unused). Transit rows get
   `_mode_transit_distance(mode, ...)` / transit speed at simulation time.
4. **Durations** — `add_duration_simulations_w90` / `_sequential` → `_add_duration_simulations`
   (uniform Lo..Hi per row).
5. **Weather** — `load_all_weather` merges project / barge / yard ZIPs; wind is
   `Adjusted_Windspeed = 0.9 × |u10,v10|`. `get_seasonal_eligible_indices` picks start hours around
   the start date and `draw_start_times` draws them **once** per run; the same `start_times` go to
   every case and to both phases (the WTG phase starts at `start_times[i]` + foundation duration),
   while durations use a separate RNG stream (`default_rng([seed, 1])`). `simulate_weather_impacts_foundation`, `simulate_weather_impacts_w90`,
   `simulate_sequential_wtg_weather` step through the timeline and wait for workable weather
   via `_workable_runs` + `_window_start`: a restricted step starts only when its limits hold for
   every hour it touches, and steps start at the exact previous finish time (no rounding). `Loading_Weather` rows use the loading-location
   weather (yard wind-only / barge / project by loading mode). In the foundation phase loading
   rows are not weather-restricted.
6. **Output** — `build_project_summary`, `plot_*`, `build_simulation_excel_export`,
   `render_advanced_weather_intelligence`.

Results are kept in `st.session_state` so that touching widgets after "Run All Simulations" does
not wipe them (a `st.button` is only true on the click rerun).

`htd = 1/24` converts hours to days. `Sim*` matrices and `Seq_Duration_Days` are in days; despite
the name, `Parallel_Branch_*_Hours` also holds days.

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
