#!/usr/bin/env python3
"""
Render publication-quality regional & local atmospheric dispersion heatmaps
(Figure 3 & Figure 4) on real map/satellite tiles, plus an interactive Folium HTML map.
"""
import io, json, math, base64
import numpy as np
import requests
from PIL import Image
from scipy.ndimage import gaussian_filter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patheffects as pe
import folium
from folium.raster_layers import ImageOverlay

PROJ = '/home/mike10010100/.gemini/antigravity-cli/scratch/iaaap-plume-analysis'

def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = np.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = (lon_deg + 180.0) / 360.0 * n
    ytile = (1.0 - np.arcsinh(np.tan(lat_rad)) / np.pi) / 2.0 * n
    return xtile, ytile

def fetch_basemap(lat_min, lat_max, lon_min, lon_max, zoom, url_tmpl):
    x0, y0 = deg2num(lat_max, lon_min, zoom)
    x1, y1 = deg2num(lat_min, lon_max, zoom)
    ix0, ix1 = int(math.floor(x0)), int(math.floor(x1))
    iy0, iy1 = int(math.floor(y0)), int(math.floor(y1))
    canvas = Image.new('RGB', ((ix1 - ix0 + 1) * 256, (iy1 - iy0 + 1) * 256), (25, 30, 38))
    sess = requests.Session()
    sess.headers['User-Agent'] = 'OSINT-Plume-Analysis/1.0'
    for ix in range(ix0, ix1 + 1):
        for iy in range(iy0, iy1 + 1):
            try:
                r = sess.get(url_tmpl.format(z=zoom, x=ix, y=iy), timeout=12)
                if r.status_code == 200:
                    canvas.paste(Image.open(io.BytesIO(r.content)).convert('RGB'), ((ix - ix0) * 256, (iy - iy0) * 256))
            except Exception:
                pass
    def to_px(lat, lon):
        tx, ty = deg2num(lat, lon, zoom)
        return (tx - ix0) * 256.0, (ty - iy0) * 256.0
    # Crop exact requested bbox
    px0, py0 = to_px(lat_max, lon_min)
    px1, py1 = to_px(lat_min, lon_max)
    box = (int(px0), int(py0), int(px1), int(py1))
    cropped = canvas.crop(box)
    def to_px_crop(lat, lon):
        px, py = to_px(lat, lon)
        return px - box[0], py - box[1]
    return cropped, to_px_crop

def range_ring_px(lat0, lon0, miles, to_px):
    km = miles * 1.60934
    angs = np.linspace(0, 2*np.pi, 181)
    lats = lat0 + (km / 111.132) * np.cos(angs)
    lons = lon0 + (km / (111.320 * math.cos(math.radians(lat0)))) * np.sin(angs)
    return to_px(lats, lons)

