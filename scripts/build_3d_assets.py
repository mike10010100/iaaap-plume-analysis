#!/usr/bin/env python3
"""
Generate all 3D WebGL scene assets for the GitHub Pages interactive 3D plume viewer:
  1. assets/textures/terrain_dark.jpg  (2048x1600 ESRI Dark Gray + Reference, exact 76x60 km box)
  2. assets/textures/terrain_sat.jpg   (2048x1600 ESRI World Imagery satellite, exact 76x60 km box)
  3. assets/textures/drape_exposure.png    (transparent RGBA log10(chi/Q) heatmap)
  4. assets/textures/drape_deposition.png  (transparent RGBA coarse+fine surface deposition)
  5. assets/textures/drape_sensitivity.png (transparent RGBA 0.01%/0.1%/1.0%/5.0% 15m >= 0.15 ug/m3 contours)
  6. site_data/terrain_dem.json        (48x38 real elevation grid from Open-Meteo Elevation API!)
  7. site_data/plume_3d.json           (2,400 real 3D Lagrangian particles across 48 keyframe timestamps: 0..240 min!)
  8. site_data/scene_static.json       (cameras, FAA tower, stereo ellipse, IAAAP perimeter, towns, vertical profile, sensitivity)
"""
import io, json, math, time, csv
import numpy as np
import requests
from PIL import Image
from scipy.ndimage import gaussian_filter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REPO = '/home/mike10010100/git/iaaap-plume-analysis'
SCRATCH = '/home/mike10010100/.gemini/antigravity-cli/scratch/iaaap-plume-analysis'

# Exact 3D world bounds centered at 3-camera triangulated origin (40.8090N, -91.2657W)
LAT0, LON0 = 40.8090, -91.2657
M_PER_LAT = 111132.0
M_PER_LON = 111320.0 * math.cos(math.radians(LAT0))

# Widened 3D world box in km: x in [-168, +24] (192 km E-W), y in [-132, +24] (156 km N-S)
# Covers from Burlington/Galesburg past Kirksville/Macon/Ottumwa/Quincy so the 5-hour plume fades naturally with zero box cutoff!
WX_MIN, WX_MAX = -168.0, 24.0
WY_MIN, WY_MAX = -132.0, 24.0
LON_MIN = LON0 + (WX_MIN * 1000.0) / M_PER_LON
LON_MAX = LON0 + (WX_MAX * 1000.0) / M_PER_LON
LAT_MIN = LAT0 + (WY_MIN * 1000.0) / M_PER_LAT
LAT_MAX = LAT0 + (WY_MAX * 1000.0) / M_PER_LAT


