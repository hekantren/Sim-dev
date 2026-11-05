# Morven DD – verktøy

Filer til due diligence-notatet «Due diligence: W90 mot konkurrenter på Morven».
Alt er Python 3.10+ med `numpy`, `pandas`, `openpyxl` (og `cdsapi>=0.7.7` for nedlasting).

| Fil | Hva den gjør |
| --- | --- |
| `sim_reference.py` | Referanseimplementasjon av rettelsene: fem-blokk-syklus (pre / lasting × k / mid / installasjon × k / post), parallell lasting med lengste tid og strengeste grense, værsjekk over hele operasjonen, felles startdatoer, dag-først-datoer, alfafaktor og vindhøyde. Leser samme Excel-mal som appen. |
| `reproduce_findings.py` | Kjører appens egne funksjoner (uten Streamlit) på malen og viser feilene: transitter per enhet, rader som faller ut, dato-tolkningen, og app mot rettet logikk på syntetisk vær. |
| `era5_morven_download.py` | Laster ned ERA5-tidsserier (10 m- og 100 m-vind, vindkast, Hs, bølgeperiode og -retning) for Morven North/South, lekterpunkter og verft. ZIP-filene kan lastes rett opp i appen (2.1b). |
| `metocean_screening.py` | Månedlig arbeidsbarhet per grensesett: starttime-sjekk mot helt vindu, ventetid, vindskjær 10→100 m. |

## Rekkefølge

```bash
pip install "cdsapi>=0.7.7" numpy pandas openpyxl tabulate

# 1) se feilene i dagens app
python reproduce_findings.py --app w90_monte_carlo_v8_simapp.py \
    --xlsx New_Input_for_simulations.xlsx --weather

# 2) værdata (krever CDS-token i ~/.cdsapirc og akseptert lisens)
python era5_morven_download.py --list
python era5_morven_download.py --sites morven_north morven_south forth_mouth moray_firth yard_dundee

# 3) screening av arbeidsbarhet
python metocean_screening.py era5_morven/morven_north.zip --out screening_north --hub 150
```

## Porte rettelsene inn i appen

1. Erstatt `build_mp_timeline`, `build_w90_timeline`, `build_sequential_wtg_timeline` og
   `build_jacket_timeline` med `read_sheet` + `build_plan`. Jacket: `barge_fed=True,
   infield_rows=(8,)`. JUV turbiner: `per_unit_extra=(16, 17, 18)` til malen er rettet.
2. Erstatt `wait`/`_wait` med vindu-sjekken i `simulate` (`window=True`).
3. Trekk startindekser én gang (`seasonal_start_indices` eller `year_start_indices`) og send dem
   til alle tilfeller; bruk egen RNG for varigheter.
4. Les startdato med `parse_start_date` (dag først), eller bruk en datovelger.
5. Lasterader i fundamentfasen skal værsjekkes mot lastestedets vær, som i turbinfasen.

Den syntetiske været i `reproduce_findings.py` er bare for å teste mekanismer. Bruk ERA5 for Morven.
