# IAAAP Controlled Detonation — Open-Source Video, 3D Plume & Live Telemetry Analysis

**Live Interactive 3D Report (GitHub Pages):** https://mike10010100.github.io/iaaap-plume-analysis/

An end-to-end open-source intelligence (OSINT) and atmospheric dispersion benchmark analyzing the **November 25, 2026 (9:00 a.m. CST / 15:00 UTC)** U.S. Army controlled detonation of ~5 tons (~9,500 lb / ~3,050 kg elemental Pb) of degraded lead azide at the **Iowa Army Ammunition Plant (IAAAP)** in Middletown, Iowa.

## What's Inside
- **Interactive 3D WebGL Atmospheric Plume Model (`index.html`, `app3d.js`)**:
  - 2,400 real 3D Lagrangian trajectories across 43 keyframes (`T+0s → T+4h 00m`) over actual SE Iowa / NE Missouri elevation terrain (`156–241 m MSL`).
  - Switchable ESRI Dark Gray / Satellite ground textures and transparent exposure (`χ/Q`), surface deposition, and sensitivity heatmaps.
  - Camera viewpoint presets including exact **Cam 1 (KWQC `190th Ave` across 3D FAA Tower T3)** and **Cam 2 (KCRG/AP `9856 202nd Ave`)** sightlines, vertical wind-shear cross-section, growing morning PBL ceiling (`285 → 785 m`), and `1,075 m` subsidence inversion lid.
  - Clickable 3D live EPA Xact monitoring towers, 43 EPA air station markers, and live ASOS + Iowa DOT RWIS 3D wind vectors.
- **Dual-Camera Stereo Triangulation (`scripts/build_3d_assets.py`, `data/stereo_triangulation.json`)**:
  - Cross-triangulates **KWQC-TV6 / C-SPAN** (`190th Ave` → FAA Tower `T3` `235 ft` @ `158.3°` SSE + `Δt = 18.8 s` acoustic range) with **KCRG-TV9 / AP** (`9856 202nd Ave` via blue rural 911 marker @ `112.0°` ESE), narrowing the 95% blast-origin uncertainty area **5.8×** (`±250 m` around `40.8099° N, -91.2668° W`) and validating 3D wind shear across three altitude bands (`4.9 → 7.2 → 8.7 m/s`, $R^2 = 0.956–0.999$).
- **3D Dispersion Ensemble, Inverse EPA Bound & Forward Sensitivity (`scripts/run_dispersion_model.py`, `scripts/run_sensitivity_and_inverse.py`)**:
  - 60,000–80,000-particle Lagrangian ensembles driven by observed regional ASOS winds, 12Z KDVN radiosonde, and hourly 15Z–20Z vertical wind shear.
  - Compares live EPA Region 7 Xact telemetry (`AS01–AS04`) against modeled arrival curves to place an inverse upper bound on actual airborne respirable lead escape (`≲ 0.013%–0.047%`, i.e., `>99.95%` containment).
- **Auto-Updating Telemetry Daemon (`fetch_measurements.py`, `.github/workflows/update-telemetry.yml`)**:
  - Pure Python 3 standard-library fetcher polling EPA Region 7 Xact live lead monitors, EPA's 43-station registry, 10 regional ASOS airports, 6 Iowa DOT RWIS highway stations, and Open-Meteo vertical meteorology + regional AQ.
  - Runs automatically every 10 minutes via local `systemd --user` timer (`iaaap-plume-updater.timer`) and every 15 minutes via GitHub Actions, archiving deduplicated `.json.gz` snapshots and pushing `site_data/live_telemetry.json` only when actual measurements change.

## Local Development
```bash
# Run a one-shot live data refresh
python3 fetch_measurements.py

# Serve the interactive 3D report locally
python3 -m http.server 8765
# Open http://localhost:8765/
```