def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    return (lon_deg + 180.0) / 360.0 * n, (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n


def fetch_texture(url_tmpl, zoom=10, rgba=False):
    x0, y0 = deg2num(LAT_MAX, LON_MIN, zoom)
    x1, y1 = deg2num(LAT_MIN, LON_MAX, zoom)
    ix0, ix1 = int(math.floor(x0)), int(math.floor(x1))
    iy0, iy1 = int(math.floor(y0)), int(math.floor(y1))
    mode = 'RGBA' if rgba else 'RGB'
    bg = (0, 0, 0, 0) if rgba else (20, 24, 30)
    canvas = Image.new(mode, ((ix1 - ix0 + 1) * 256, (iy1 - iy0 + 1) * 256), bg)
    sess = requests.Session()
    sess.headers['User-Agent'] = 'IAAAP-3D-Builder/1.0'
    for ix in range(ix0, ix1 + 1):
        for iy in range(iy0, iy1 + 1):
            r = sess.get(url_tmpl.format(z=zoom, x=ix, y=iy), timeout=15)
            if r.status_code == 200:
                canvas.paste(Image.open(io.BytesIO(r.content)).convert(mode), ((ix - ix0) * 256, (iy - iy0) * 256))
    px0, py0 = (x0 - ix0) * 256.0, (y0 - iy0) * 256.0
    px1, py1 = (x1 - ix0) * 256.0, (y1 - iy0) * 256.0
    crop = canvas.crop((int(px0), int(py0), int(px1), int(py1)))
    return crop.resize((1900, 1500), Image.Resampling.LANCZOS)


def build_textures():
    print('Fetching widened ESRI Dark Gray & Satellite textures (192x156 km) for 3D ground plane...')
    dark_base = fetch_texture("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", zoom=9)
    dark_ref  = fetch_texture("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", zoom=9, rgba=True)
    dark = Image.alpha_composite(dark_base.convert('RGBA'), dark_ref).convert('RGB')
    dark.save(f'{REPO}/assets/textures/terrain_dark.jpg', quality=88)

    sat = fetch_texture("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", zoom=9)
    sat.save(f'{REPO}/assets/textures/terrain_sat.jpg', quality=84)

    # Render exact-aligned transparent RGBA ground drapes for Exposure, Deposition, and Sensitivity!
    d0 = np.load(f'{SCRATCH}/data/dispersion_results.npz')
    ds = np.load(f'{SCRATCH}/data/sensitivity_results.npz')

    def save_drape(grid_2d, gx_min, gx_max, gy_min, gy_max, out_path, mode='log', cmap='inferno', span=4.5, thresh_floor=1e-11):
        # Resample onto 960x780 covering WX_MIN..WX_MAX, WY_MIN..WY_MAX (km)
        W, H = 960, 780
        xs = np.linspace(WX_MIN * 1000.0, WX_MAX * 1000.0, W)
        ys = np.linspace(WY_MAX * 1000.0, WY_MIN * 1000.0, H) # row 0 = North (WY_MAX)
        ny, nx = grid_2d.shape
        dx = (gx_max - gx_min) / nx
        dy = (gy_max - gy_min) / ny
        ix = np.clip(((xs - gx_min) / dx).astype(int), 0, nx - 1)
        iy = np.clip(((ys - gy_min) / dy).astype(int), 0, ny - 1)
        sub = grid_2d[np.ix_(iy, ix)]
        # Zero outside source grid bounds
        out_x = (xs < gx_min) | (xs > gx_max)
        out_y = (ys < gy_min) | (ys > gy_max)
        sub[:, out_x] = 0.0
        sub[out_y, :] = 0.0
        sub = gaussian_filter(sub, 2.2)

        if mode == 'log':
            lg = np.where(sub > thresh_floor, np.log10(np.maximum(sub, 1e-30)), -99.0)
            valid = lg[lg > np.log10(thresh_floor)]
            vmax = np.percentile(valid, 99.5) if len(valid) else -7.0
            vmin = vmax - span
            norm = np.clip((lg - vmin) / (vmax - vmin), 0.0, 1.0)
            rgba = plt.get_cmap(cmap)(norm)
            # Continuous feather to 0.0 alpha at tail + smooth 5% outer border fade (zero cutoff!)
            alpha = np.where(lg <= vmin, 0.0, np.clip((norm ** 0.78) * 0.80, 0.0, 0.80))
            fx = np.minimum(np.linspace(0, 1, W) / 0.05, np.linspace(1, 0, W) / 0.05)
            fy = np.minimum(np.linspace(0, 1, H) / 0.05, np.linspace(1, 0, H) / 0.05)
            edge_fade = np.clip(np.outer(np.clip(fy, 0, 1), np.clip(fx, 0, 1)), 0.0, 1.0)
            rgba[..., 3] = alpha * edge_fade
            Image.fromarray((rgba * 255).astype(np.uint8), mode='RGBA').save(out_path)
        elif mode == 'sensitivity':
            # Discrete colored tiers for 5%, 1%, 0.1%, 0.01% Pb 15-min peak >= 0.15 ug/m3!
            TOTAL = float(ds['TOTAL_PB_KG'])
            rgba = np.zeros((H, W, 4), dtype=np.uint8)
            tiers = [
                (0.05,   (119, 34, 204, 105)),
                (0.01,   (255, 51, 85,  140)),
                (0.001,  (255, 170, 34, 175)),
                (0.0001, (0, 229, 255,  215)),
            ]
            for f, col in tiers:
                thr = 0.15 / (TOTAL * f * 1e9)
                m = sub >= thr
                rgba[m] = col
            Image.fromarray(rgba, mode='RGBA').save(out_path)

    save_drape(d0['air_exp_reg'], float(d0['RX_MIN']), float(d0['RX_MAX']), float(d0['RY_MIN']), float(d0['RY_MAX']),
               f'{REPO}/assets/textures/drape_exposure.png', mode='log', cmap='inferno', span=4.2, thresh_floor=1e-11)
    dep_tot = d0['dep_coarse_reg'] + d0['dep_pm10_reg'] + d0['dep_pm25_reg']
    save_drape(dep_tot, float(d0['RX_MIN']), float(d0['RX_MAX']), float(d0['RY_MIN']), float(d0['RY_MAX']),
               f'{REPO}/assets/textures/drape_deposition.png', mode='log', cmap='magma', span=4.0, thresh_floor=1e-13)
    save_drape(ds['peak_15m_Q_reg'], float(ds['RX_MIN']), float(ds['RX_MAX']), float(ds['RY_MIN']), float(ds['RY_MAX']),
               f'{REPO}/assets/textures/drape_sensitivity.png', mode='sensitivity')
    print('Saved all 5 aligned textures to assets/textures/')


def build_dem():
    print('Fetching widened real 3D elevation DEM grid (36x30 = 1080 pts) from Open-Meteo Elevation API...')
    NX, NY = 36, 30
    xs = np.linspace(WX_MIN, WX_MAX, NX)
    ys = np.linspace(WY_MAX, WY_MIN, NY) # row 0 = North (matches Three.js PlaneGeometry!)
    lats_all, lons_all = [], []
    for y in ys:
        for x in xs:
            lats_all.append(round(LAT0 + (y * 1000.0) / M_PER_LAT, 4))
            lons_all.append(round(LON0 + (x * 1000.0) / M_PER_LON, 4))
    elevs = []
    sess = requests.Session()
    for i in range(0, len(lats_all), 90):
        la_s = ','.join(map(str, lats_all[i:i+90]))
        lo_s = ','.join(map(str, lons_all[i:i+90]))
        batch = None
        for attempt in range(3):
            r = sess.get(f'https://api.open-meteo.com/v1/elevation?latitude={la_s}&longitude={lo_s}', timeout=15).json()
            if 'elevation' in r:
                batch = [round(float(v), 1) for v in r['elevation']]
                break
            time.sleep(1.0)
        if batch is None:
            batch = [215.0] * len(lats_all[i:i+90])
        elevs.extend(batch)
        time.sleep(0.35)
    dem = {
        'nx': NX, 'ny': NY,
        'wx_min_km': WX_MIN, 'wx_max_km': WX_MAX, 'wy_min_km': WY_MIN, 'wy_max_km': WY_MAX,
        'min_elev_m': min(elevs), 'max_elev_m': max(elevs), 'ref_elev_m': 215.0,
        'elev_m': elevs
    }
    json.dump(dem, open(f'{REPO}/site_data/terrain_dem.json', 'w'), separators=(',', ':'))
    print(f'Saved terrain_dem.json: {NX}x{NY}, elev range {dem["min_elev_m"]}m - {dem["max_elev_m"]}m MSL')


def build_3d_particles():
    print('Simulating 2,400 real 3D Lagrangian particles across 49 keyframe timestamps (0 -> 240 min)...')
    rng = np.random.default_rng(202611253)
    om = json.load(open(f'{REPO}/data/openmeteo_iaaap.json'))['hourly']
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

    # Bias-correct 15Z & 16Z surface to observed ASOS
    u_prof[0, 0], v_prof[0, 0] = -2.21, -3.44
    u_prof[1, 0], v_prof[1, 0] = -3.11, -3.73

    N_C, N_10, N_25 = 700, 850, 850
    N = N_C + N_10 + N_25
    regime = np.concatenate([np.zeros(N_C, dtype=np.int8), np.ones(N_10, dtype=np.int8), np.full(N_25, 2, dtype=np.int8)])
    # Cam 3 (Wilson Drone) two-stage ballistic-collapse physics:
    # First 400 coarse particles represent dark soil/gel ballistic jets peaking at 111m (N) / 159m (S) at T+4.5s
    # then collapsing straight back onto the IAAAP pad by T+10-15s!
    is_ballistic = np.zeros(N, dtype=np.bool_)
    is_ballistic[:400] = True
    vs = np.concatenate([
        10**rng.uniform(np.log10(0.035), np.log10(0.30), N_C),
        10**rng.uniform(np.log10(0.0012), np.log10(0.016), N_10),
        10**rng.uniform(np.log10(0.00005), np.log10(0.0006), N_25),
    ])
    p_stick = np.where(regime == 0, 1.0, np.where(regime == 1, 0.30, 0.06))

    # 3-Camera verified Dual-Bunker layout: 311 m separation along 208.6 deg (SSW-NNE diagonal magazine loop!)
    # North bunker (-155.5 m, smaller 35%) -> South bunker (+155.5 m, larger 65%), fired simultaneously (33 ms apart)
    bunker = rng.choice([-155.5, 155.5], size=N, p=[0.35, 0.65])
    x = bunker * math.sin(math.radians(208.6)) + rng.normal(0.0, 35.0, N)
    y = bunker * math.cos(math.radians(208.6)) + rng.normal(0.0, 35.0, N)
    z = rng.uniform(5.0, 22.0, N)

    # Stage 1 ballistic apex (111m North, 159m South at T+4.5s) and rapid gravitational collapse speed (T+5..15s)
    z_apex = np.where(bunker < 0, rng.uniform(55.0, 111.0, N), rng.uniform(70.0, 159.0, N))
    w_fall_ball = z_apex / rng.uniform(5.5, 9.5, N)

    # Stage 2 stabilized white smoke/vapor cap (levels off flat by T+25-30s at 15-108m N / 20-130m S + 10% wisps to 175m!)
    w_buoy = np.where(bunker < 0, rng.uniform(0.8, 6.8, N), rng.uniform(1.2, 8.6, N))
    w_buoy = np.where((regime == 0) & (~is_ballistic), w_buoy * 0.65, w_buoy)
    hi_wisp = (regime >= 1) & (bunker > 0) & (rng.random(N) < 0.10)
    w_buoy[hi_wisp] = rng.uniform(8.6, 12.2, hi_wisp.sum())
    dth_ens = np.radians(rng.normal(0.0, 5.5, N))
    spd_ens = rng.normal(1.0, 0.08, N)

    # Dense early keyframes (0, 5, 10, 15, 20, 30s capturing exact Cam 3 ballistic peak & collapse!) + 5-hr regional sweep!
    kf_sec = [
        0, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 780, 900, 1080, 1200,
        1500, 1800, 2100, 2400, 2700, 3000, 3300, 3600, 4200, 4800, 5400, 6000, 6600,
        7200, 7800, 8400, 9000, 9600, 10200, 10800, 11400, 12000, 12600, 13200, 13800, 14400,
        15300, 16200, 17100, 18000
    ]
    kf_set = set(kf_sec)
    frames_xyz = []
    frames_pbl = []

    def snap(pbl_val):
        # Pack as flat int16-friendly rounded lists: x_10m (meters/10), y_10m (meters/10), z_m (meters, 0 = deposited!)
        arr = np.empty(N * 3, dtype=np.int32)
        arr[0::3] = np.rint(x / 10.0).astype(np.int32)
        arr[1::3] = np.rint(y / 10.0).astype(np.int32)
        arr[2::3] = np.where(active, np.rint(np.clip(z, 0, 1200)).astype(np.int32), 0)
        frames_xyz.append(arr.tolist())
        frames_pbl.append(round(float(pbl_val), 1))

    active = np.ones(N, dtype=np.bool_)
    snap(pbl_hr[0])

    # Step dt=2.5s for first 30s (resolving T+4.5s ballistic peak & T+12s collapse!), dt=5s to 120s, then dt=15s to 18,000s!
    t_cur = 0.0
    while t_cur < 18000.0:
        dt = 2.5 if t_cur < 30.0 else (5.0 if t_cur < 120.0 else 15.0)
        t_cur += dt
        t_hr = t_cur / 3600.0
        idx_a = np.where(active)[0]
        if len(idx_a) > 0:
            xa, ya, za = x[idx_a], y[idx_a], z[idx_a]
            h_lo = min(int(t_hr), len(hours_utc) - 2)
            hf = min(1.0, t_hr - h_lo)
            u_z = (1 - hf)*u_prof[h_lo] + hf*u_prof[h_lo+1]
            v_z = (1 - hf)*v_prof[h_lo] + hf*v_prof[h_lo+1]
            pbl_c = min(1075.0, max(285.0, (1 - hf)*pbl_hr[h_lo] + hf*pbl_hr[h_lo+1]))
            um = np.interp(za, z_grid, u_z)
            vm = np.interp(za, z_grid, v_z)
            cd, sd = np.cos(dth_ens[idx_a]), np.sin(dth_ens[idx_a])
            sf = spd_ens[idx_a]
            ur = sf * (um*cd + vm*sd)
            vr = sf * (-um*sd + vm*cd)
            conv = min(1.0, t_hr / 2.0)
            Kh = 16.0 + 36.0 * conv
            Kz = (3.2 + 14.0 * conv) * np.clip(1.0 - 0.7*(za/1075.0)**2, 0.15, 1.0)

            ball_m = is_ballistic[idx_a]
            if t_cur <= 5.0:
                vz_part = np.where(ball_m, (z_apex[idx_a] - 12.0) / 5.0, w_buoy[idx_a] * math.exp(-t_cur / 14.0) - vs[idx_a])
                # Ballistic ejecta experiences minimal horizontal wind drift during vertical ejection
                u_eff = np.where(ball_m, ur * 0.25, ur)
                v_eff = np.where(ball_m, vr * 0.25, vr)
            elif t_cur <= 25.0:
                vz_part = np.where(ball_m, -w_fall_ball[idx_a], w_buoy[idx_a] * math.exp(-t_cur / 14.0) - vs[idx_a])
                u_eff = np.where(ball_m, ur * 0.35, ur)
                v_eff = np.where(ball_m, vr * 0.35, vr)
            else:
                buoy_term = w_buoy[idx_a] * math.exp(-t_cur / 14.0) if t_cur <= 90.0 else 0.0
                vz_part = np.where(ball_m, -w_fall_ball[idx_a], buoy_term - vs[idx_a])
                u_eff, v_eff = ur, vr

            xn = xa + u_eff*dt + rng.normal(0.0, math.sqrt(2*Kh*dt), len(idx_a))
            yn = ya + v_eff*dt + rng.normal(0.0, math.sqrt(2*Kh*dt), len(idx_a))
            zn = za + vz_part*dt + np.where(ball_m, rng.normal(0.0, 2.0, len(idx_a)), rng.normal(0.0, np.sqrt(2*Kz*dt)))
            abv = zn > pbl_c
            if np.any(abv):
                zn = np.where(abv, pbl_c - (zn - pbl_c)*0.85, zn)
                zn = np.clip(zn, -50.0, 1075.0)
            hg = zn <= 0.0
            if np.any(hg):
                st = hg & (rng.random(len(idx_a)) < p_stick[idx_a])
                zn[hg & (~st)] = np.abs(zn[hg & (~st)])
                if np.any(st):
                    active[idx_a[st]] = False
                    zn[st] = 0.0
            x[idx_a], y[idx_a], z[idx_a] = xn, yn, zn
        t_int = int(round(t_cur))
        if abs(t_cur - t_int) < 1e-6 and t_int in kf_set:
            h_lo = min(int(t_cur/3600.0), len(hours_utc) - 2)
            hf = min(1.0, (t_cur/3600.0) - h_lo)
            snap(min(1075.0, max(285.0, (1 - hf)*pbl_hr[h_lo] + hf*pbl_hr[h_lo+1])))

    out = {
        'n_particles': N,
        'coord_scale_m': 10.0, # divide x,y by 100 to get km! z is in meters AGL
        'times_sec': kf_sec,
        'pbl_m': frames_pbl,
        'regimes': regime.tolist(), # 0=coarse, 1=pm10, 2=pm25
        'frames': frames_xyz
    }
    p_path = f'{REPO}/site_data/plume_3d.json'
    json.dump(out, open(p_path, 'w'), separators=(',', ':'))
    import os
    print(f'Saved {p_path} ({os.path.getsize(p_path)/1024:.0f} KB, {len(kf_sec)} frames x {N} particles)')


def build_scene_static():
    stereo = json.load(open(f'{REPO}/data/stereo_triangulation.json'))
    kin = json.load(open(f'{REPO}/data/calibrated_kinematics.json'))
    met = json.load(open(f'{REPO}/data/met_summary.json'))
    towns = json.load(open(f'{REPO}/data/town_impacts.json'))
    si = json.load(open(f'{REPO}/data/sensitivity_and_inverse.json'))
    iaaap_raw = json.load(open(f'{REPO}/data/iaaap_osm.json'))[0]['geojson']

    def ll2km(lat, lon):
        return round((lon - LON0)*M_PER_LON/1000.0, 3), round((lat - LAT0)*M_PER_LAT/1000.0, 3)

    # Simplify IAAAP polygon outer ring to km
    rings = []
    if iaaap_raw['type'] == 'Polygon':
        rings = [iaaap_raw['coordinates'][0]]
    elif iaaap_raw['type'] == 'MultiPolygon':
        rings = [p[0] for p in iaaap_raw['coordinates']]
    iaaap_km = [[ll2km(pt[1], pt[0]) for pt in r[::2]] for r in rings]

    town_pts = []
    for t in towns:
        xk, yk = ll2km(t['lat'], t['lon'])
        if WX_MIN <= xk <= WX_MAX and WY_MIN <= yk <= WY_MAX:
            town_pts.append({**t, 'x_km': xk, 'y_km': yk})

    c1 = stereo['cam1_kwqc']; c2 = stereo['cam2_kcrg_ap']; c3 = stereo['cam3_wilson_drone']
    tri = stereo['triangulated_origin']
    bn = tri['bunker_north']; bs = tri['bunker_south']
    t3 = (40.84110, -91.28298)
    static = {
        'origin': {'lat': LAT0, 'lon': LON0, 'elev_m': 215.0, **tri},
        'world_bounds_km': {'x_min': WX_MIN, 'x_max': WX_MAX, 'y_min': WY_MIN, 'y_max': WY_MAX},
        'world_bounds_ll': {'lat_min': LAT_MIN, 'lat_max': LAT_MAX, 'lon_min': LON_MIN, 'lon_max': LON_MAX},
        'cam1_kwqc': {**c1, 'x_km': ll2km(c1['lat'], c1['lon'])[0], 'y_km': ll2km(c1['lat'], c1['lon'])[1]},
        'cam2_kcrg': {**c2, 'x_km': ll2km(c2['lat'], c2['lon'])[0], 'y_km': ll2km(c2['lat'], c2['lon'])[1]},
        'cam3_wilson': {**c3, 'x_km': ll2km(c3['lat'], c3['lon'])[0], 'y_km': ll2km(c3['lat'], c3['lon'])[1]},
        'bunkers': {
            'north': {**bn, 'x_km': ll2km(bn['lat'], bn['lon'])[0], 'y_km': ll2km(bn['lat'], bn['lon'])[1]},
            'south': {**bs, 'x_km': ll2km(bs['lat'], bs['lon'])[0], 'y_km': ll2km(bs['lat'], bs['lon'])[1]},
        },
        'tower_t3': {'lat': t3[0], 'lon': t3[1], 'height_m': 71.6, 'x_km': ll2km(*t3)[0], 'y_km': ll2km(*t3)[1]},
        'iaaap_rings_km': iaaap_km,
        'towns': town_pts,
        'kinematics': kin,
        'met_profile_15z': met['profile_15z'],
        'inversion_cap_m': 1075.0,
        'receptors_model': {k: {fk: fv for fk, fv in v.items() if fk != 'obs_series'} for k, v in si['receptors'].items()},
        'sensitivity_sweep': si['sweep'],
    }
    json.dump(static, open(f'{REPO}/site_data/scene_static.json', 'w'), separators=(',', ':'))
    print('Saved site_data/scene_static.json')


if __name__ == '__main__':
    t0 = time.time()
    build_textures()
    build_dem()
    build_3d_particles()
    build_scene_static()
    print(f'All 3D assets generated cleanly in {time.time()-t0:.1f}s!')
