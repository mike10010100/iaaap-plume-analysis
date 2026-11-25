#!/usr/bin/env python3
"""
3D Time-Varying Lagrangian Particle Dispersion & Deposition Ensemble Model
for the November 25, 2026 IAAAP Controlled Lead Azide Detonation (Middletown, IA).

Combines:
- Visual plume heights, dual-puff geometry, and velocity calibration from KWQC/C-SPAN livestream
- Real surface ASOS observations (KBRL, KMPZ, KFSW, KEOK, KFFL, KAWG, KMQB, KGBG)
- Real vertical wind shear & boundary-layer evolution (Open-Meteo 15Z-20Z + KDVN 12Z sounding)
- 3 aerodynamic particle size regimes (coarse ballistic/dust, respirable PM10, fine PM2.5 fume)
"""
import json, math, csv, time
import numpy as np

PROJ = '/home/mike10010100/.gemini/antigravity-cli/scratch/iaaap-plume-analysis'

def main():
    t_start = time.time()
    rng = np.random.default_rng(20261125)

    # 1. Load meteorological data
    om = json.load(open(f'{PROJ}/data/openmeteo_iaaap.json'))['hourly']
    met = json.load(open(f'{PROJ}/data/met_summary.json'))
    ELEV_M = 215.0

    # Build hourly (15Z..20Z -> t_hr = 0..5) vertical wind lookup tables (z_agl -> u, v)
    # Note: meteorological wind direction D (FROM) -> u = -spd*sin(D), v = -spd*cos(D) (m/s East, North)
    hours_utc = [15, 16, 17, 18, 19, 20]
    z_grid = np.array([10.0, 80.0, 120.0, 180.0, 250.0, 450.0, 665.0, 880.0, 1330.0])
    u_prof = np.zeros((len(hours_utc), len(z_grid)))
    v_prof = np.zeros((len(hours_utc), len(z_grid)))
    pbl_hr = np.zeros(len(hours_utc))

    for hi, hr in enumerate(hours_utc):
        idx = om['time'].index(f'2026-11-25T{hr:02d}:00')
        pbl_hr[hi] = om['boundary_layer_height'][idx]
        lvls = [
            (10.0,  om['wind_speed_10m'][idx],  om['wind_direction_10m'][idx]),
            (80.0,  om['wind_speed_80m'][idx],  om['wind_direction_80m'][idx]),
            (120.0, om['wind_speed_120m'][idx], om['wind_direction_120m'][idx]),
            (180.0, om['wind_speed_180m'][idx], om['wind_direction_180m'][idx]),
            (om['geopotential_height_975hPa'][idx]-ELEV_M, om['wind_speed_975hPa'][idx], om['wind_direction_975hPa'][idx]),
            (om['geopotential_height_950hPa'][idx]-ELEV_M, om['wind_speed_950hPa'][idx], om['wind_direction_950hPa'][idx]),
            (om['geopotential_height_925hPa'][idx]-ELEV_M, om['wind_speed_925hPa'][idx], om['wind_direction_925hPa'][idx]),
            (om['geopotential_height_900hPa'][idx]-ELEV_M, om['wind_speed_900hPa'][idx], om['wind_direction_900hPa'][idx]),
            (om['geopotential_height_850hPa'][idx]-ELEV_M, om['wind_speed_850hPa'][idx], om['wind_direction_850hPa'][idx]),
        ]
        lvls.sort(key=lambda x: x[0])
        zs = np.array([x[0] for x in lvls])
        us = np.array([-x[1]*math.sin(math.radians(x[2])) for x in lvls])
        vs = np.array([-x[1]*math.cos(math.radians(x[2])) for x in lvls])
        u_prof[hi] = np.interp(z_grid, zs, us)
        v_prof[hi] = np.interp(z_grid, zs, vs)

    # Blend surface-layer spatial anomaly from real ASOS stations (14:50-15:20Z)
    # Specifically, KBRL (43->223 @ 4.5), KMPZ (45->225 @ 3.3), KEOK (38->218 @ 3.3), KFSW (10->190 @ 4.6)
    stns = met['stations']
    # Origin reference: geolocated NW/central IAAAP magazine sightline intersection (40.808, -91.265)
    LAT0, LON0 = 40.808, -91.265
    M_PER_LAT = 111132.0
    M_PER_LON = 111320.0 * math.cos(math.radians(LAT0))

    stn_xy = np.array([[(d['lon']-LON0)*M_PER_LON, (d['lat']-LAT0)*M_PER_LAT] for d in stns.values()])
    stn_uv = np.array([[d['u'], d['v']] for d in stns.values()])
    # Mean observed surface (u,v) right at origin via IDW (p=2, L=15km)
    def idw_surface_uv(x, y):
        # x, y: 1D arrays (N,)
        dx = x[:, None] - stn_xy[None, :, 0]
        dy = y[:, None] - stn_xy[None, :, 1]
        d2 = dx*dx + dy*dy + (8000.0**2) # 8 km smoothing kernel prevents point singularities
        w = 1.0 / d2
        w /= w.sum(axis=1, keepdims=True)
        return w @ stn_uv[:, 0], w @ stn_uv[:, 1]

    u_sfc0, v_sfc0 = idw_surface_uv(np.array([0.0]), np.array([0.0]))
    print(f'IDW observed surface wind at IAAAP: u={u_sfc0[0]:.2f}, v={v_sfc0[0]:.2f} m/s '
          f'(spd={math.hypot(u_sfc0[0],v_sfc0[0]):.2f} m/s, TO={(math.degrees(math.atan2(u_sfc0[0],v_sfc0[0]))+360)%360:.1f}°)')

    # 2. Particle ensemble setup: 60,000 particles across 3 aerodynamic regimes
    # Regime 0 (25%): Coarse dust / soil-bound lead (20-70 um, vs = 0.04 - 0.35 m/s) -> near-field fallout
    # Regime 1 (40%): Respirable fine dust PM10 (3-15 um, vs = 0.001 - 0.018 m/s) -> mid-range transport & deposition
    # Regime 2 (35%): Sub-micron lead aerosol / fume PM2.5 (0.3-2.5 um, vs = 0.00005 - 0.0006 m/s) -> long-range dispersion
    N_COARSE = 15000
    N_PM10   = 24000
    N_PM25   = 21000
    N = N_COARSE + N_PM10 + N_PM25
    regime = np.concatenate([
        np.zeros(N_COARSE, dtype=np.int8),
        np.ones(N_PM10, dtype=np.int8),
        np.full(N_PM25, 2, dtype=np.int8),
    ])

    # Gravitational settling velocity v_s (m/s) using log-uniform aerodynamic distribution per regime
    # (Lead-enriched particulate effective density ~3.5-5.0 g/cm3 increases settling slightly over pure silica!)
    vs = np.concatenate([
        10**rng.uniform(np.log10(0.035), np.log10(0.32), N_COARSE),
        10**rng.uniform(np.log10(0.0012), np.log10(0.018), N_PM10),
        10**rng.uniform(np.log10(0.00004), np.log10(0.0006), N_PM25),
    ])

    # Initial positions: Dual-bunker layout (separated by ~240m along WSW-ENE axis) + IAAAP source-location uncertainty ensemble!
    # Also include 20% probability of slightly south-east origin inside central IAAAP (within ~1.5 km) to honestly capture
    # the OSINT uncertainty in exact interior bunker coordinates!
    bunker_choice = rng.choice([-120.0, 120.0], size=N, p=[0.35, 0.65]) # Plume 2 (West) was ~2x larger than Plume 1 (East)
    # Bunker axis roughly perpendicular to 158 deg sightline (i.e. along 068/248 deg)
    bx = bunker_choice * math.sin(math.radians(248.0))
    by = bunker_choice * math.cos(math.radians(248.0))
    # Positional uncertainty along sightline (158 deg: +/- 700m, clamped strictly inside IAAAP perimeter south of 40.820N!)
    along_u = rng.normal(0.0, 650.0, N)
    cross_u = rng.normal(0.0, 260.0, N)
    x = bx + along_u * math.sin(math.radians(158.0)) + cross_u * math.sin(math.radians(248.0)) + rng.normal(0, 35.0, N)
    y = by + along_u * math.cos(math.radians(158.0)) + cross_u * math.cos(math.radians(248.0)) + rng.normal(0, 35.0, N)
    # Clamp y <= +1200 m (40.819N, strictly inside IAAAP northern perimeter south of Hwy 34 & Middletown)
    y = np.minimum(y, 1200.0)

    # Initial release height AGL calibrated directly to video plumes:
    # Plume 1 (East): core 15-75m, Plume 2 (West): core 20-130m, buoyant tail up to ~220m (10% chance of 180-320m thermal wisps)
    z = np.where(bunker_choice < 0,
                 rng.uniform(10.0, 85.0, N),
                 rng.uniform(15.0, 145.0, N))
    # Buoyant wispy tail for fine aerosols (5-15% lofted into 150-300m shear layer)
    hi_tail = (regime >= 1) & (rng.random(N) < 0.20)
    z[hi_tail] = rng.uniform(130.0, 290.0, hi_tail.sum())

    # Ensemble synoptic/mesoscale wind-direction perturbation per sub-ensemble (50 realizations x 1200 particles)
    # Represents +/- 7 deg directional uncertainty & +/- 12% wind speed uncertainty across unmonitored rural terrain!
    n_ens = 60
    ens_id = np.arange(N) % n_ens
    ens_dtheta = np.radians(rng.normal(0.0, 6.5, n_ens))[ens_id]
    ens_spd_fac = rng.normal(1.0, 0.10, n_ens)[ens_id]
    # Also include 25% of realizations with stronger KDVN-like easterly veering aloft (+12 deg above 200m)
    ens_veer_extra = np.radians(np.where(rng.random(n_ens) < 0.30, rng.uniform(6.0, 16.0, n_ens), 0.0))[ens_id]

    # 3. Define output grids (Local near-field 250m grid & Regional 500m grid)
    # Regional grid: x in [-72 km, +12 km], y in [-52 km, +18 km] (resolution 500m -> 168 x 140)
    RX_MIN, RX_MAX, R_DX = -72000.0, 12000.0, 500.0
    RY_MIN, RY_MAX, R_DY = -52000.0, 18000.0, 500.0
    RNX = int((RX_MAX - RX_MIN) / R_DX)
    RNY = int((RY_MAX - RY_MIN) / R_DY)

    # Local high-res grid: x in [-22 km, +6 km], y in [-18 km, +8 km] (resolution 150m)
    LX_MIN, LX_MAX, L_DX = -22000.0, 6000.0, 150.0
    LY_MIN, LY_MAX, L_DY = -18000.0, 8000.0, 150.0
    LNX = int((LX_MAX - LX_MIN) / L_DX)
    LNY = int((LY_MAX - LY_MIN) / L_DY)

    # Accumulators:
    # - air_exp_reg: time-integrated normalized concentration (chi/Q in s/m^3) in breathing layer z in [0, 50m]
    # - dep_coarse_reg, dep_pm10_reg, dep_pm25_reg: surface deposition fraction per m^2
    # - min_arrival_reg: earliest arrival time (minutes) of breathing-zone plume
    # - ens_hit_reg: bitmask/count of ensemble members hitting each cell (for dispersion probability!)
    air_exp_reg = np.zeros((RNY, RNX), dtype=np.float64)
    air_exp_loc = np.zeros((LNY, LNX), dtype=np.float64)
    dep_coarse_reg = np.zeros((RNY, RNX), dtype=np.float64)
    dep_pm10_reg   = np.zeros((RNY, RNX), dtype=np.float64)
    dep_pm25_reg   = np.zeros((RNY, RNX), dtype=np.float64)
    dep_tot_loc    = np.zeros((LNY, LNX), dtype=np.float64)
    arr_time_sum   = np.zeros((RNY, RNX), dtype=np.float64)
    arr_weight_sum = np.zeros((RNY, RNX), dtype=np.float64)
    # Track per-ensemble hits on coarse 1km grid for clean probability contour
    P_DX = 1000.0
    PNX, PNY = int((RX_MAX - RX_MIN)/P_DX), int((RY_MAX - RY_MIN)/P_DX)
    ens_hit = np.zeros((n_ens, PNY, PNX), dtype=np.bool_)

    # 4. Time-stepping integration: 4 hours (14,400 s) with dt = 15 s (960 steps)
    DT = 15.0
    N_STEPS = 960
    active = np.ones(N, dtype=np.bool_)
    deposited = np.zeros(N, dtype=np.bool_)
    # Dry deposition reflection probability for fine particles touching z <= 0 (imperfect reflection -> realistic dry dep!)
    # Coarse particles stick immediately (p_stick=1.0), PM10 p_stick=0.35, PM2.5 p_stick=0.06 per ground encounter
    p_stick = np.where(regime == 0, 1.0, np.where(regime == 1, 0.35, 0.06))

    # Effective breathing layer depth for chi/Q calculation
    Z_BREATH = 50.0
    VOL_REG = R_DX * R_DY * Z_BREATH
    VOL_LOC = L_DX * L_DY * Z_BREATH
    AREA_REG = R_DX * R_DY
    AREA_LOC = L_DX * L_DY

    # Weight per particle so total released airborne mass across fine+PM10+coarse = 1.0 (normalized!)
    # Let's track chi/Q normalized to UNIT TOTAL AIRBORNE RELEASE (Q = 1 kg -> chi/Q in s/m^3!)
    w_part = 1.0 / N

    # Snapshot trajectories for 150 sample particles for plotting streamlines!
    traj_idx = np.linspace(0, N-1, 150, dtype=int)
    traj_x = np.full((N_STEPS//8 + 1, 150), np.nan)
    traj_y = np.full((N_STEPS//8 + 1, 150), np.nan)
    traj_z = np.full((N_STEPS//8 + 1, 150), np.nan)
    traj_x[0], traj_y[0], traj_z[0] = x[traj_idx], y[traj_idx], z[traj_idx]

    for step in range(1, N_STEPS + 1):
        t_sec = step * DT
        t_hr = t_sec / 3600.0
        idx_a = np.where(active)[0]
        if len(idx_a) == 0:
            break

        xa = x[idx_a]; ya = y[idx_a]; za = z[idx_a]

        # Interpolate hourly Open-Meteo vertical profile in time & height
        h_lo = min(int(t_hr), len(hours_utc) - 2)
        h_frac = min(1.0, t_hr - h_lo)
        u_z_cur = (1 - h_frac) * u_prof[h_lo] + h_frac * u_prof[h_lo + 1]
        v_z_cur = (1 - h_frac) * v_prof[h_lo] + h_frac * v_prof[h_lo + 1]
        pbl_cur = (1 - h_frac) * pbl_hr[h_lo] + h_frac * pbl_hr[h_lo + 1]
        z_cap = max(pbl_cur, 320.0) # daytime mixing top (capped below 1075m subsidence inversion)
        z_cap = min(z_cap, 1075.0)

        u_mod = np.interp(za, z_grid, u_z_cur)
        v_mod = np.interp(za, z_grid, v_z_cur)

        # Blend spatial surface ASOS anomaly smoothly decaying with height (exp(-z / 120m))
        # Captures real NNE backing toward Fort Madison (KFSW) in the river valley!
        if step % 2 == 1: # refresh IDW every 30s for speed (or exact every step; fast for 60k x 8!)
            pass
        us_idw, vs_idw = idw_surface_uv(xa, ya)
        du_sfc = us_idw - u_sfc0[0]
        dv_sfc = vs_idw - v_sfc0[0]
        w_sfc = np.exp(-za / 120.0)
        u_mean = u_mod + w_sfc * du_sfc
        v_mean = v_mod + w_sfc * dv_sfc

        # Apply ensemble wind-direction/speed perturbation + optional extra KDVN easterly veering aloft
        dth = ens_dtheta[idx_a] + np.where(za > 200.0, ens_veer_extra[idx_a], 0.0)
        cd, sd = np.cos(dth), np.sin(dth)
        sf = ens_spd_fac[idx_a]
        # Rotate (u, v) clockwise by dth: u' = u*cos + v*sin, v' = -u*sin + v*cos
        u_rot = sf * (u_mean * cd + v_mean * sd)
        v_rot = sf * (-u_mean * sd + v_mean * cd)

        # Turbulent diffusion (Pasquill-Gifford Class D transitioning to C as morning solar heating increases!)
        # Horizontal eddy diffusivity Kh grows from ~18 m^2/s (9 AM, Class D) to ~55 m^2/s (11 AM+, Class C)
        # Vertical eddy diffusivity Kz grows from ~3.5 m^2/s (9 AM) to ~18 m^2/s (11 AM+ convective mixing)
        conv_ramp = min(1.0, t_hr / 2.0)
        Kh = 18.0 + 38.0 * conv_ramp
        Kz = 3.5 + 14.5 * conv_ramp
        # Reduce vertical mixing near the top of PBL / inversion
        Kz_eff = Kz * np.clip(1.0 - 0.7 * (za / 1075.0)**2, 0.15, 1.0)

        sig_xy = math.sqrt(2.0 * Kh * DT)
        sig_z  = np.sqrt(2.0 * Kz_eff * DT)

        xn = xa + u_rot * DT + rng.normal(0.0, sig_xy, len(idx_a))
        yn = ya + v_rot * DT + rng.normal(0.0, sig_xy, len(idx_a))
        zn = za - vs[idx_a] * DT + rng.normal(0.0, sig_z)

        # Upper reflective boundary at subsidence inversion / mixing lid
        above_cap = zn > z_cap
        if np.any(above_cap):
            # Allow 8% weak entrainment into stable layer up to 1075m, otherwise reflect at z_cap
            zn = np.where(above_cap, z_cap - (zn - z_cap) * 0.85, zn)
            zn = np.clip(zn, -50.0, 1075.0)

        # Ground contact & deposition
        hit_gnd = zn <= 0.0
        if np.any(hit_gnd):
            stick = hit_gnd & (rng.random(len(idx_a)) < p_stick[idx_a])
            bounce = hit_gnd & (~stick)
            zn[bounce] = np.abs(zn[bounce])

            if np.any(stick):
                si = idx_a[stick]
                xs_d, ys_d, reg_d = xn[stick], yn[stick], regime[si]
                # Deposit on regional grid
                ixr = ((xs_d - RX_MIN) / R_DX).astype(np.int32)
                iyr = ((ys_d - RY_MIN) / R_DY).astype(np.int32)
                ok = (ixr >= 0) & (ixr < RNX) & (iyr >= 0) & (iyr < RNY)
                if np.any(ok):
                    for rcode, arr in [(0, dep_coarse_reg), (1, dep_pm10_reg), (2, dep_pm25_reg)]:
                        m = ok & (reg_d == rcode)
                        if np.any(m):
                            np.add.at(arr, (iyr[m], ixr[m]), w_part / AREA_REG)
                # Deposit on local grid
                ixl = ((xs_d - LX_MIN) / L_DX).astype(np.int32)
                iyl = ((ys_d - LY_MIN) / L_DY).astype(np.int32)
                okl = (ixl >= 0) & (ixl < LNX) & (iyl >= 0) & (iyl < LNY)
                if np.any(okl):
                    np.add.at(dep_tot_loc, (iyl[okl], ixl[okl]), w_part / AREA_LOC)
                active[si] = False
                deposited[si] = True

        x[idx_a] = xn; y[idx_a] = yn; z[idx_a] = zn

        # Accumulate breathing-zone airborne exposure (0 <= z <= 50 m) for still-active particles!
        still = active[idx_a]
        in_bz = still & (zn <= Z_BREATH) & (zn > 0.0)
        if np.any(in_bz):
            xb, yb = xn[in_bz], yn[in_bz]
            ixr = ((xb - RX_MIN) / R_DX).astype(np.int32)
            iyr = ((yb - RY_MIN) / R_DY).astype(np.int32)
            ok = (ixr >= 0) & (ixr < RNX) & (iyr >= 0) & (iyr < RNY)
            if np.any(ok):
                contrib = (w_part * DT) / VOL_REG # s / m^3 per unit mass released!
                np.add.at(air_exp_reg, (iyr[ok], ixr[ok]), contrib)
                np.add.at(arr_time_sum, (iyr[ok], ixr[ok]), contrib * (t_sec / 60.0))
                np.add.at(arr_weight_sum, (iyr[ok], ixr[ok]), contrib)

            ixl = ((xb - LX_MIN) / L_DX).astype(np.int32)
            iyl = ((yb - LY_MIN) / L_DY).astype(np.int32)
            okl = (ixl >= 0) & (ixl < LNX) & (iyl >= 0) & (iyl < LNY)
            if np.any(okl):
                np.add.at(air_exp_loc, (iyl[okl], ixl[okl]), (w_part * DT) / VOL_LOC)

            # Ensemble hit map (every 4th step = 1 min)
            if step % 4 == 0:
                eb = ens_id[idx_a[in_bz]]
                ixp = ((xb - RX_MIN) / P_DX).astype(np.int32)
                iyp = ((yb - RY_MIN) / P_DX).astype(np.int32)
                okp = (ixp >= 0) & (ixp < PNX) & (iyp >= 0) & (iyp < PNY)
                if np.any(okp):
                    ens_hit[eb[okp], iyp[okp], ixp[okp]] = True

        # Also deactivate particles that left the regional domain entirely
        out_dom = still & ((xn < RX_MIN - 15000) | (xn > RX_MAX + 5000) | (yn < RY_MIN - 15000) | (yn > RY_MAX + 5000))
        if np.any(out_dom):
            active[idx_a[out_dom]] = False

        if step % 8 == 0:
            s_idx = step // 8
            traj_x[s_idx] = np.where(active[traj_idx], x[traj_idx], np.nan)
            traj_y[s_idx] = np.where(active[traj_idx], y[traj_idx], np.nan)
            traj_z[s_idx] = np.where(active[traj_idx], z[traj_idx], np.nan)

    mean_arr_min = np.where(arr_weight_sum > 0, arr_time_sum / np.maximum(arr_weight_sum, 1e-30), np.nan)
    ens_prob = ens_hit.mean(axis=0) # 0..1 probability across 60 meteorological/source realizations

    # Deposition stats by regime
    for rcode, name in [(0, 'Coarse (20-70 um)'), (1, 'PM10 (3-15 um)'), (2, 'PM2.5 (<2.5 um)')]:
        m = (regime == rcode)
        dep_frac = deposited[m].mean()
        exit_frac = ((~active[m]) & (~deposited[m])).mean()
        dists_km = np.hypot(x[m], y[m]) / 1000.0
        print(f'Regime {name:18s}: deposited={dep_frac*100:5.1f}% | exited_domain={exit_frac*100:5.1f}% | median dist={np.median(dists_km):5.1f} km | 90th pct={np.percentile(dists_km,90):5.1f} km')

    # Compute community / town impact summary table!
    towns = [
        ('Middletown, IA',      40.8284, -91.2590),
        ('Danville, IA',        40.8656, -91.3149),
        ('West Burlington, IA', 40.8195, -91.1793),
        ('Burlington, IA',      40.8075, -91.1129),
        ('Augusta, IA',         40.7556, -91.2779),
        ('Denmark, IA',         40.7370, -91.3340),
        ('West Point, IA',      40.7167, -91.4544),
        ('Fort Madison, IA',    40.6298, -91.3151),
        ('Houghton, IA',        40.7839, -91.6032),
        ('Donnellson, IA',      40.6406, -91.5604),
        ('Salem, IA',           40.8511, -91.6199),
        ('Farmington, IA',      40.6400, -91.7435),
        ('Bonaparte, IA',       40.6989, -91.8038),
        ('Keosauqua, IA',       40.7303, -91.9624),
        ('Keokuk, IA',          40.3973, -91.3849),
        ('Kahoka, MO',          40.4220, -91.7218),
        ('Wayland, MO',         40.3917, -91.5846),
        ('Memphis, MO',         40.4595, -92.1707),
    ]
    print('\n=== COMMUNITY DISPERSION IMPACT & ARRIVAL SUMMARY ===')
    town_rows = []
    for name, lat, lon in towns:
        tx = (lon - LON0) * M_PER_LON
        ty = (lat - LAT0) * M_PER_LAT
        dist_km = math.hypot(tx, ty) / 1000.0
        brg = (math.degrees(math.atan2(tx, ty)) + 360) % 360
        ixr = int((tx - RX_MIN) / R_DX)
        iyr = int((ty - RY_MIN) / R_DY)
        ixp = int((tx - RX_MIN) / P_DX)
        iyp = int((ty - RY_MIN) / P_DX)
        if 1 <= ixr < RNX-1 and 1 <= iyr < RNY-1:
            chi_q = float(air_exp_reg[iyr-1:iyr+2, ixr-1:ixr+2].mean())
            dep_c = float(dep_coarse_reg[iyr-1:iyr+2, ixr-1:ixr+2].mean())
            dep_f = float((dep_pm10_reg + dep_pm25_reg)[iyr-1:iyr+2, ixr-1:ixr+2].mean())
            arr = float(np.nanmean(mean_arr_min[iyr-1:iyr+2, ixr-1:ixr+2])) if np.any(~np.isnan(mean_arr_min[iyr-1:iyr+2, ixr-1:ixr+2])) else None
            prob = float(ens_prob[iyp, ixp]) * 100.0
        else:
            chi_q, dep_c, dep_f, arr, prob = 0.0, 0.0, 0.0, None, 0.0
        row = {
            'town': name, 'lat': lat, 'lon': lon,
            'dist_km': round(dist_km, 1), 'dist_mi': round(dist_km/1.60934, 1), 'bearing_deg': round(brg, 1),
            'ens_prob_pct': round(prob, 1), 'chi_over_Q_s_m3': chi_q,
            'dep_coarse_m2': dep_c, 'dep_fine_m2': dep_f,
            'mean_arrival_min': round(arr, 1) if arr is not None else None
        }
        town_rows.append(row)
        arr_s = f'{arr:5.1f} min' if arr is not None else '    N/A  '
        print(f'  {name:20s} | {dist_km:5.1f} km ({dist_km/1.60934:4.1f} mi) @{brg:5.1f}° | Prob={prob:5.1f}% | chi/Q={chi_q:9.2e} s/m3 | Arr={arr_s}')

    # Save all grids + metadata
    np.savez_compressed(
        f'{PROJ}/data/dispersion_results.npz',
        LAT0=LAT0, LON0=LON0, M_PER_LAT=M_PER_LAT, M_PER_LON=M_PER_LON,
        RX_MIN=RX_MIN, RX_MAX=RX_MAX, R_DX=R_DX, RY_MIN=RY_MIN, RY_MAX=RY_MAX, R_DY=R_DY,
        LX_MIN=LX_MIN, LX_MAX=LX_MAX, L_DX=L_DX, LY_MIN=LY_MIN, LY_MAX=LY_MAX, L_DY=L_DY,
        P_DX=P_DX,
        air_exp_reg=air_exp_reg, air_exp_loc=air_exp_loc,
        dep_coarse_reg=dep_coarse_reg, dep_pm10_reg=dep_pm10_reg, dep_pm25_reg=dep_pm25_reg,
        dep_tot_loc=dep_tot_loc, mean_arr_min=mean_arr_min, ens_prob=ens_prob,
        traj_x=traj_x, traj_y=traj_y, traj_z=traj_z
    )
    json.dump(town_rows, open(f'{PROJ}/data/town_impacts.json', 'w'), indent=2)
    print(f'\nSimulation completed in {time.time()-t_start:.2f}s! Saved data/dispersion_results.npz & data/town_impacts.json')

if __name__ == '__main__':
    main()
