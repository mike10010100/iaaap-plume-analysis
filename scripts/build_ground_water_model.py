#!/usr/bin/env python3
"""
Compute quantitative Ground, Surface Dust, Topsoil, and Watershed Runoff/Dilution models
for the Nov 25, 2026 IAAAP Controlled Detonation from real open-source inputs:
  - 60,000-particle 3D Lagrangian surface deposition grids (dispersion_results.npz)
  - USGS National Hydrography Dataset (NHD) & WBD HUC12 (070801071005 Long Creek)
  - Federal USGS/EPA Water Quality Portal (WQP) historical local lead-in-water baseline
  - EPA Region 7 live Xact inverse airborne mass bound (AS02 <= 0.028% / 0.85 kg Pb)
"""
import json, math
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter

REPO = Path('/home/mike10010100/git/iaaap-plume-analysis')
SCRATCH = Path('/home/mike10010100/.gemini/antigravity-cli/scratch/iaaap-plume-analysis')

TOTAL_PB_KG = 3050.0
FT2_TO_M2 = 0.09290304
# Iowa loess topsoil bulk density ~1,400 kg/m^3 -> 1 cm depth = 14.0 kg soil per m^2
SOIL_KG_PER_M2_1CM = 14.0


def build():
    d = np.load(SCRATCH / 'data' / 'dispersion_results.npz')
    lat0, lon0 = float(d['LAT0']), float(d['LON0'])
    m_lat, m_lon = float(d['M_PER_LAT']), float(d['M_PER_LON'])

    # Smooth slightly (sigma=1.0 cell) so discrete Monte Carlo bin gaps don't distort centerline estimates
    dep_loc = gaussian_filter(d['dep_tot_loc'].astype(np.float64), sigma=1.0)
    dep_reg = gaussian_filter(
        (d['dep_coarse_reg'] + d['dep_pm10_reg'] + d['dep_pm25_reg']).astype(np.float64),
        sigma=1.0,
    )

    lx_min, lx_max = float(d['LX_MIN']), float(d['LX_MAX'])
    ly_min, ly_max = float(d['LY_MIN']), float(d['LY_MAX'])
    rx_min, rx_max = float(d['RX_MIN']), float(d['RX_MAX'])
    ry_min, ry_max = float(d['RY_MIN']), float(d['RY_MAX'])

    xs_l = np.linspace(lx_min, lx_max, dep_loc.shape[1])
    ys_l = np.linspace(ly_min, ly_max, dep_loc.shape[0])
    xs_r = np.linspace(rx_min, rx_max, dep_reg.shape[1])
    ys_r = np.linspace(ry_min, ry_max, dep_reg.shape[0])

    def sample_dep(xm, ym):
        """Return deposited fraction per m^2 at (xm, ym) meters from origin."""
        if lx_min + 500 <= xm <= lx_max - 500 and ly_min + 500 <= ym <= ly_max - 500:
            ix = int(np.argmin(np.abs(xs_l - xm)))
            iy = int(np.argmin(np.abs(ys_l - ym)))
            # Local 3x3 peak average to capture plume centerline cleanly
            y0, y1 = max(0, iy - 1), min(dep_loc.shape[0], iy + 2)
            x0, x1 = max(0, ix - 1), min(dep_loc.shape[1], ix + 2)
            return float(np.mean(dep_loc[y0:y1, x0:x1]))
        if rx_min <= xm <= rx_max and ry_min <= ym <= ry_max:
            ix = int(np.argmin(np.abs(xs_r - xm)))
            iy = int(np.argmin(np.abs(ys_r - ym)))
            y0, y1 = max(0, iy - 1), min(dep_reg.shape[0], iy + 2)
            x0, x1 = max(0, ix - 1), min(dep_reg.shape[1], ix + 2)
            return float(np.mean(dep_reg[y0:y1, x0:x1]))
        return 0.0

    def to_metrics(dep_m2, q_kg):
        ug_ft2 = dep_m2 * q_kg * 1e9 * FT2_TO_M2
        ppm_1cm = (dep_m2 * q_kg * 1e6) / SOIL_KG_PER_M2_1CM
        return round(ug_ft2, 5), round(ppm_1cm, 6)

    # 1. Centerline profile along SW plume axis (searching +-8 deg around 234 deg for peak centerline deposition)
    centerline = []
    for dist_km in [0.8, 1.5, 2.5, 4.0, 6.0, 8.0, 12.0, 16.0, 20.0, 30.0, 40.0, 60.0]:
        best_dep = 0.0
        for ang in range(224, 244, 2):
            xm = dist_km * 1000.0 * math.sin(math.radians(ang))
            ym = dist_km * 1000.0 * math.cos(math.radians(ang))
            best_dep = max(best_dep, sample_dep(xm, ym))
        u_028, p_028 = to_metrics(best_dep, TOTAL_PB_KG * 0.0002777)
        u_01, p_01 = to_metrics(best_dep, TOTAL_PB_KG * 0.001)
        u_1, p_1 = to_metrics(best_dep, TOTAL_PB_KG * 0.01)
        u_5, p_5 = to_metrics(best_dep, TOTAL_PB_KG * 0.05)
        zone = (
            "Inside IAAAP Perimeter" if dist_km <= 2.5
            else ("5-Mile Evac / Buffer Zone" if dist_km <= 8.0
                  else ("20-Mile Shelter-in-Place Zone" if dist_km <= 32.0 else "Regional Far-Field"))
        )
        centerline.append({
            "dist_km": dist_km,
            "dist_mi": round(dist_km * 0.621371, 2),
            "zone": zone,
            "dep_frac_m2": best_dep,
            "wipe_ug_ft2_bound": u_028,
            "soil_ppm_1cm_bound": p_028,
            "wipe_ug_ft2_01pct": u_01,
            "wipe_ug_ft2_1pct": u_1,
            "soil_ppm_1cm_1pct": p_1,
            "wipe_ug_ft2_5pct": u_5,
        })

    # 2. Community-specific modeled surface dust & topsoil loading
    towns_raw = json.loads((REPO / 'data' / 'town_impacts.json').read_text())
    town_surface = []
    for t in towns_raw:
        xm = (t['lon'] - lon0) * m_lon
        ym = (t['lat'] - lat0) * m_lat
        dep_m2 = sample_dep(xm, ym)
        u_028, p_028 = to_metrics(dep_m2, TOTAL_PB_KG * 0.0002777)
        u_1, p_1 = to_metrics(dep_m2, TOTAL_PB_KG * 0.01)
        town_surface.append({
            "town": t['town'],
            "lat": t['lat'],
            "lon": t['lon'],
            "dist_km": t['dist_km'],
            "dist_mi": round(t['dist_km'] * 0.621371, 1),
            "hit_prob_pct": t.get('ens_prob_pct', 0.0),
            "dep_frac_m2": dep_m2,
            "wipe_ug_ft2_bound": u_028,
            "soil_ppm_1cm_bound": p_028,
            "wipe_ug_ft2_1pct": u_1,
            "soil_ppm_1cm_1pct": p_1,
        })

    # 3. Watershed & WQP baseline integration
    wqp = json.loads((REPO / 'data' / 'wqp_lead_baseline.json').read_text())
    hydro = json.loads((REPO / 'data' / 'watershed_hydrology.json').read_text())

    out = {
        "total_pb_kg": TOTAL_PB_KG,
        "airborne_bound_pct": 0.02777,
        "airborne_bound_kg": 0.85,
        "onsite_retained_pct": 99.97,
        "onsite_retained_kg": 3049.15,
        "blast_huc12": {
            "huc12": hydro["blast_huc12"].get("huc12", "070801071005"),
            "name": hydro["blast_huc12"].get("name", "Long Creek"),
            "areasqkm": hydro["blast_huc12"].get("areasqkm", 61.1),
            "receiving_river": "Skunk River at Augusta (USGS 05474000, 6.2 km S) -> Mississippi River (~15 km SE)",
            "superfund_context": "IAAAP has been an EPA National Priorities List (Superfund) site since 1990 with long-term surface-water and groundwater monitoring along Brush Creek, Long Creek, and Spring Creek.",
        },
        "epa_sampling_campaign": {
            "pre_blast_total": 1216,
            "soil_samples": 650,
            "dust_samples": 297,
            "water_samples": 210,
            "air_samples": 59,
            "personnel_deployed": 300,
            "lab_turnaround_note": "EPA Xact air telemetry updates in near-real-time; physical soil, surface-wipe, water, and air-cassette ICP-MS lab samples typically require 24-72+ hours for validated upload.",
            "independent_testing": "Purdue University (Dr. Ellen Wells, School of Health Sciences) is also offering free before/after residential soil & dust lead testing for Iowa residents.",
        },
        "regulatory_benchmarks": {
            "dust_floor_classic_ug_ft2": 10.0,
            "dust_floor_action_2025_ug_ft2": 5.0,
            "dust_sill_action_2025_ug_ft2": 40.0,
            "dust_sill_classic_ug_ft2": 100.0,
            "soil_residential_rsl_ppm": 200.0,
            "soil_residential_multi_ppm": 100.0,
            "soil_iowa_bg_range_ppm": [15.0, 30.0],
            "water_epa_action_ugL": 15.0,
            "water_epa_lcri_2027_ugL": 10.0,
            "water_aquatic_chronic_hard_ugL": 5.5,
        },
        "municipal_water_sources": [
            {
                "city": "Burlington, IA",
                "source": "80% Mississippi River (upstream of Skunk River confluence) + 20% Pleistocene aquifer wells",
                "exposure_pathway": "Upwind of air plume & upstream of Skunk River outfall",
                "risk_tier": "Minimal / Upstream",
            },
            {
                "city": "Middletown & Danville, IA",
                "source": "Local groundwater / rural municipal wells",
                "exposure_pathway": "Upwind (N/NW) of SW plume; deep/cased wells insulated from surface dust",
                "risk_tier": "Minimal / Upwind",
            },
            {
                "city": "Fort Madison, IA",
                "source": "Mississippi Alluvial Aquifer wells treated via Reverse Osmosis (RO)",
                "exposure_pathway": "East edge of SW plume; RO treatment removes >95-99% of dissolved/particulate lead",
                "risk_tier": "Very Low (RO Protected)",
            },
            {
                "city": "West Point & Donnellson, IA",
                "source": "Municipal groundwater wells & Rathbun Regional Water",
                "exposure_pathway": "Under SW air plume (modeled surface dust <=0.2 ug/ft^2); cased wells shielded from airborne dust",
                "risk_tier": "Very Low (Airborne Bound)",
            },
            {
                "city": "Keokuk, IA",
                "source": "100% Mississippi River surface water (downstream of Skunk & Des Moines confluences)",
                "exposure_pathway": "Massive Mississippi River dilution (~40,000-60,000 cfs) + conventional coagulation/filtration removes particulate lead",
                "risk_tier": "Very Low (High Dilution)",
            },
            {
                "city": "Private Rural Wells & Livestock Ponds (0-5 mi SW)",
                "source": "Shallow private wells, cisterns, open farm ponds along Long/Brush Creek & Augusta",
                "exposure_pathway": "Primary focus of EPA's 210 water samples; open ponds/cisterns could catch settled dust if uncovered, while Long Creek runoff is the main medium-term watchpoint",
                "risk_tier": "Monitor / Test (EPA Sampling)",
            },
        ],
        "wqp_historical_baseline": wqp["sites"],
        "centerline_profile": centerline,
        "town_surface": town_surface,
    }

    out_path = REPO / 'data' / 'ground_water_analysis.json'
    out_path.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(f"Saved {out_path} ({out_path.stat().st_size/1024:.1f} KB)")
    for row in centerline:
        print(f"  {row['dist_km']:4.1f} km ({row['dist_mi']:4.1f} mi): dep={row['dep_frac_m2']:.2e}/m² | 0.028%: {row['wipe_ug_ft2_bound']:.4f} µg/ft² (+{row['soil_ppm_1cm_bound']:.5f} ppm) | 1%: {row['wipe_ug_ft2_1pct']:.2f} µg/ft²")


if __name__ == '__main__':
    build()
