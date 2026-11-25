#!/usr/bin/env python3
"""
Upgraded 3D Dispersion Sensitivity Sweep & Inverse EPA Xact Upper-Bound Analysis.
Uses:
- Dual-camera stereo-triangulated origin (40.8099N, -91.2668W, +/- 250m)
- Updated observed 15:00-16:00 UTC ASOS wind ramp (KBRL 040@8kt -> 050@13kt, KFSW 010@8kt -> 030@9kt, etc.)
- KCRG+KWQC verified plume rise (dense core 20-140m + buoyant top reaching 200-250m by T+50s)
- High-resolution 5-minute concentration time-series chi(t)/Q right at EPA Xact stations AS01, AS02, AS03, AS04
- Inverse bound on actual airborne Pb release fraction Q_max from live EPA telemetry through 16:10 UTC
- Forward sensitivity sweep over hypothetical release fractions: 0.01%, 0.1%, 1.0%, 5.0% of 3,050 kg Pb inventory
"""
import json, math, csv, time, datetime
import numpy as np

PROJ = '/home/mike10010100/.gemini/antigravity-cli/scratch/iaaap-plume-analysis'

def main():
    t0 = time.time()
    rng = np.random.default_rng(202611252)

    # Total lead inventory in ~9,500 lb (4,310 kg) lead azide Pb(N3)2:
    # Molar mass Pb = 207.2, Pb(N3)2 = 291.24 -> Pb mass fraction = 71.14% -> ~3,066 kg (~3,050 kg) elemental Pb
    TOTAL_PB_KG = 3050.0

    # Stereo-triangulated origin
    LAT0, LON0 = 40.8099, -91.2668
    M_PER_LAT = 111132.0
    M_PER_LON = 111320.0 * math.cos(math.radians(LAT0))

    # 1. Load hourly Open-Meteo vertical profile (15Z..20Z)
    om = json.load(open(f'{PROJ}/data/openmeteo_iaaap.json'))['hourly']
    ELEV_M = 215.0
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

    # 2. Incorporate ACTUAL observed 15:00Z & 15:55Z ASOS surface winds from poll #2!
    # Notice KBRL ramped from 040@8kt (15:00Z) to 050@13kt (15:53Z)!
    def load_asos_window(t_lo, t_hi):
        obs = {}
        with open(f'{PROJ}/data/iem_asos_poll2.csv') as f:
            for r in csv.DictReader(f):
                if t_lo <= r['valid'][11:] <= t_hi and r['drct'] != 'M' and r['sknt'] != 'M':
                    s = r['station']
                    spd = float(r['sknt']) * 0.514444
                    d = float(r['drct'])
                    obs.setdefault(s, []).append((float(r['lat']), float(r['lon']), -spd*math.sin(math.radians(d)), -spd*math.cos(math.radians(d))))
        xy, uv = [], []
        for s, rows in sorted(obs.items()):
            xy.append([(rows[0][1]-LON0)*M_PER_LON, (rows[0][0]-LAT0)*M_PER_LAT])
            uv.append([np.mean([r[2] for r in rows]), np.mean([r[3] for r in rows])])
        return np.array(xy), np.array(uv)

    xy_15z, uv_15z = load_asos_window('14:50', '15:20')
    xy_16z, uv_16z = load_asos_window('15:35', '16:10')

    def idw_uv(x, y, stn_xy, stn_uv):
        dx = x[:, None] - stn_xy[None, :, 0]
        dy = y[:, None] - stn_xy[None, :, 1]
        w = 1.0 / (dx*dx + dy*dy + 8000.0**2)
        w /= w.sum(axis=1, keepdims=True)
        return w @ stn_uv[:, 0], w @ stn_uv[:, 1]

    u0_15, v0_15 = idw_uv(np.array([0.0]), np.array([0.0]), xy_15z, uv_15z)
    u0_16, v0_16 = idw_uv(np.array([0.0]), np.array([0.0]), xy_16z, uv_16z)
    # Bias-correct Open-Meteo 10m wind at 15Z & 16Z to match ACTUAL observed ASOS at IAAAP!
    u_prof[0, 0] = u0_15[0]; v_prof[0, 0] = v0_15[0]
    u_prof[1, 0] = u0_16[0]; v_prof[1, 0] = v0_16[0]
    print(f'Observed ASOS at IAAAP: 15Z={math.hypot(u0_15[0],v0_15[0]):.2f}m/s TO {(math.degrees(math.atan2(u0_15[0],v0_15[0]))+360)%360:.1f}° | '
          f'16Z={math.hypot(u0_16[0],v0_16[0]):.2f}m/s TO {(math.degrees(math.atan2(u0_16[0],v0_16[0]))+360)%360:.1f}°')

    # 3. Load EPA Xact real-time stations AS01..AS04 for exact virtual receptor sampling!
    xact_raw = json.load(open(f'{PROJ}/data/epa_xact_poll2.json'))['features']
    xact_stns = {}
    for f in xact_raw:
        a = f['attributes']
        sid = a['Air_Station_ID']
        dt = datetime.datetime.fromtimestamp(a['Mon_Date_Time']/1000, datetime.timezone.utc)
        xact_stns.setdefault(sid, {'lat': a['Latitude'], 'lon': a['Longitude'], 'obs': []})
        xact_stns[sid]['obs'].append((dt, float(a['Mon_Results'] or 0.0)))
    for sid in xact_stns:
        xact_stns[sid]['obs'].sort()
        xact_stns[sid]['x'] = (xact_stns[sid]['lon'] - LON0) * M_PER_LON
        xact_stns[sid]['y'] = (xact_stns[sid]['lat'] - LAT0) * M_PER_LAT
        xact_stns[sid]['dist_km'] = math.hypot(xact_stns[sid]['x'], xact_stns[sid]['y']) / 1000.0
        xact_stns[sid]['brg'] = (math.degrees(math.atan2(xact_stns[sid]['x'], xact_stns[sid]['y'])) + 360) % 360

    # 4. Run 80,000-particle 3D Lagrangian Respirable (PM10 + PM2.5) + Coarse Ensemble!
    # Wait: for EPA Xact (which measures airborne particulate lead in PM10/TSP air intake!) and health screening,
    # let's specifically track respirable/suspendable lead particulate (PM10 + PM2.5: 60% PM10 2.5-10um, 40% PM2.5 <2.5um)
    # SEPARATELY from ballistic coarse fallout (20-70um) so our release-fraction Q refers unambiguously to SUSPENDED AIRBORNE PM10/PM2.5 LEAD!
    N = 80000
    is_pm25 = rng.random(N) < 0.45
    vs = np.where(is_pm25,
                  10**rng.uniform(np.log10(0.00005), np.log10(0.0007), N),
                  10**rng.uniform(np.log10(0.0010), np.log10(0.015), N))
    p_stick = np.where(is_pm25, 0.06, 0.30)

    # Dual-bunker stereo-constrained initial positions (+/- 240m separation + +/- 250m stereo ellipse!)
    bunker = rng.choice([-120.0, 120.0], size=N, p=[0.35, 0.65])
    x = bunker * math.sin(math.radians(248.0)) + rng.normal(0, 220.0, N)
    y = bunker * math.cos(math.radians(248.0)) + rng.normal(0, 200.0, N)
    # Initial heights calibrated to BOTH KWQC (dense core 20-140m) AND KCRG/AP (buoyant upper rise to 200-250m by T+50s!)
    z = np.where(bunker < 0, rng.uniform(15.0, 110.0, N), rng.uniform(25.0, 235.0, N))

    # 80 meteorological realizations (+/- 6 deg wind uncertainty, +/- 10% speed)
    n_ens = 80
    ens_id = np.arange(N) % n_ens
    ens_dtheta = np.radians(rng.normal(0.0, 6.0, n_ens))[ens_id]
    ens_spd = rng.normal(1.0, 0.09, n_ens)[ens_id]

    # Regional grid (500m) for 1-hr peak, 24-hr avg, and integrated chi/Q screening contours!
    RX_MIN, RX_MAX, R_DX = -72000.0, 12000.0, 500.0
    RY_MIN, RY_MAX, R_DY = -52000.0, 18000.0, 500.0
    RNX, RNY = int((RX_MAX - RX_MIN)/R_DX), int((RY_MAX - RY_MIN)/R_DY)
    chi_Q_reg = np.zeros((RNY, RNX), dtype=np.float64)    # s/m^3 integrated over 4 hours
    peak_15m_Q_reg = np.zeros((RNY, RNX), dtype=np.float64) # peak 15-min avg C/Q (m^-3)!
    cur_15m_Q_reg  = np.zeros((RNY, RNX), dtype=np.float64)

    # Virtual receptors at exact EPA Xact stations AS01..AS04 (5-minute time bins from T+0 to T+180 min -> 36 bins!)
    # Use a 1.5 km x 1.5 km x 50 m receptor sampling box (and also per-ensemble distributions so we get 10th/50th/90th pct!)
    stn_ids = sorted(xact_stns.keys())
    n_bins = 36 # 5-min bins -> 0..180 min (15:00..18:00 UTC)
    rec_chi_ens = np.zeros((len(stn_ids), n_ens, n_bins), dtype=np.float64) # average C/Q (m^-3) in each 5-min bin per ensemble member!
    REC_HALF = 1200.0 # +/- 1.2 km box (2.4 x 2.4 km x 50m = 2.88e8 m^3) for clean Monte Carlo statistics per ensemble member
    REC_VOL = (2*REC_HALF)**2 * 50.0
    Z_BREATH = 50.0
    VOL_REG = R_DX * R_DY * Z_BREATH

    DT = 15.0
    N_STEPS = 960 # 4 hours
    active = np.ones(N, dtype=np.bool_)
    w_all = 1.0 / N
    w_ens = float(n_ens) / N # weight of each particle WITHIN its single ensemble member!

    for step in range(1, N_STEPS + 1):
        t_sec = step * DT
        t_hr = t_sec / 3600.0
        idx_a = np.where(active)[0]
        if len(idx_a) == 0: break
        xa, ya, za = x[idx_a], y[idx_a], z[idx_a]

        h_lo = min(int(t_hr), len(hours_utc) - 2)
        h_frac = min(1.0, t_hr - h_lo)
        u_z = (1 - h_frac)*u_prof[h_lo] + h_frac*u_prof[h_lo+1]
        v_z = (1 - h_frac)*v_prof[h_lo] + h_frac*v_prof[h_lo+1]
        pbl_cur = min(1075.0, max(320.0, (1 - h_frac)*pbl_hr[h_lo] + h_frac*pbl_hr[h_lo+1]))

        u_mod = np.interp(za, z_grid, u_z)
        v_mod = np.interp(za, z_grid, v_z)

        # Blend observed 15Z -> 16Z spatial surface ASOS anomaly
        w16 = min(1.0, t_hr)
        us15, vs15 = idw_uv(xa, ya, xy_15z, uv_15z)
        us16, vs16 = idw_uv(xa, ya, xy_16z, uv_16z)
        du_sfc = (1-w16)*(us15 - u0_15[0]) + w16*(us16 - u0_16[0])
        dv_sfc = (1-w16)*(vs15 - v0_15[0]) + w16*(vs16 - v0_16[0])
        w_sfc = np.exp(-za / 120.0)
        u_m = u_mod + w_sfc * du_sfc
        v_m = v_mod + w_sfc * dv_sfc

        cd, sd = np.cos(ens_dtheta[idx_a]), np.sin(ens_dtheta[idx_a])
        sf = ens_spd[idx_a]
        u_r = sf * (u_m*cd + v_m*sd)
        v_r = sf * (-u_m*sd + v_m*cd)

        conv_ramp = min(1.0, t_hr / 2.0)
        Kh = 18.0 + 38.0 * conv_ramp
        Kz = (3.5 + 14.5 * conv_ramp) * np.clip(1.0 - 0.7*(za/1075.0)**2, 0.15, 1.0)
        xn = xa + u_r*DT + rng.normal(0.0, math.sqrt(2*Kh*DT), len(idx_a))
        yn = ya + v_r*DT + rng.normal(0.0, math.sqrt(2*Kh*DT), len(idx_a))
        zn = za - vs[idx_a]*DT + rng.normal(0.0, np.sqrt(2*Kz*DT))

        # Upper lid reflection
        abv = zn > pbl_cur
        if np.any(abv):
            zn = np.where(abv, pbl_cur - (zn - pbl_cur)*0.85, zn)
            zn = np.clip(zn, -50.0, 1075.0)

        # Ground contact
        hg = zn <= 0.0
        if np.any(hg):
            st = hg & (rng.random(len(idx_a)) < p_stick[idx_a])
            zn[hg & (~st)] = np.abs(zn[hg & (~st)])
            if np.any(st):
                active[idx_a[st]] = False

        x[idx_a] = xn; y[idx_a] = yn; z[idx_a] = zn

        # Accumulate breathing-zone (0-50m) concentrations
        in_bz = active[idx_a] & (zn > 0.0) & (zn <= Z_BREATH)
        if np.any(in_bz):
            xb, yb = xn[in_bz], yn[in_bz]
            eb = ens_id[idx_a[in_bz]]
            ixr = ((xb - RX_MIN)/R_DX).astype(np.int32)
            iyr = ((yb - RY_MIN)/R_DY).astype(np.int32)
            ok = (ixr >= 0) & (ixr < RNX) & (iyr >= 0) & (iyr < RNY)
            if np.any(ok):
                c_step = (w_all * DT) / VOL_REG # s/m^3 contribution
                np.add.at(chi_Q_reg, (iyr[ok], ixr[ok]), c_step)
                np.add.at(cur_15m_Q_reg, (iyr[ok], ixr[ok]), c_step / 900.0) # avg C/Q over 15 min (m^-3)

            # Virtual EPA Xact station sampling (first 180 min -> 36 x 5-min bins)
            b_idx = int((t_sec - 1) // 300)
            if b_idx < n_bins:
                for si, sid in enumerate(stn_ids):
                    sx, sy = xact_stns[sid]['x'], xact_stns[sid]['y']
                    hit = (np.abs(xb - sx) <= REC_HALF) & (np.abs(yb - sy) <= REC_HALF)
                    if np.any(hit):
                        # Each step adds (w_ens * DT / 300s) / REC_VOL -> avg C/Q (m^-3) over 5-min bin per ensemble member!
                        np.add.at(rec_chi_ens[si, :, b_idx], eb[hit], (w_ens * (DT / 300.0)) / REC_VOL)

        if step % 60 == 0: # Every 15 minutes, record max 15-min C/Q
            peak_15m_Q_reg = np.maximum(peak_15m_Q_reg, cur_15m_Q_reg)
            cur_15m_Q_reg.fill(0.0)

        out = active[idx_a] & ((xn < RX_MIN - 15000) | (xn > RX_MAX + 5000) | (yn < RY_MIN - 15000) | (yn > RY_MAX + 5000))
        if np.any(out):
            active[idx_a[out]] = False

    # 5. Analyze modeled vs observed EPA Xact curves AND invert for upper-bound airborne Pb release Q_max!
    # Units check:
    #   If Q = 1 kg Pb = 1e9 ug Pb released as suspendable PM10/PM2.5:
    #   Concentration C (ug/m^3) = Q_ug * (C/Q in m^-3) = (Q_kg * 1e9) * (C/Q)!
    #   24-hr average concentration C_24hr (ug/m^3) = Q_ug * (chi_Q in s/m^3) / 86400 s!
    print('\n=== VIRTUAL EPA XACT RECEPTOR ANALYSIS (Ensemble across 80 wind realizations) ===')
    receptor_summary = {}
    for si, sid in enumerate(stn_ids):
        st = xact_stns[sid]
        # Ensemble statistics of 5-min avg C/Q (m^-3) over time
        mean_ts = rec_chi_ens[si].mean(axis=0)
        p50_ts  = np.percentile(rec_chi_ens[si], 50, axis=0)
        p90_ts  = np.percentile(rec_chi_ens[si], 90, axis=0)
        p25_ts  = np.percentile(rec_chi_ens[si], 25, axis=0)
        # Peak 5-min C/Q per ensemble member
        ens_peaks = rec_chi_ens[si].max(axis=1)
        hit_prob = float((ens_peaks > 0).mean() * 100.0)
        med_peak = float(np.median(ens_peaks))
        mean_peak = float(np.mean(ens_peaks))
        p10_peak = float(np.percentile(ens_peaks[ens_peaks > 0], 10)) if np.any(ens_peaks > 0) else 0.0
        # Arrival window (minutes post-15:00Z where mean_ts > 10% of max)
        t_bins = np.arange(n_bins) * 5.0 + 2.5
        if mean_ts.max() > 0:
            pk_t = float(t_bins[np.argmax(mean_ts)])
            w_bins = t_bins[mean_ts >= 0.1 * mean_ts.max()]
            win_str = f'T+{w_bins[0]:.0f}..{w_bins[-1]:.0f}m (peak T+{pk_t:.0f}m)'
        else:
            pk_t, win_str = None, 'No hit'

        # Observed post-blast max in 15:00-16:10 UTC (from poll #2)
        post_obs = [(t.strftime('%H:%M'), v) for t, v in st['obs'] if t.hour >= 15]
        pre_obs  = [(t.strftime('%H:%M'), v) for t, v in st['obs'] if t.hour < 15]
        obs_post_max = max([v for _, v in post_obs], default=0.0)
        obs_pre_max  = max([v for _, v in pre_obs], default=0.0)
        # Effective excess detection ceiling (taking max(obs_post_max, 0.005 ug/m^3 Xact practical floor) for conservative upper bound!)
        c_ceil_ugm3 = max(obs_post_max, 0.005)
        # Inverse Q_max (kg Pb) using median & mean modeled peak C/Q (valid for stations already passed by 16:10 UTC = T+70m!)
        q_max_med_kg = (c_ceil_ugm3 / (med_peak * 1e9)) if med_peak > 0 else None
        q_max_mean_kg = (c_ceil_ugm3 / (mean_peak * 1e9)) if mean_peak > 0 else None

        print(f'  {sid} ({st["dist_km"]:4.1f}km @{st["brg"]:5.1f}°): HitProb={hit_prob:5.1f}% | Window={win_str:26s} | '
              f'Peak C/Q med={med_peak:.2e} mean={mean_peak:.2e} m^-3 | Obs post-max={obs_post_max:.4f} (pre-max={obs_pre_max:.4f}) ug/m3')
        if q_max_med_kg is not None:
            print(f'      -> Inverse Bound (at C_ceil={c_ceil_ugm3:.3f} ug/m3): Q_max <= {q_max_med_kg:.3f} kg Pb ({q_max_med_kg/TOTAL_PB_KG*100:.4f}% of 3,050 kg inventory!) [mean-based: <= {q_max_mean_kg:.3f} kg / {q_max_mean_kg/TOTAL_PB_KG*100:.4f}%]')

        receptor_summary[sid] = {
            'lat': st['lat'], 'lon': st['lon'], 'dist_km': round(st['dist_km'], 2), 'bearing_deg': round(st['brg'], 1),
            'hit_prob_pct': round(hit_prob, 1), 'peak_time_min': pk_t,
            'peak_C_over_Q_med': med_peak, 'peak_C_over_Q_mean': mean_peak,
            'ts_min': t_bins.tolist(), 'mean_C_over_Q': mean_ts.tolist(),
            'p50_C_over_Q': p50_ts.tolist(), 'p90_C_over_Q': p90_ts.tolist(), 'p25_C_over_Q': p25_ts.tolist(),
            'obs_post_max_ugm3': obs_post_max, 'obs_pre_max_ugm3': obs_pre_max,
            'obs_series': [[(t - datetime.datetime(2026,11,25,15,0,0,tzinfo=datetime.timezone.utc)).total_seconds()/60.0, v] for t, v in st['obs']],
            'q_max_med_kg': round(q_max_med_kg, 4) if q_max_med_kg else None,
            'f_max_med_pct': round(q_max_med_kg/TOTAL_PB_KG*100, 5) if q_max_med_kg else None,
        }

    # 6. Forward sensitivity sweep table: max downwind distance (km & mi) exceeding key screening levels for 0.01%, 0.1%, 1.0%, 5.0% Pb release!
    rny, rnx = chi_Q_reg.shape
    rx_c = RX_MIN + (np.arange(rnx) + 0.5) * R_DX
    ry_c = RY_MIN + (np.arange(rny) + 0.5) * R_DY
    RXG, RYG = np.meshgrid(rx_c, ry_c)
    DIST_KM = np.hypot(RXG, RYG) / 1000.0

    # Benchmarks:
    #   - 0.15 ug/m^3 (24-hr avg equivalent to EPA NAAQS rolling 3-mo standard; super conservative acute benchmark)
    #   - 0.50 ug/m^3 (24-hr avg WHO/EPA emergency screening reference)
    #   - 50.0 ug/m^3 (15-min peak vs OSHA 8-hr PEL)
    #   - 150.0 ug/m^3 (15-min peak vs DOE PAC-1 / mild transient irritation threshold for inorganic Pb)
    fracs = [0.0001, 0.001, 0.01, 0.05] # 0.01%, 0.1%, 1.0%, 5.0%
    print('\n=== FORWARD RELEASE-FRACTION SENSITIVITY SWEEP (Total Pb Inventory = 3,050 kg) ===')
    sweep_rows = []
    for f in fracs:
        q_kg = TOTAL_PB_KG * f
        q_ug = q_kg * 1e9
        c24 = chi_Q_reg * q_ug / 86400.0     # 24-hr equivalent average (ug/m^3)
        c15 = peak_15m_Q_reg * q_ug          # 15-min peak concentration (ug/m^3)
        def max_km(arr, thr):
            m = arr >= thr
            return float(DIST_KM[m].max()) if np.any(m) else 0.0
        def area_km2(arr, thr):
            return float((arr >= thr).sum() * (R_DX * R_DY) / 1e6)
        row = {
            'release_pct': f * 100.0, 'q_pb_kg': round(q_kg, 2),
            'c24_gt_0p15_km': round(max_km(c24, 0.15), 1), 'c24_gt_0p15_km2': round(area_km2(c24, 0.15), 1),
            'c24_gt_0p50_km': round(max_km(c24, 0.50), 1), 'c24_gt_0p50_km2': round(area_km2(c24, 0.50), 1),
            'c15_gt_50_km':   round(max_km(c15, 50.0), 1), 'c15_gt_150_km': round(max_km(c15, 150.0), 1),
            'max_c24_5mi_ugm3': round(float(c24[(DIST_KM >= 7.5) & (DIST_KM <= 8.5)].max()), 3),
            'max_c15_5mi_ugm3': round(float(c15[(DIST_KM >= 7.5) & (DIST_KM <= 8.5)].max()), 2),
            'max_c24_20mi_ugm3': round(float(c24[(DIST_KM >= 31.5) & (DIST_KM <= 33.0)].max()), 4),
            'max_c15_20mi_ugm3': round(float(c15[(DIST_KM >= 31.5) & (DIST_KM <= 33.0)].max()), 2),
        }
        sweep_rows.append(row)
        print(f"  f={f*100:5.2f}% ({q_kg:6.2f} kg Pb) | 24h>=0.15ug/m3: {row['c24_gt_0p15_km']:5.1f}km ({row['c24_gt_0p15_km2']:6.1f}km²) | "
              f"24h>=0.50: {row['c24_gt_0p50_km']:5.1f}km | 15m>=50: {row['c15_gt_50_km']:5.1f}km | 15m>=150(PAC-1): {row['c15_gt_150_km']:5.1f}km | "
              f"@5mi: 24h={row['max_c24_5mi_ugm3']:.3f} / 15m={row['max_c15_5mi_ugm3']:.1f} ug/m3")

    np.savez_compressed(
        f'{PROJ}/data/sensitivity_results.npz',
        LAT0=LAT0, LON0=LON0, M_PER_LAT=M_PER_LAT, M_PER_LON=M_PER_LON,
        RX_MIN=RX_MIN, RX_MAX=RX_MAX, R_DX=R_DX, RY_MIN=RY_MIN, RY_MAX=RY_MAX, R_DY=R_DY,
        chi_Q_reg=chi_Q_reg, peak_15m_Q_reg=peak_15m_Q_reg, TOTAL_PB_KG=TOTAL_PB_KG
    )
    json.dump({'receptors': receptor_summary, 'sweep': sweep_rows}, open(f'{PROJ}/data/sensitivity_and_inverse.json', 'w'), indent=2)
    print(f'\nCompleted in {time.time()-t0:.2f}s! Saved data/sensitivity_results.npz & data/sensitivity_and_inverse.json')

if __name__ == '__main__':
    main()