def main():
    d = np.load(f'{PROJ}/data/dispersion_results.npz')
    LAT0, LON0 = float(d['LAT0']), float(d['LON0'])
    M_PER_LAT, M_PER_LON = float(d['M_PER_LAT']), float(d['M_PER_LON'])
    RX_MIN, RX_MAX, R_DX = float(d['RX_MIN']), float(d['RX_MAX']), float(d['R_DX'])
    RY_MIN, RY_MAX, R_DY = float(d['RY_MIN']), float(d['RY_MAX']), float(d['R_DY'])
    LX_MIN, LX_MAX, L_DX = float(d['LX_MIN']), float(d['LX_MAX']), float(d['L_DX'])
    LY_MIN, LY_MAX, L_DY = float(d['LY_MIN']), float(d['LY_MAX']), float(d['L_DY'])
    P_DX = float(d['P_DX'])

    air_reg = gaussian_filter(d['air_exp_reg'], 1.2)
    dep_c   = gaussian_filter(d['dep_coarse_reg'], 1.0)
    dep_f   = gaussian_filter(d['dep_pm10_reg'] + d['dep_pm25_reg'], 1.3)
    dep_tot_reg = dep_c + dep_f
    arr_min = d['mean_arr_min']
    ens_prob = gaussian_filter(d['ens_prob'] * 100.0, 1.0)
    air_loc = gaussian_filter(d['air_exp_loc'], 1.4)
    dep_loc = gaussian_filter(d['dep_tot_loc'], 1.4)

    epa = json.load(open(f'{PROJ}/data/epa_air_monitors.json'))['features']
    towns = json.load(open(f'{PROJ}/data/town_impacts.json'))
    iaaap_poly = json.load(open(f'{PROJ}/data/iaaap_osm.json'))[0]['geojson']
    met = json.load(open(f'{PROJ}/data/met_summary.json'))

    # Grid centers -> lat/lon
    rny, rnx = air_reg.shape
    rx_c = RX_MIN + (np.arange(rnx) + 0.5) * R_DX
    ry_c = RY_MIN + (np.arange(rny) + 0.5) * R_DY
    RXG, RYG = np.meshgrid(rx_c, ry_c)
    RLAT = LAT0 + RYG / M_PER_LAT
    RLON = LON0 + RXG / M_PER_LON

    pny, pnx = ens_prob.shape
    px_c = RX_MIN + (np.arange(pnx) + 0.5) * P_DX
    py_c = RY_MIN + (np.arange(pny) + 0.5) * P_DX
    PXG, PYG = np.meshgrid(px_c, py_c)
    PLAT = LAT0 + PYG / M_PER_LAT
    PLON = LON0 + PXG / M_PER_LON

    # ============================================================
    # FIGURE 3: WIDENED REGIONAL DISPERSION & DEPOSITION HEATMAP (2-PANEL)
    # ============================================================
    lat_min, lat_max, lon_min, lon_max = 39.85, 41.02, -93.05, -91.02
    # ESRI World Dark Gray Base + Dark Gray Reference (free, zero API key, crisp rivers/highways/counties!)
    esri_dark_base = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
    esri_dark_ref  = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
    base_reg, to_px_r = fetch_basemap(lat_min, lat_max, lon_min, lon_max, 9, esri_dark_base)
    # Fetch crisp road/river/border reference overlay and blend subtly
    def fetch_rgba_overlay(lat_min, lat_max, lon_min, lon_max, zoom, url_tmpl):
        x0, y0 = deg2num(lat_max, lon_min, zoom)
        x1, y1 = deg2num(lat_min, lon_max, zoom)
        ix0, ix1, iy0, iy1 = int(math.floor(x0)), int(math.floor(x1)), int(math.floor(y0)), int(math.floor(y1))
        canvas = Image.new('RGBA', ((ix1-ix0+1)*256, (iy1-iy0+1)*256), (0,0,0,0))
        sess = requests.Session(); sess.headers['User-Agent'] = 'OSINT-Plume-Analysis/1.0'
        for ix in range(ix0, ix1+1):
            for iy in range(iy0, iy1+1):
                try:
                    r = sess.get(url_tmpl.format(z=zoom, x=ix, y=iy), timeout=10)
                    if r.status_code == 200:
                        canvas.paste(Image.open(io.BytesIO(r.content)).convert('RGBA'), ((ix-ix0)*256, (iy-iy0)*256))
                except Exception:
                    pass
        px0, py0 = (x0-ix0)*256, (y0-iy0)*256
        px1, py1 = (x1-ix0)*256, (y1-iy0)*256
        return canvas.crop((int(px0), int(py0), int(px1), int(py1)))
    ref_reg = fetch_rgba_overlay(lat_min, lat_max, lon_min, lon_max, 9, esri_dark_ref)
    base_reg = Image.alpha_composite(base_reg.convert('RGBA'), ref_reg).convert('RGB')
    PX_r, PY_r = to_px_r(RLAT, RLON)
    PPX_r, PPY_r = to_px_r(PLAT, PLON)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(19, 9.2), dpi=160, facecolor='#0b0f14')
    plt.subplots_adjust(wspace=0.08, top=0.89, bottom=0.09, left=0.03, right=0.97)

    # Custom translucent plasma/inferno colormap for log10(chi/Q)
    cmap_air = plt.cm.inferno.copy()
    cmap_air.set_under(alpha=0.0)
    log_air = np.where(air_reg > 1e-11, np.log10(air_reg), -99.0)
    vmax_air = np.percentile(log_air[log_air > -11], 99.5)
    vmin_air = vmax_air - 4.2 # 4+ orders of magnitude dynamic range!

    # --- Panel 1: Breathing-Zone Time-Integrated Airborne Exposure (chi/Q) + EPA Stations ---
    ax1.imshow(base_reg)
    # Smooth filled heatmap (semi-transparent)
    mask_air = np.ma.masked_where(log_air < vmin_air, log_air)
    hm1 = ax1.pcolormesh(PX_r, PY_r, mask_air, cmap='inferno', vmin=vmin_air, vmax=vmax_air, alpha=0.70, shading='auto')
    # Ensemble probability contours (10%, 50%, 85% of 60 realizations)
    cs_p = ax1.contour(PPX_r, PPY_r, ens_prob, levels=[10, 50, 85], colors=['#00e5ff', '#ffffff', '#ffdd44'],
                       linewidths=[1.1, 1.4, 1.3], linestyles=[':', '--', '-'], alpha=0.88)
    ax1.clabel(cs_p, fmt='%d%%', fontsize=7.5, inline=True, colors='white')

    # Range rings (5 mi, 20 mi, 35 mi)
    for mi, col, ls in [(5, '#ff4466', '-'), (20, '#ffaa44', '--'), (35, '#88aacc', ':')]:
        rx_p, ry_p = range_ring_px(LAT0, LON0, mi, to_px_r)
        ax1.plot(rx_p, ry_p, color=col, lw=1.2, ls=ls, alpha=0.75)
        # Label ring at north/east arc
        lbl_lat = LAT0 + (mi*1.60934/111.132)*math.cos(math.radians(35))
        lbl_lon = LON0 + (mi*1.60934/(111.32*math.cos(math.radians(LAT0))))*math.sin(math.radians(35))
        lx, ly = to_px_r(lbl_lat, lbl_lon)
        if 15 < lx < base_reg.width-40 and 15 < ly < base_reg.height-15:
            ax1.text(lx, ly, f'{mi} mi', color=col, fontsize=7.5, weight='bold',
                     path_effects=[pe.withStroke(linewidth=2.5, foreground='black')])

    # Overlay EPA air monitoring stations
    epa_xs, epa_ys = to_px_r(np.array([f['geometry']['y'] for f in epa]), np.array([f['geometry']['x'] for f in epa]))
    ax1.scatter(epa_xs, epa_ys, c='#00ff88', s=24, marker='^', edgecolors='black', linewidths=0.5, zorder=8,
                label=f'EPA Region 7 Air Monitors ({len(epa)} deployed)')

    # IAAAP detonation origin & KWQC camera
    ox, oy = to_px_r(LAT0, LON0)
    cx_p, cy_p = to_px_r(40.865, -91.2955)
    ax1.plot(ox, oy, '*', color='#ff2244', ms=15, mec='yellow', mew=1.2, zorder=12, label='IAAAP Detonation Zone')
    ax1.plot(cx_p, cy_p, 's', color='#00e5ff', ms=6.5, mec='white', zorder=11, label='KWQC Camera (190th Ave, ENE Danville)')
    ax1.plot([cx_p, ox], [cy_p, oy], color='#00e5ff', lw=1.2, ls='-', alpha=0.85, zorder=10)

    # Label key towns cleanly (skipping Ottumwa/Quincy already labeled on ESRI Reference near edges!)
    key_towns = ['Middletown, IA', 'Burlington, IA', 'West Point, IA', 'Fort Madison, IA',
                 'Donnellson, IA', 'Farmington, IA', 'Keosauqua, IA', 'Keokuk, IA',
                 'Kahoka, MO', 'Memphis, MO', 'Edina, MO', 'Kirksville, MO', 'Bloomfield, IA']
    t_off = {
        'Middletown, IA': (8, -12), 'Burlington, IA': (6, -6),
        'West Point, IA': (6, -10), 'Fort Madison, IA': (8, 4), 'Donnellson, IA': (8, 4),
        'Farmington, IA': (-54, 12), 'Keosauqua, IA': (-54, -10),
        'Keokuk, IA': (8, 4), 'Kahoka, MO': (8, 6), 'Memphis, MO': (-46, -10),
        'Edina, MO': (8, 4), 'Kirksville, MO': (-58, 12), 'Bloomfield, IA': (-54, -10),
        'Ottumwa, IA': (-48, -10), 'Quincy, IL': (8, 4),
    }
    for tr in towns:
        if tr['town'] in key_towns:
            tx, ty = to_px_r(tr['lat'], tr['lon'])
            dx, dy = t_off.get(tr['town'], (6, -6))
            short = tr['town'].replace(', IA', '').replace(', MO', ' (MO)')
            ax1.plot(tx, ty, 'o', color='white', ms=3, mec='black', zorder=9)
            ax1.text(tx+dx, ty+dy, short, color='#f0f4f8', fontsize=7.8, weight='bold', zorder=10,
                     path_effects=[pe.withStroke(linewidth=2.5, foreground='black')])

    cb1 = plt.colorbar(hm1, ax=ax1, orientation='horizontal', fraction=0.042, pad=0.03, aspect=36)
    cb1.set_label('Breathing-Zone Integrated Exposure Factor  log₁₀(χ/Q)  [s/m³ per unit mass released]  —  Contours: Ensemble Hit Probability (%)',
                  color='#ddd', fontsize=8.5)
    cb1.ax.tick_params(colors='#ccc', labelsize=8)
    ax1.set_title('A. Airborne Plume Dispersion Heatmap (0–50 m AGL, 0–5 hr) & EPA Monitors', color='white', fontsize=11.5, weight='bold', pad=8)
    ax1.legend(facecolor='#0e1117', edgecolor='#555', labelcolor='white', fontsize=7.8, loc='lower right')
    ax1.set_xlim(0, base_reg.width); ax1.set_ylim(base_reg.height, 0); ax1.axis('off')

    # --- Panel 2: Surface Deposition Regimes + Hourly Arrival Isochrones ---
    ax2.imshow(base_reg)
    log_dep = np.where(dep_tot_reg > 1e-13, np.log10(dep_tot_reg), -99.0)
    vmax_dep = np.percentile(log_dep[log_dep > -13], 99.5)
    vmin_dep = vmax_dep - 4.0
    mask_dep = np.ma.masked_where(log_dep < vmin_dep, log_dep)
    hm2 = ax2.pcolormesh(PX_r, PY_r, mask_dep, cmap='magma', vmin=vmin_dep, vmax=vmax_dep, alpha=0.72, shading='auto')

    # Highlight coarse ballistic/dust fallout core (top 90% of coarse mass!)
    dep_c_norm = dep_c / (dep_c.max() + 1e-30)
    ax2.contour(PX_r, PY_r, dep_c_norm, levels=[0.015, 0.10], colors=['#00ff88', '#88ffcc'], linewidths=[1.6, 1.4], linestyles=['-', '-'])
    # Arrival time isochrones (15, 30, 60, 120, 180 min post-blast) ONLY where exposure is meaningful
    arr_clean = np.where(air_reg > 10**(vmin_air + 0.4), arr_min, np.nan)
    # Fill tiny gaps via gaussian smoothing of valid mask
    v_arr = ~np.isnan(arr_clean)
    if v_arr.sum() > 50:
        arr_s = gaussian_filter(np.where(v_arr, arr_clean, 0.0), 1.5) / np.maximum(gaussian_filter(v_arr.astype(float), 1.5), 1e-6)
        arr_s = np.where(air_reg > 10**(vmin_air + 0.6), arr_s, np.nan)
        cs_t = ax2.contour(PX_r, PY_r, arr_s, levels=[15, 30, 60, 120, 180], colors='#00e5ff', linewidths=1.3, linestyles='--', alpha=0.92)
        ax2.clabel(cs_t, fmt=lambda v: f'+{int(v)}m' if v < 60 else f'+{int(v//60)}h', fontsize=8, inline=True, colors='#00e5ff')

    # Range rings + IAAAP + Towns
    for mi, col, ls in [(5, '#ff4466', '-'), (20, '#ffaa44', '--')]:
        rx_p, ry_p = range_ring_px(LAT0, LON0, mi, to_px_r)
        ax2.plot(rx_p, ry_p, color=col, lw=1.1, ls=ls, alpha=0.65)
    ax2.plot(ox, oy, '*', color='#ff2244', ms=15, mec='yellow', mew=1.2, zorder=12)

    # Sample particle streamlines (low-level SW vs elevated WSW shear!)
    tx_arr, ty_arr = d['traj_x'], d['traj_y']
    for k in range(0, tx_arr.shape[1], 5):
        tlats = LAT0 + ty_arr[:, k] / M_PER_LAT
        tlons = LON0 + tx_arr[:, k] / M_PER_LON
        ok = ~np.isnan(tlats)
        if ok.sum() > 3:
            pxt, pyt = to_px_r(tlats[ok], tlons[ok])
            ax2.plot(pxt, pyt, color='white', lw=0.45, alpha=0.28)

    for tr in towns:
        if tr['town'] in key_towns:
            tx, ty = to_px_r(tr['lat'], tr['lon'])
            dx, dy = t_off.get(tr['town'], (6, -6))
            short = tr['town'].replace(', IA', '').replace(', MO', ' (MO)')
            ax2.plot(tx, ty, 'o', color='white', ms=3, mec='black', zorder=9)
            ax2.text(tx+dx, ty+dy, short, color='#f0f4f8', fontsize=7.8, weight='bold', zorder=10,
                     path_effects=[pe.withStroke(linewidth=2.5, foreground='black')])

    # Legend proxy artists for Panel 2
    ax2.plot([], [], color='#00ff88', lw=1.8, label='Coarse Dust Fallout Core (20–70 µm, ~0–6 km)')
    ax2.plot([], [], color='#00e5ff', lw=1.4, ls='--', label='Plume Arrival Isochrones (+15m, +30m, +1h, +2h, +3h)')
    ax2.plot([], [], color='white', lw=0.8, alpha=0.6, label='Sample 3D Sheared Particle Trajectories')
    ax2.plot([], [], color='#ff4466', lw=1.2, label='5-mi & 20-mi Reference Radii')

    cb2 = plt.colorbar(hm2, ax=ax2, orientation='horizontal', fraction=0.042, pad=0.03, aspect=36)
    cb2.set_label('Relative Surface Dust Deposition Intensity  log₁₀(Deposition Fraction / m²)  —  Coarse + Fine Regimes Combined',
                  color='#ddd', fontsize=8.5)
    cb2.ax.tick_params(colors='#ccc', labelsize=8)
    ax2.set_title('B. Surface Deposition Regimes, Coarse Fallout Core & Arrival Isochrones', color='white', fontsize=11.5, weight='bold', pad=8)
    ax2.legend(facecolor='#0e1117', edgecolor='#555', labelcolor='white', fontsize=7.8, loc='upper left')
    ax2.set_xlim(0, base_reg.width); ax2.set_ylim(base_reg.height, 0); ax2.axis('off')

    fig.suptitle('Modeled Dispersion & Deposition Heatmaps — IAAAP Controlled Detonation (Nov 25, 2026 | 9:00 AM–2:00 PM CST)',
                 color='white', fontsize=14, weight='bold', y=0.955)
    fig.text(0.5, 0.915, '60,000-Particle 3D Lagrangian Ensemble (60 Wind/Source Realizations) | Driven by Observed Regional ASOS + 15Z–20Z Vertical Wind Shear + Video-Calibrated Plume Rise',
             color='#aac4dd', fontsize=9.8, ha='center')
    plt.savefig(f'{PROJ}/outputs/fig3_dispersion_heatmap_regional.png', dpi=160, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print('Saved outputs/fig3_dispersion_heatmap_regional.png')

    # ============================================================
    # FIGURE 4: LOCAL NEAR-FIELD HEATMAP & GEOLOCATION VERIFICATION
    # ============================================================
    lny, lnx = air_loc.shape
    lx_c = LX_MIN + (np.arange(lnx) + 0.5) * L_DX
    ly_c = LY_MIN + (np.arange(lny) + 0.5) * L_DY
    LXG, LYG = np.meshgrid(lx_c, ly_c)
    LLAT = LAT0 + LYG / M_PER_LAT
    LLON = LON0 + LXG / M_PER_LON

    loc_lat0, loc_lat1, loc_lon0, loc_lon1 = 40.66, 40.89, -91.50, -91.16
    esri_url = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    base_loc, to_px_l = fetch_basemap(loc_lat0, loc_lat1, loc_lon0, loc_lon1, 12, esri_url)
    PX_l, PY_l = to_px_l(LLAT, LLON)

    fig, ax = plt.subplots(figsize=(13.5, 11), dpi=150, facecolor='#0b0f14')
    ax.imshow(base_loc)

    # Overlay IAAAP boundary polygon from OSM (label once)
    def plot_geo(geom, label=None, **kw):
        used = [False]
        def _p(ring):
            pts = np.array(ring)
            xs, ys = to_px_l(pts[:,1], pts[:,0])
            ax.plot(xs, ys, label=(label if not used[0] else None), **kw)
            used[0] = True
        if geom['type'] == 'Polygon':
            for ring in geom['coordinates']: _p(ring)
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                for ring in poly: _p(ring)
    plot_geo(iaaap_poly, color='#ffdd44', lw=2.0, ls='--', alpha=0.9, label='IAAAP Perimeter (19,000 acres)')

    # Overlay local near-field combined deposition + breathing-zone exposure heatmap!
    log_aloc = np.where(air_loc > 1e-10, np.log10(air_loc), -99.0)
    vmax_l = np.percentile(log_aloc[log_aloc > -10], 99.5)
    vmin_l = vmax_l - 3.2
    mask_l = np.ma.masked_where(log_aloc < vmin_l, log_aloc)
    hm_l = ax.pcolormesh(PX_l, PY_l, mask_l, cmap='inferno', vmin=vmin_l, vmax=vmax_l, alpha=0.62, shading='auto')

    # Local coarse deposition contours
    log_dloc = np.where(dep_loc > 1e-12, np.log10(dep_loc), -99.0)
    vd_max = np.percentile(log_dloc[log_dloc > -12], 99.5)
    ax.contour(PX_l, PY_l, log_dloc, levels=[vd_max-2.5, vd_max-1.5, vd_max-0.7], colors=['#00ff88', '#88ffaa', '#ffffff'],
               linewidths=[1.2, 1.5, 1.8], linestyles=[':', '--', '-'])

    # 5-mile radius ring
    rx5, ry5 = range_ring_px(LAT0, LON0, 5.0, to_px_l)
    ax.plot(rx5, ry5, color='#ff4466', lw=2.0, ls='--', alpha=0.9, label='5-Mile Evacuation Reference Radius')

    # KWQC Camera, FAA Tower T3, Sightline Ray & Camera FOV cone!
    cx_l, cy_l = to_px_l(40.865, -91.2955)
    t3x, t3y = to_px_l(40.84110, -91.28298)
    ox_l, oy_l = to_px_l(LAT0, LON0)
    # Draw camera telephoto FOV cone (~ +/- 2.5 deg around 158.2 deg)
    for ang_off, alpha_l in [(-2.8, 0.55), (0.0, 0.95), (2.8, 0.55)]:
        ang = 158.2 + ang_off
        end_lat = 40.865 + (9.5/111.132)*math.cos(math.radians(ang))
        end_lon = -91.2955 + (9.5/(111.32*math.cos(math.radians(40.85))))*math.sin(math.radians(ang))
        ex, ey = to_px_l(end_lat, end_lon)
        ax.plot([cx_l, ex], [cy_l, ey], color='#00e5ff', lw=1.8 if ang_off==0 else 1.0, ls='-' if ang_off==0 else ':', alpha=alpha_l)

    ax.plot(cx_l, cy_l, 's', color='#00e5ff', ms=10, mec='black', mew=1.2, zorder=12, label='KWQC Camera (190th Ave, 1 mi ENE Danville)')
    ax.plot(t3x, t3y, '^', color='#ff44ff', ms=11, mec='white', mew=1.0, zorder=12, label='FAA Tower T3 (235 ft AGL — Optical Scale Ref)')
    ax.plot(ox_l, oy_l, '*', color='#ff2244', ms=18, mec='yellow', mew=1.3, zorder=13, label='Geolocated Dual-Bunker Sightline Origin (~4.2 mi SE)')

    # Nearby EPA monitors inside local box
    ex_l, ey_l = [], []
    for f in epa:
        la, lo = f['geometry']['y'], f['geometry']['x']
        if loc_lat0 <= la <= loc_lat1 and loc_lon0 <= lo <= loc_lon1:
            px, py = to_px_l(la, lo)
            ex_l.append(px); ey_l.append(py)
            ax.text(px+7, py-5, f['attributes']['Station_ID'], color='#88ffaa', fontsize=7.5, weight='bold',
                    path_effects=[pe.withStroke(linewidth=2, foreground='black')])
    ax.scatter(ex_l, ey_l, c='#00ff88', s=48, marker='^', edgecolors='black', linewidths=0.8, zorder=11, label='Nearby EPA Air Monitoring Stations')

    # Local town labels
    local_towns = [
        ('Danville', 40.8656, -91.3149, (-62, -4)),
        ('Middletown', 40.8284, -91.2590, (10, -10)),
        ('West Burlington', 40.8195, -91.1793, (-72, -16)),
        ('Augusta', 40.7556, -91.2779, (10, 6)),
        ('Denmark', 40.7370, -91.3340, (10, 6)),
        ('West Point', 40.7167, -91.4544, (-45, -14)),
        ('KWQC Camera\n(190th Ave)', 40.865, -91.2955, (14, -18)),
        ('FAA Tower T3\n(235 ft)', 40.8411, -91.2830, (14, -8)),
    ]
    for nm, la, lo, (dx, dy) in local_towns:
        px, py = to_px_l(la, lo)
        if nm not in ('KWQC Camera\n(190th Ave)', 'FAA Tower T3\n(235 ft)'):
            ax.plot(px, py, 'o', color='white', ms=4, mec='black', zorder=10)
        col = '#00e5ff' if 'KWQC' in nm else ('#ff88ff' if 'Tower' in nm else 'white')
        ax.text(px+dx, py+dy, nm, color=col, fontsize=8.8, weight='bold', zorder=12,
                path_effects=[pe.withStroke(linewidth=2.8, foreground='black')])

    # Add wind direction callout box cleanly in top-left open area!
    ax.text(28, 30,
            '15:00 UTC Observed & Modeled Winds:\n'
            '• Surface (10 m): FROM 043° (NE) → TO 223° (SW) @ 4.1–4.5 m/s\n'
            '• Plume Core (80–180 m): TO 224°–235° @ 5.1–9.9 m/s\n'
            '• Mid-PBL (450 m): TO 239° (WSW) @ 8.7 m/s',
            color='#ffdd44', fontsize=8.3, weight='bold', ha='left', va='top', zorder=14,
            bbox=dict(facecolor='#0e1117', alpha=0.88, edgecolor='#ffdd44', pad=6))
    # Clean wind arrow right below callout box pointing SW (225 deg)
    ax.annotate('', xy=(85, 195), xytext=(155, 130),
                arrowprops=dict(arrowstyle='->', color='#ffdd44', lw=3.2), zorder=14)
    ax.text(138, 178, 'SW / WSW\nTransport', color='#ffdd44', fontsize=8, weight='bold', zorder=14,
            path_effects=[pe.withStroke(linewidth=2.2, foreground='black')])

    ax.plot([], [], color='#00ff88', lw=1.6, ls='--', label='Near-Field Surface Deposition Contours (Coarse+Fine)')
    cb_l = plt.colorbar(hm_l, ax=ax, orientation='horizontal', fraction=0.036, pad=0.02, aspect=42)
    cb_l.set_label('Near-Field Breathing-Zone Exposure  log₁₀(χ/Q)  [s/m³ per unit mass released]', color='#ddd', fontsize=9)
    cb_l.ax.tick_params(colors='#ccc', labelsize=8.5)
    ax.set_title('Local Near-Field Dispersion Heatmap, Camera Sightline Triangulation & IAAAP Perimeter (ESRI Satellite)',
                 color='white', fontsize=12.5, weight='bold', pad=10)
    ax.legend(facecolor='#0e1117', edgecolor='#666', labelcolor='white', fontsize=8.0, loc='lower right')
    ax.set_xlim(0, base_loc.width); ax.set_ylim(base_loc.height, 0); ax.axis('off')
    plt.savefig(f'{PROJ}/outputs/fig4_local_heatmap_geoloc.png', dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print('Saved outputs/fig4_local_heatmap_geoloc.png')

    # ============================================================
    # INTERACTIVE FOLIUM / LEAFLET HTML HEATMAP
    # ============================================================
    m = folium.Map(location=[40.68, -91.45], zoom_start=10, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
        attr='Esri Dark Gray Canvas', name='ESRI Dark Gray Canvas', show=True
    ).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
        attr='Esri Reference', name='Map Labels & Roads Overlay', overlay=True, show=True
    ).add_to(m)
    folium.TileLayer('OpenStreetMap', name='OpenStreetMap Streets', show=False).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri World Imagery', name='ESRI Satellite Imagery', show=False
    ).add_to(m)

    # Helper: convert 2D numpy grid (lat ascending, lon ascending) to RGBA PNG data URI for Folium ImageOverlay!
    def grid_to_png_uri(grid, vmin, vmax, cmap_name='inferno', alpha_max=0.72):
        norm = np.clip((grid - vmin) / (vmax - vmin), 0.0, 1.0)
        cmap = plt.get_cmap(cmap_name)
        rgba = cmap(norm) # (H, W, 4) float 0..1
        # Smooth alpha ramp from 0 below vmin up to alpha_max
        alpha = np.where(grid < vmin, 0.0, np.clip(norm**0.55 * alpha_max + 0.12, 0.0, alpha_max))
        rgba[..., 3] = alpha
        # Flip vertically because ImageOverlay expects row 0 = north (max lat)!
        rgba_u8 = (np.flipud(rgba) * 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(rgba_u8, mode='RGBA').save(buf, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')

    bounds_reg = [[float(RLAT.min()), float(RLON.min())], [float(RLAT.max()), float(RLON.max())]]
    ImageOverlay(
        image=grid_to_png_uri(log_air, vmin_air, vmax_air, 'inferno', 0.72),
        bounds=bounds_reg, name='Airborne Exposure Heatmap (0–50 m AGL, log10 χ/Q)', interactive=False, show=True
    ).add_to(m)
    ImageOverlay(
        image=grid_to_png_uri(log_dep, vmin_dep, vmax_dep, 'magma', 0.72),
        bounds=bounds_reg, name='Surface Deposition Heatmap (Coarse + Fine Dust)', interactive=False, show=False
    ).add_to(m)
    ImageOverlay(
        image=grid_to_png_uri(ens_prob, 5.0, 95.0, 'viridis', 0.55),
        bounds=[[float(PLAT.min()), float(PLON.min())], [float(PLAT.max()), float(PLON.max())]],
        name='Ensemble Plume Probability (60 Realizations, %)', interactive=False, show=False
    ).add_to(m)

    # Feature groups
    fg_geom = folium.FeatureGroup(name='Detonation Zone, Camera Sightline & Radii', show=True)
    # IAAAP boundary
    folium.GeoJson(iaaap_poly, style_function=lambda x: {'color': '#ffdd44', 'weight': 2, 'fillOpacity': 0.08, 'dashArray': '6,4'},
                   tooltip='Iowa Army Ammunition Plant (IAAAP)').add_to(fg_geom)
    # 5-mi and 20-mi circles
    folium.Circle([LAT0, LON0], radius=5*1609.34, color='#ff4466', weight=2, dash_array='6,4', fill=False, tooltip='5-Mile Radius').add_to(fg_geom)
    folium.Circle([LAT0, LON0], radius=20*1609.34, color='#ffaa44', weight=1.5, dash_array='8,6', fill=False, tooltip='20-Mile Radius').add_to(fg_geom)
    # Camera, Tower T3, Blast Origin, Sightline
    folium.Marker([LAT0, LON0], icon=folium.Icon(color='red', icon='fire', prefix='fa'),
                  popup=f'<b>Geolocated Dual-Bunker Detonation Zone</b><br>Nominal: {LAT0:.4f}°N, {LON0:.4f}°W<br>~4.2 mi SE of KWQC Camera (Az ~158°)<br>Time: ~9:00 AM CST (15:00 UTC), Nov 25, 2026').add_to(fg_geom)
    folium.Marker([40.865, -91.2955], icon=folium.Icon(color='blue', icon='video-camera', prefix='fa'),
                  popup='<b>KWQC Livestream Camera</b><br>190th Ave (Level-B dirt road), ~1 mi ENE of Danville, IA<br>Look Bearing: ~158° SSE across FAA Tower T3').add_to(fg_geom)
    folium.Marker([40.84110, -91.28298], icon=folium.Icon(color='purple', icon='signal', prefix='fa'),
                  popup='<b>FAA Tower T3 (OAS 19-002048)</b><br>Height: 235 ft (71.6 m) AGL<br>Optical Scale Calibration Landmark (307 px)').add_to(fg_geom)
    folium.PolyLine([[40.865, -91.2955], [40.84110, -91.28298], [LAT0, LON0]], color='#00e5ff', weight=2.5, dash_array='5,5',
                    tooltip='KWQC Camera Sightline (158.2° SSE)').add_to(fg_geom)
    fg_geom.add_to(m)

    # EPA Air Monitoring Stations layer
    fg_epa = folium.FeatureGroup(name=f'EPA Region 7 Air Monitors ({len(epa)} stations)', show=True)
    for f in epa:
        a = f['attributes']; g = f['geometry']
        folium.CircleMarker([g['y'], g['x']], radius=5, color='#000', weight=1, fill=True, fill_color='#00ff88', fill_opacity=0.85,
                            popup=f"<b>EPA {a['Station_ID']}</b><br>Instruments: {a['Instrument_Type']}<br>({g['y']:.4f}, {g['x']:.4f})").add_to(fg_epa)
    fg_epa.add_to(m)

    # ASOS Weather Stations layer
    fg_wx = folium.FeatureGroup(name='Observed ASOS Surface Winds (15Z)', show=True)
    for s, w in met['stations'].items():
        folium.CircleMarker([w['lat'], w['lon']], radius=6, color='white', weight=1.5, fill=True, fill_color='#00aaff', fill_opacity=0.9,
                            popup=f"<b>K{s} ASOS (14:50–15:20Z)</b><br>Wind FROM {w['from_dir']:.0f}° → TO {w['to_dir']:.0f}°<br>Speed: {w['spd']:.2f} m/s ({w['spd']*2.237:.1f} mph)").add_to(fg_wx)
        # Draw short arrow polyline in wind transport direction (length proportional to speed)
        scale_km = w['spd'] * 1.8
        dlat = (scale_km/111.132) * math.cos(math.radians(w['to_dir']))
        dlon = (scale_km/(111.32*math.cos(math.radians(w['lat'])))) * math.sin(math.radians(w['to_dir']))
        folium.PolyLine([[w['lat'], w['lon']], [w['lat']+dlat, w['lon']+dlon]], color='#ffdd44', weight=3).add_to(fg_wx)
    fg_wx.add_to(m)

    # Town Impact Markers layer
    fg_towns = folium.FeatureGroup(name='Community Modeled Arrival & Exposure Summary', show=False)
    for tr in towns:
        arr_s = f"+{tr['mean_arrival_min']:.0f} min" if tr['mean_arrival_min'] is not None else "Outside primary path"
        col = '#ff4444' if tr['ens_prob_pct'] >= 60 else ('#ffaa33' if tr['ens_prob_pct'] >= 15 else '#88aacc')
        folium.CircleMarker([tr['lat'], tr['lon']], radius=5, color='white', weight=1, fill=True, fill_color=col, fill_opacity=0.85,
                            popup=f"<b>{tr['town']}</b><br>Distance: {tr['dist_mi']} mi @ {tr['bearing_deg']}°<br>Ensemble Hit Prob: {tr['ens_prob_pct']}%<br>Est. Plume Arrival: {arr_s}<br>χ/Q: {tr['chi_over_Q_s_m3']:.2e} s/m³").add_to(fg_towns)
    fg_towns.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    # Add compact dark legend HTML box
    legend_html = """
    <div style="position: fixed; bottom: 25px; left: 25px; z-index: 9999; background: rgba(14,17,23,0.90);
                color: #eee; padding: 12px 14px; border-radius: 8px; border: 1px solid #555; font-family: sans-serif; font-size: 12px; max-width: 310px;">
      <div style="font-weight: bold; font-size: 13px; color: #ffdd44; margin-bottom: 6px;">IAAAP Controlled Detonation Dispersion (Nov 25, 2026)</div>
      <div><b>Event:</b> ~9:00 AM CST (15:00 UTC) | Dual-bunker lead azide blast</div>
      <div><b>Observed Wind:</b> NE 040°–050° @ 8–10 kt (sfc) veering ENE 058° @ 17 kt aloft</div>
      <div><b>Plume Drift:</b> SW → WSW (220°–242°) across Lee/Van Buren IA & Clark MO</div>
      <div style="margin-top:6px; font-size:11px; color:#aaa;">Heatmap shows <i>relative dilution (χ/Q)</i> & deposition regimes (not absolute lead mg/m³, as in-bunker mitigation efficiency is unknown from open video).</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    out_html = f'{PROJ}/outputs/iaaap_plume_dispersion_interactive.html'
    m.save(out_html)
    print(f'Saved {out_html}')

if __name__ == '__main__':
    main()
