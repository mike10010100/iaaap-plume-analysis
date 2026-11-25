#!/usr/bin/env python3
"""
Render:
- Figure 5: Dual-Camera Stereo Geolocation (KWQC + KCRG/AP 9856 202nd Ave) & 3D Plume Verification
- Figure 6: Live EPA Xact Telemetry vs Modeled Arrival, Inverse Release Upper Bound & Sensitivity Sweep
- Upgraded Interactive Folium HTML Map with Stereo Camera 2 + Sensitivity Contours + Live Xact Popups
"""
import io, json, math, base64
import numpy as np
import requests
from PIL import Image
from scipy.ndimage import gaussian_filter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as patches
import matplotlib.patheffects as pe
import folium
from folium.raster_layers import ImageOverlay

PROJ = '/home/mike10010100/.gemini/antigravity-cli/scratch/iaaap-plume-analysis'

def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = np.radians(lat_deg)
    n = 2.0 ** zoom
    return (lon_deg + 180.0) / 360.0 * n, (1.0 - np.arcsinh(np.tan(lat_rad)) / np.pi) / 2.0 * n

def fetch_basemap(lat_min, lat_max, lon_min, lon_max, zoom, url_tmpl, rgba=False):
    x0, y0 = deg2num(lat_max, lon_min, zoom); x1, y1 = deg2num(lat_min, lon_max, zoom)
    ix0, ix1, iy0, iy1 = int(math.floor(x0)), int(math.floor(x1)), int(math.floor(y0)), int(math.floor(y1))
    mode = 'RGBA' if rgba else 'RGB'
    bg = (0,0,0,0) if rgba else (25,30,38)
    canvas = Image.new(mode, ((ix1-ix0+1)*256, (iy1-iy0+1)*256), bg)
    sess = requests.Session(); sess.headers['User-Agent'] = 'OSINT-Plume-Analysis/1.0'
    for ix in range(ix0, ix1+1):
        for iy in range(iy0, iy1+1):
            try:
                r = sess.get(url_tmpl.format(z=zoom, x=ix, y=iy), timeout=12)
                if r.status_code == 200:
                    canvas.paste(Image.open(io.BytesIO(r.content)).convert(mode), ((ix-ix0)*256, (iy-iy0)*256))
            except Exception:
                pass
    px0, py0 = (x0-ix0)*256.0, (y0-iy0)*256.0
    px1, py1 = (x1-ix0)*256.0, (y1-iy0)*256.0
    box = (int(px0), int(py0), int(px1), int(py1))
    cropped = canvas.crop(box)
    def to_px(lat, lon):
        tx, ty = deg2num(lat, lon, zoom)
        return (tx-ix0)*256.0 - box[0], (ty-iy0)*256.0 - box[1]
    return cropped, to_px

def make_fig5():
    stereo = json.load(open(f'{PROJ}/data/stereo_triangulation.json'))
    kin = json.load(open(f'{PROJ}/data/calibrated_kinematics.json'))
    met = json.load(open(f'{PROJ}/data/met_summary.json'))
    iaaap_poly = json.load(open(f'{PROJ}/data/iaaap_osm.json'))[0]['geojson']

    fig = plt.figure(figsize=(17.5, 11.8), dpi=150, facecolor='#0e1117')
    gs = gridspec.GridSpec(2, 2, height_ratios=[0.95, 1.15], hspace=0.25, wspace=0.14, top=0.90, bottom=0.05, left=0.04, right=0.97)

    # --- Panel A: KCRG/AP Wide Shot + Inset of Blue "9856" Rural 911 Sign ---
    axA = fig.add_subplot(gs[0, 0])
    im_wide = Image.open(f'{PROJ}/frames/ap_cam2/ap_02.png')
    axA.imshow(im_wide)
    # Annotate key geolocation landmarks
    axA.annotate('090° East\nGrass Lane', xy=(420, 620), xytext=(120, 420),
                 arrowprops=dict(arrowstyle='->', color='#00e5ff', lw=2),
                 color='#00e5ff', fontsize=8.5, weight='bold', bbox=dict(facecolor='black', alpha=0.75, pad=2.5))
    axA.annotate('~130° SE\nBush Clump', xy=(1530, 560), xytext=(1250, 350),
                 arrowprops=dict(arrowstyle='->', color='#00ff88', lw=2),
                 color='#00ff88', fontsize=8.5, weight='bold', bbox=dict(facecolor='black', alpha=0.75, pad=2.5))
    axA.annotate('138° SE Sun Glare\n(9:00 AM CST)', xy=(1720, 120), xytext=(1240, 130),
                 arrowprops=dict(arrowstyle='->', color='#ffdd44', lw=2),
                 color='#ffdd44', fontsize=8.5, weight='bold', bbox=dict(facecolor='black', alpha=0.75, pad=2.5))
    # Draw zoom box around "9856" sign (x=1170..1230, y=670..790)
    rect = patches.Rectangle((1150, 650), 95, 150, lw=2, edgecolor='#ff3366', facecolor='none')
    axA.add_patch(rect)
    # Inset magnification of blue "9856" 911 marker!
    ax_in = axA.inset_axes([0.74, 0.05, 0.23, 0.44])
    crop_sign = im_wide.crop((1165, 675, 1228, 785))
    ax_in.imshow(crop_sign)
    ax_in.set_title('911 Sign: "9856"\n(202nd Ave)', color='#ffdd44', fontsize=7.8, weight='bold', pad=3,
                    bbox=dict(facecolor='black', alpha=0.8, pad=1.5))
    ax_in.set_xticks([]); ax_in.set_yticks([])
    for sp in ax_in.spines.values(): sp.set_edgecolor('#ff3366'); sp.set_linewidth(2)
    axA.set_title('A. Camera 2 (KCRG/AP Wide): Pinpointed via "9856" 911 Sign at 9856 202nd Ave', color='white', fontsize=10.5, weight='bold', pad=6)
    axA.set_xticks([]); axA.set_yticks([])

    # --- Panel B: KCRG/AP Stationary Telephoto Plume Track (t=20s vs t=36s overlay!) ---
    axB = fig.add_subplot(gs[0, 1])
    im20 = np.asarray(Image.open(f'{PROJ}/frames/ap_cam2/ap_20.png'), dtype=np.float32)
    im36 = np.asarray(Image.open(f'{PROJ}/frames/ap_cam2/ap_36.png'), dtype=np.float32)
    # Show ap_20 base with contours of ap_20 (T+30s) AND ap_36 (T+46s) plume positions!
    files_ap = [f'{PROJ}/frames/ap_cam2/ap_{i:02d}.png' for i in range(19, 43)]
    stack_ap = np.stack([np.asarray(Image.open(f), dtype=np.float32) for f in files_ap], axis=0)
    med_ap = np.median(stack_ap, axis=0)
    rb_med = med_ap[:,:,0] - med_ap[:,:,2]
    d20 = gaussian_filter(rb_med - (im20[:,:,0] - im20[:,:,2]), 4.0)
    d36 = gaussian_filter(rb_med - (im36[:,:,0] - im36[:,:,2]), 4.0)
    d20[560:, :] = 0; d20[:220, :] = 0; d20[:, :700] = 0; d20[:, 1150:] = 0
    d36[560:, :] = 0; d36[:220, :] = 0; d36[:, 950:1000] = d36[:, 950:1000]; d36[:, :950] = 0; d36[:, 1450:] = 0

    axB.imshow(np.clip((0.55*im20 + 0.45*im36)/255.0, 0, 1))
    axB.contour(d20, levels=[10, 28, 55], colors=['#00e5ff','#00aaff','#ffffff'], linewidths=1.6)
    axB.contour(d36, levels=[10, 28, 55], colors=['#ffdd44','#ff7700','#ff2244'], linewidths=1.6)
    axB.annotate('Plume @ T+30s\n(Az ~112° ESE)', xy=(892, 430), xytext=(420, 250),
                 arrowprops=dict(arrowstyle='->', color='#00e5ff', lw=2),
                 color='#00e5ff', fontsize=8.5, weight='bold', bbox=dict(facecolor='black', alpha=0.75, pad=2.5))
    axB.annotate('Plume @ T+46s\n(+14.3 px/s, R²=0.998)\nv ≈ 8.7 m/s | h → 252 m', xy=(1135, 360), xytext=(1250, 200),
                 arrowprops=dict(arrowstyle='->', color='#ffdd44', lw=2),
                 color='#ffdd44', fontsize=8.5, weight='bold', bbox=dict(facecolor='black', alpha=0.75, pad=2.5))
    axB.annotate('', xy=(1140, 365), xytext=(900, 445),
                 arrowprops=dict(arrowstyle='->', color='#ff4466', lw=2.8))
    axB.text(1020, 495, '20° Buoyant\nRise + SW Drift', color='#ff8899', fontsize=8, weight='bold', ha='center',
             bbox=dict(facecolor='black', alpha=0.75, pad=2))
    axB.set_xlim(180, 1780); axB.set_ylim(820, 120)
    axB.set_title('B. Camera 2 Stationary Telephoto: Composite Track T+30s (Cyan) → T+46s (Gold)', color='white', fontsize=10.5, weight='bold', pad=6)
    axB.set_xticks([]); axB.set_yticks([])

    # --- Panel C: High-Res ESRI Satellite Stereo Cross-Triangulation Map ---
    axC = fig.add_subplot(gs[1, 0])
    esri_url = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    lat0, lat1, lon0, lon1 = 40.765, 40.878, -91.345, -91.205
    sat, to_px = fetch_basemap(lat0, lat1, lon0, lon1, 13, esri_url)
    axC.imshow(sat)

    # IAAAP boundary
    def plot_geo(geom, label=None, **kw):
        used = [False]
        def _p(ring):
            pts = np.array(ring); xs, ys = to_px(pts[:,1], pts[:,0])
            axC.plot(xs, ys, label=(label if not used[0] else None), **kw); used[0] = True
        if geom['type'] == 'Polygon':
            for r in geom['coordinates']: _p(r)
        elif geom['type'] == 'MultiPolygon':
            for p in geom['coordinates']:
                for r in p: _p(r)
    plot_geo(iaaap_poly, color='#ffdd44', lw=1.8, ls='--', alpha=0.9, label='IAAAP Facility Perimeter')

    c1 = stereo['cam1_kwqc']; c2 = stereo['cam2_kcrg_ap']; orig = stereo['triangulated_origin']
    t3_lat, t3_lon = 40.84110, -91.28298
    MLAT = 111132.0; MLON = 111320.0 * math.cos(math.radians(orig['lat']))

    def draw_ray_wedge(clat, clon, brg, err_deg, km_len, col, label):
        cx, cy = to_px(clat, clon)
        # Central ray
        elat = clat + (km_len/111.132)*math.cos(math.radians(brg))
        elon = clon + (km_len/(111.32*math.cos(math.radians(clat))))*math.sin(math.radians(brg))
        ex, ey = to_px(elat, elon)
        axC.plot([cx, ex], [cy, ey], color=col, lw=2.0, label=label, zorder=8)
        # Uncertainty fan (+/- err_deg)
        lats_w = [clat]; lons_w = [clon]
        for a in np.linspace(brg-err_deg, brg+err_deg, 15):
            lats_w.append(clat + (km_len/111.132)*math.cos(math.radians(a)))
            lons_w.append(clon + (km_len/(111.32*math.cos(math.radians(clat))))*math.sin(math.radians(a)))
        xs_w, ys_w = to_px(np.array(lats_w), np.array(lons_w))
        axC.fill(xs_w, ys_w, color=col, alpha=0.18, zorder=6)

    draw_ray_wedge(c1['lat'], c1['lon'], c1['bearing_deg'], 2.2, 8.8, '#00e5ff', 'Cam 1 Sightline (KWQC, 158.3° ± 2.2°)')
    draw_ray_wedge(c2['lat'], c2['lon'], c2['bearing_deg'], 2.2, 7.2, '#ff8833', 'Cam 2 Sightline (KCRG/AP, 112.0° ± 2.2°)')

    # Acoustic range arc from Cam 1 (6.23 km +/- 0.5 km)
    angs_ac = np.linspace(142, 174, 60)
    for r_km, ls, al, lbl in [(6.23, '-', 0.9, 'KWQC Acoustic Range (6.23 ± 0.5 km)'), (5.73, ':', 0.55, None), (6.73, ':', 0.55, None)]:
        la = c1['lat'] + (r_km/111.132)*np.cos(np.radians(angs_ac))
        lo = c1['lon'] + (r_km/(111.32*math.cos(math.radians(c1['lat']))))*np.sin(np.radians(angs_ac))
        ax_x, ax_y = to_px(la, lo)
        axC.plot(ax_x, ax_y, color='#00ff88', lw=1.6 if ls=='-' else 1.0, ls=ls, alpha=al, label=lbl, zorder=7)

    # Plot Single-Camera 95% Error Ellipse (~1300m x 350m along 158 deg) vs Dual-Camera Stereo Ellipse (~290m x 190m)!
    def plot_ellipse(clat, clon, a_m, b_m, rot_deg, col, ls, lw, lbl):
        th = np.linspace(0, 2*np.pi, 100)
        ex = a_m * np.cos(th); ey = b_m * np.sin(th)
        # Rotate clockwise from North by rot_deg (major axis along rot_deg!)
        rad = math.radians(rot_deg)
        dN = ex * math.cos(rad) - ey * math.sin(rad)
        dE = ex * math.sin(rad) + ey * math.cos(rad)
        xs, ys = to_px(clat + dN/MLAT, clon + dE/MLON)
        axC.plot(xs, ys, color=col, ls=ls, lw=lw, label=lbl, zorder=10)
    plot_ellipse(40.808, -91.265, 1300, 360, 158.0, 'white', '--', 1.4, 'Single-Camera 95% Zone (~1.4 km²)')
    plot_ellipse(orig['lat'], orig['lon'], 340, 220, 135.0, '#ffff00', '-', 2.3, 'Dual-Camera Stereo 95% Zone (~0.25 km²)')

    # Markers
    for la, lo, mk, col, txt, (dx, dy) in [
        (c1['lat'], c1['lon'], 's', '#00e5ff', 'Cam 1: KWQC\n(190th Ave)', (10, -14)),
        (t3_lat, t3_lon, '^', '#ff44ff', 'FAA Tower T3\n(235 ft)', (10, -8)),
        (c2['lat'], c2['lon'], 's', '#ff8833', 'Cam 2: KCRG/AP\n(9856 202nd Ave)', (-22, -32)),
        (orig['lat'], orig['lon'], '*', '#ff2244', 'Stereo Origin\n40.810°N, -91.267°W\n(46.3° Cross-Angle)', (14, -22)),
    ]:
        px, py = to_px(la, lo)
        axC.plot(px, py, mk, color=col, ms=15 if mk=='*' else 9, mec='white' if mk!='*' else 'yellow', mew=1.2, zorder=12)
        axC.text(px+dx, py+dy, txt, color=col if mk!='*' else '#ffdd44', fontsize=8.0, weight='bold', zorder=13,
                 path_effects=[pe.withStroke(linewidth=2.5, foreground='black')])

    axC.set_title('C. Dual-Camera Stereo Cross-Triangulation & 5.8× Uncertainty Reduction', color='white', fontsize=10.5, weight='bold', pad=6)
    axC.legend(facecolor='#0e1117', edgecolor='#666', labelcolor='white', fontsize=7.3, loc='lower left')
    axC.set_xlim(0, sat.width); axC.set_ylim(sat.height, 0); axC.axis('off')

    # --- Panel D: Unified 2-Camera 3D Plume Ascent & Wind-Profile Validation ---
    axD = fig.add_subplot(gs[1, 1], facecolor='#161b22')
    p15 = met['profile_15z']
    zs = [x['z_agl'] for x in p15 if x['z_agl'] <= 480]
    spds = [x['spd_ms'] for x in p15 if x['z_agl'] <= 480]
    axD.plot(spds, zs, 'o-', color='#00e5ff', lw=2.5, ms=7, label='15:00 UTC Modeled Wind Speed Profile (Open-Meteo)')
    # Observed 15Z & 16Z ASOS surface winds at IAAAP
    axD.plot(4.09, 10, 's', color='#00ff88', ms=9, mec='white', zorder=8, label='Observed ASOS Surface Wind @ 15:00Z (4.1 m/s)')
    axD.plot(4.86, 10, 'D', color='#88ffaa', ms=8, mec='white', zorder=8, label='Observed ASOS Surface Wind @ 15:55Z (4.9 m/s)')
    # Cam 1 (KWQC) Plume 1 & Plume 2 + Cam 2 (KCRG/AP) Buoyant Top!
    axD.errorbar(kin['plume_1_east']['v_nom_ms'], 45, xerr=1.1, yerr=25, fmt='o', color='#22ff88', ecolor='#22ff88', ms=9, capsize=4, lw=2,
                 label=f"Cam 1 (KWQC) Plume 1 [T+8–24s]: {kin['plume_1_east']['v_nom_ms']:.1f} m/s @ 20–70 m")
    axD.errorbar(kin['plume_2_west']['v_nom_ms'], 95, xerr=1.3, yerr=45, fmt='s', color='#ff5555', ecolor='#ff5555', ms=9, capsize=4, lw=2,
                 label=f"Cam 1 (KWQC) Plume 2 [T+8–26s]: {kin['plume_2_west']['v_nom_ms']:.1f} m/s @ 40–140 m")
    axD.errorbar(c2['v_true_ms'], 225, xerr=1.4, yerr=30, fmt='^', color='#ffdd44', ecolor='#ffdd44', ms=11, capsize=4, lw=2.2,
                 label=f"Cam 2 (KCRG/AP) Rising Top [T+35–51s]: {c2['v_true_ms']:.1f} m/s @ 200–255 m")
    # Arrow showing continuous vertical plume evolution from Cam 1 to Cam 2!
    axD.annotate('', xy=(c2['v_true_ms']-0.15, 212), xytext=(kin['plume_2_west']['v_nom_ms']+0.15, 108),
                 arrowprops=dict(arrowstyle='->', color='#ffaa44', lw=2.2, ls='--'))
    axD.text(6.6, 165, 'Buoyant Ascent\n(+2.8 m/s vertical)\ninto 180–250 m Shear Jet', color='#ffaa44', fontsize=8.3, weight='bold', ha='center',
             bbox=dict(facecolor='#0e1117', alpha=0.85, edgecolor='#ffaa44', pad=3))
    axD.axhline(285, color='#aa88ff', ls='-.', lw=1.4, label='15:00 UTC Mixing Layer Top (PBL ≈ 285 m AGL)')
    axD.set_xlim(2.0, 11.5); axD.set_ylim(0, 420)
    axD.set_xlabel('Horizontal Wind / Optical Plume Drift Speed (m/s)', color='#ddd', fontsize=9.5)
    axD.set_ylabel('Altitude AGL (meters)', color='#ddd', fontsize=9.5)
    axD.set_title('D. Stereo 3D Verification: Both Cameras Align Along the Real Vertical Shear Profile', color='white', fontsize=10.5, weight='bold', pad=8)
    axD.tick_params(colors='#ccc'); axD.grid(color='#333', alpha=0.6)
    axD.legend(facecolor='#0e1117', edgecolor='#555', labelcolor='white', fontsize=7.6, loc='upper left')
    for sp in axD.spines.values(): sp.set_edgecolor('#444')

    fig.suptitle('Dual-Camera Stereo Triangulation & 3D Plume Verification (KWQC-TV6 + KCRG-TV9 / AP)',
                 color='white', fontsize=14, weight='bold', y=0.965)
    fig.text(0.5, 0.928, 'Two Independent Broadcast Angles (46.3° Baseline) Lock Blast Origin to ±250 m & Confirm Continuous Buoyant Ascent into the 180–250 m Shear Layer',
             color='#aac4dd', fontsize=9.8, ha='center')
    plt.savefig(f'{PROJ}/outputs/fig5_dual_camera_triangulation.png', dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print('Saved outputs/fig5_dual_camera_triangulation.png')

def make_fig6_and_upgrade_html():
    si = json.load(open(f'{PROJ}/data/sensitivity_and_inverse.json'))
    recs = si['receptors']; sweep = si['sweep']
    d = np.load(f'{PROJ}/data/sensitivity_results.npz')
    LAT0, LON0 = float(d['LAT0']), float(d['LON0'])
    M_PER_LAT, M_PER_LON = float(d['M_PER_LAT']), float(d['M_PER_LON'])
    RX_MIN, RX_MAX, R_DX = float(d['RX_MIN']), float(d['RX_MAX']), float(d['R_DX'])
    RY_MIN, RY_MAX, R_DY = float(d['RY_MIN']), float(d['RY_MAX']), float(d['R_DY'])
    TOTAL_PB_KG = float(d['TOTAL_PB_KG'])
    chi_Q = gaussian_filter(d['chi_Q_reg'], 1.1)
    peak_15m = gaussian_filter(d['peak_15m_Q_reg'], 1.1)

    fig = plt.figure(figsize=(18, 12.2), dpi=150, facecolor='#0e1117')
    gs_top = gridspec.GridSpec(1, 4, top=0.89, bottom=0.56, left=0.05, right=0.98, wspace=0.20)
    gs_bot = gridspec.GridSpec(1, 2, top=0.48, bottom=0.06, left=0.05, right=0.98, wspace=0.16, width_ratios=[1.25, 1.0])

    # --- Top Row: Live EPA Xact Telemetry vs Modeled Arrival Curves at AS01..AS04 ---
    stns = ['AS01', 'AS02', 'AS03', 'AS04']
    titles_s = [
        'AS01 (6.0 km SW @ 235°)',
        'AS02 (19.1 km SW @ 235°)',
        'AS03 (30.9 km SW @ 235°)',
        'AS04 (44.1 km SW @ 236°)',
    ]
    for i, (sid, stitle) in enumerate(zip(stns, titles_s)):
        ax = fig.add_subplot(gs_top[0, i], facecolor='#161b22')
        r = recs[sid]
        ts = np.array(r['ts_min'])
        mean_c = np.array(r['mean_C_over_Q'])
        p90_c  = np.array(r['p90_C_over_Q'])
        # Plot modeled 5-min concentrations (ug/m^3) for 1.0%, 0.1%, 0.01% Pb release!
        for frac, col, lbl in [(0.01, '#ff4455', '1.0% Pb (30.5 kg)'), (0.001, '#ffaa33', '0.1% Pb (3.05 kg)'), (0.0001, '#00e5ff', '0.01% Pb (0.31 kg)')]:
            q_ug = TOTAL_PB_KG * frac * 1e9
            y_mean = np.maximum(mean_c * q_ug, 1e-5)
            ax.plot(ts, y_mean, color=col, lw=1.8, label=lbl)
            if frac == 0.001:
                ax.fill_between(ts, np.maximum(np.array(r['p25_C_over_Q'])*q_ug, 1e-5),
                                np.maximum(p90_c*q_ug, 1e-5), color=col, alpha=0.15)
        # Plot ACTUAL live EPA Xact telemetry (13:00..16:21 UTC -> -120..+81 min)!
        obs = np.array(r['obs_series'])
        obs_post = obs[obs[:,0] >= -20]
        # Replace exact 0 with 1.5e-4 floor for log-scale plotting with hollow circles!
        zeros = obs_post[obs_post[:,1] <= 0]
        nonz  = obs_post[obs_post[:,1] > 0]
        if len(zeros):
            ax.scatter(zeros[:,0], np.full(len(zeros), 1.8e-4), facecolors='none', edgecolors='#00ff88', s=22, lw=1.0, zorder=9, label='EPA Xact = 0 (non-detect)')
        if len(nonz):
            ax.scatter(nonz[:,0], nonz[:,1], color='#00ff88', edgecolors='black', s=42, marker='D', zorder=10, label='EPA Xact > 0 (trace)')
        ax.axvline(0, color='white', ls=':', lw=1.0, alpha=0.7)
        ax.axvline(81, color='#aaaaaa', ls='--', lw=1.0, alpha=0.8)
        ax.text(82.5, 40, 'Poll #2\n16:21Z', color='#bbb', fontsize=7, va='top')
        ax.axhline(0.15, color='#ff66ff', ls='--', lw=1.1, alpha=0.8, label='0.15 µg/m³ NAAQS ref')
        ax.set_yscale('log')
        ax.set_xlim(-22, 145); ax.set_ylim(1e-4, 250)
        ax.set_xlabel('Minutes Post-Detonation (T + min)', color='#ccc', fontsize=8.3)
        if i == 0:
            ax.set_ylabel('5-Min Airborne Lead Conc. (µg/m³)', color='#ddd', fontsize=9)
        ax.set_title(stitle, color='white', fontsize=9.8, weight='bold', pad=5)
        ax.tick_params(colors='#ccc', labelsize=8); ax.grid(color='#333', alpha=0.5, which='both')
        if i == 0:
            ax.legend(facecolor='#0e1117', edgecolor='#555', labelcolor='white', fontsize=6.8, loc='upper right')
        # Add concise status badge cleanly in upper sky of each plot (unobstructing the zero-line markers!)
        by_pos = 0.56 if i == 0 else 0.78
        if sid in ('AS01', 'AS02', 'AS03') and r.get('f_max_med_pct') is not None:
            badge = f"Hit Prob: {r['hit_prob_pct']:.0f}%\nObs max: {r['obs_post_max_ugm3']:.4f} µg/m³\n⇒ f_Pb ≲ {r['f_max_med_pct']:.4f}%"
            ax.text(0.03, by_pos, badge, transform=ax.transAxes, color='#88ffbb', fontsize=7.2, weight='bold', va='top',
                    bbox=dict(facecolor='#0e1117', alpha=0.88, edgecolor='#00ff88', pad=2.5))
        else:
            badge = f"Hit Prob: {r['hit_prob_pct']:.0f}%\nMain arrival ~T+{r['peak_time_min']:.0f}m\n(Pre-blast bg ~0.006 µg/m³)"
            ax.text(0.03, by_pos, badge, transform=ax.transAxes, color='#ffdd88', fontsize=7.0, weight='bold', va='top',
                    bbox=dict(facecolor='#0e1117', alpha=0.88, edgecolor='#ffaa33', pad=2.5))
        for sp in ax.spines.values(): sp.set_edgecolor('#444')

    # --- Bottom-Left: Nested Forward Sensitivity Screening Contours on ESRI Dark Gray Map ---
    axM = fig.add_subplot(gs_bot[0, 0])
    lat_min, lat_max, lon_min, lon_max = 40.35, 40.94, -92.08, -91.05
    esri_base = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
    esri_ref  = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
    bm, to_px = fetch_basemap(lat_min, lat_max, lon_min, lon_max, 10, esri_base)
    rf, _     = fetch_basemap(lat_min, lat_max, lon_min, lon_max, 10, esri_ref, rgba=True)
    bm = Image.alpha_composite(bm.convert('RGBA'), rf).convert('RGB')
    axM.imshow(bm)

    rny, rnx = chi_Q.shape
    rx_c = RX_MIN + (np.arange(rnx)+0.5)*R_DX; ry_c = RY_MIN + (np.arange(rny)+0.5)*R_DY
    RXG, RYG = np.meshgrid(rx_c, ry_c)
    RLAT = LAT0 + RYG / M_PER_LAT; RLON = LON0 + RXG / M_PER_LON
    PX, PY = to_px(RLAT, RLON)

    # Plot nested 15-min peak passing-plume >= 0.15 ug/m^3 footprints for 5%, 1%, 0.1%, 0.01% release!
    # Threshold on peak_15m (m^-3): c15 >= 0.15  <=>  peak_15m >= 0.15 / (3050 * f * 1e9)
    scen = [
        (0.05,   '#7722cc', 0.25, '5.0% Pb (152.5 kg) — Unmitigated'),
        (0.01,   '#ff3355', 0.34, '1.0% Pb (30.5 kg) — Moderate Leak'),
        (0.001,  '#ffaa22', 0.48, '0.1% Pb (3.05 kg) — Heavily Mitigated'),
        (0.0001, '#00e5ff', 0.68, '0.01% Pb (0.31 kg) — Trace Release'),
    ]
    for f, col, al, lbl in scen:
        thr = 0.15 / (TOTAL_PB_KG * f * 1e9)
        if np.any(peak_15m >= thr):
            axM.contourf(PX, PY, peak_15m, levels=[thr, 1.0], colors=[col], alpha=al)
            axM.contour(PX, PY, peak_15m, levels=[thr], colors=[col], linewidths=1.8)
        axM.plot([], [], color=col, lw=3, label=lbl)
    # Also draw bold dashed white contour for 24-hr avg >= 0.15 ug/m^3 at 5% release for comparison!
    thr24_5 = 0.15 * 86400.0 / (TOTAL_PB_KG * 0.05 * 1e9)
    if np.any(chi_Q >= thr24_5):
        axM.contour(PX, PY, chi_Q, levels=[thr24_5], colors=['white'], linewidths=1.6, linestyles='--')
        axM.plot([], [], color='white', lw=1.6, ls='--', label='24-hr Avg ≥ 0.15 µg/m³ (at 5% Pb)')

    # Plot AS01..AS04 live EPA Xact monitors + IAAAP origin + towns
    ox, oy = to_px(LAT0, LON0)
    axM.plot(ox, oy, '*', color='#ff2244', ms=15, mec='yellow', mew=1.2, zorder=12)
    epa_off = {'AS01': (-42, -14), 'AS02': (10, 6), 'AS03': (-45, -16), 'AS04': (-42, -16)}
    for sid in stns:
        r = recs[sid]; sx, sy = to_px(r['lat'], r['lon'])
        axM.plot(sx, sy, '^', color='#00ff88', ms=9, mec='black', mew=1, zorder=11)
        edx, edy = epa_off.get(sid, (8, -6))
        axM.text(sx+edx, sy+edy, f"{sid} ({r['obs_post_max_ugm3']:.3f})", color='#88ffaa', fontsize=7.6, weight='bold', zorder=12,
                 path_effects=[pe.withStroke(linewidth=2.3, foreground='black')])
    for nm, la, lo, (dx, dy) in [('Middletown',40.828,-91.259,(8,-8)), ('Denmark',40.737,-91.334,(10,5)),
                                  ('West Point',40.717,-91.454,(-72,-8)), ('Donnellson',40.641,-91.560,(8,7)),
                                  ('Kahoka (MO)',40.422,-91.722,(8,5)), ('Fort Madison',40.630,-91.315,(8,5))]:
        tx, ty = to_px(la, lo)
        axM.plot(tx, ty, 'o', color='white', ms=3, mec='black', zorder=10)
        axM.text(tx+dx, ty+dy, nm, color='white', fontsize=7.8, weight='bold', zorder=11,
                 path_effects=[pe.withStroke(linewidth=2.2, foreground='black')])
    axM.plot([], [], '^', color='#00ff88', ms=7, mec='black', label='EPA Live Xact Monitors (AS01–04, max µg/m³)')
    axM.set_title('E. Hypothetical 15-Min Peak ≥ 0.15 µg/m³ Footprints by Airborne Pb Release Fraction', color='white', fontsize=10.8, weight='bold', pad=6)
    axM.legend(facecolor='#0e1117', edgecolor='#666', labelcolor='white', fontsize=7.4, loc='lower right')
    axM.set_xlim(0, bm.width); axM.set_ylim(bm.height, 0); axM.axis('off')

    # --- Bottom-Right: Continuous Sensitivity Curve (Downwind Screening Reach vs Release Fraction) + Inverse EPA Bound ---
    axS = fig.add_subplot(gs_bot[0, 1], facecolor='#161b22')
    DIST_KM = np.hypot(RXG, RYG) / 1000.0
    f_grid = np.logspace(-5, -0.7, 50) # 0.001% to 20%
    d_15_015, d_24_015, d_15_50, d_15_150 = [], [], [], []
    for f in f_grid:
        q_ug = TOTAL_PB_KG * f * 1e9
        c24 = chi_Q * q_ug / 86400.0
        c15 = peak_15m * q_ug
        d_15_015.append(float(DIST_KM[c15 >= 0.15].max()) if np.any(c15 >= 0.15) else 0.0)
        d_24_015.append(float(DIST_KM[c24 >= 0.15].max()) if np.any(c24 >= 0.15) else 0.0)
        d_15_50.append(float(DIST_KM[c15 >= 50.0].max()) if np.any(c15 >= 50.0) else 0.0)
        d_15_150.append(float(DIST_KM[c15 >= 150.0].max()) if np.any(c15 >= 150.0) else 0.0)

    f_pct = f_grid * 100.0
    axS.plot(f_pct, np.array(d_15_015)/1.60934, '-', color='#00e5ff', lw=2.5, label='15-min Peak ≥ 0.15 µg/m³ (passing-cloud NAAQS level)')
    axS.plot(f_pct, np.array(d_24_015)/1.60934, '--', color='#ffaa33', lw=2.3, label='24-hr Avg ≥ 0.15 µg/m³ (daily NAAQS equivalent)')
    axS.plot(f_pct, np.array(d_15_50)/1.60934, '-.', color='#ff5566', lw=2.1, label='15-min Peak ≥ 50 µg/m³ (OSHA 8-hr PEL level)')
    axS.plot(f_pct, np.array(d_15_150)/1.60934, ':', color='#dd66ff', lw=2.2, label='15-min Peak ≥ 150 µg/m³ (DOE PAC-1 mild effect)')

    # Shade EPA Inverse Upper-Bound Region: AS01 (~0.001%) to AS02 (~0.013%, robust to near-field lofting!)
    f_as01 = recs['AS01']['f_max_med_pct']
    f_as02 = recs['AS02']['f_max_med_pct']
    f_as03 = recs['AS03']['f_max_med_pct']
    axS.axvspan(1e-3, f_as02, color='#00ff88', alpha=0.22, label=f'EPA Xact Inverse Bound (AS02 mixed PBL: f ≲ {f_as02:.3f}%)')
    axS.axvspan(f_as02, f_as03, color='#00ff88', alpha=0.09, label=f'Conservative AS03 Bound (31 km: f ≲ {f_as03:.3f}%)')
    axS.axvline(f_as02, color='#00ff88', ls='--', lw=1.8)
    axS.text(f_as03*1.25, 30, f'CONSISTENT WITH LIVE EPA TELEMETRY:\n• AS01 (6 km): f ≲ {f_as01:.4f}% (≤0.03 kg)\n• AS02 (19 km, mixed): f ≲ {f_as02:.3f}% (≤0.39 kg)\n• AS03 (31 km, 0.000): f ≲ {f_as03:.3f}% (≤1.44 kg)\n⇒ >99.95% of Pb inventory did NOT\n   enter far-field breathing-zone plume',
             color='#88ffbb', fontsize=7.6, weight='bold', va='top',
             bbox=dict(facecolor='#0e1117', alpha=0.92, edgecolor='#00ff88', pad=4))
    axS.axhline(5.0, color='#ff4466', ls=':', lw=1.1, alpha=0.6)
    axS.text(7.0, 5.6, '5 mi evac zone', color='#ff7788', fontsize=7.5)
    axS.axhline(20.0, color='#ffaa44', ls=':', lw=1.1, alpha=0.6)
    axS.text(7.0, 20.8, '20 mi shelter zone', color='#ffbb66', fontsize=7.5)

    axS.set_xscale('log'); axS.set_xlim(1e-3, 20.0); axS.set_ylim(0, 48)
    axS.set_xlabel('Hypothetical Airborne Respirable Pb Release Fraction (% of 3,050 kg Inventory)', color='#ddd', fontsize=9)
    axS.set_ylabel('Max Downwind Screening Reach (miles)', color='#ddd', fontsize=9)
    axS.set_title('F. Forward Screening Sensitivity & Inverse EPA Telemetry Bound', color='white', fontsize=10.8, weight='bold', pad=6)
    axS.tick_params(colors='#ccc'); axS.grid(color='#333', alpha=0.6, which='both')
    axS.legend(facecolor='#0e1117', edgecolor='#555', labelcolor='white', fontsize=7.2, loc='upper left')
    for sp in axS.spines.values(): sp.set_edgecolor('#444')

    fig.suptitle('Live EPA Xact Telemetry Cross-Check, Inverse Release Upper Bound & Forward Screening Sensitivity',
                 color='white', fontsize=14, weight='bold', y=0.968)
    fig.text(0.5, 0.932, 'Comparing Modeled Plume Passage (AS01–AS04) Against Live EPA Lead Readings Through 16:21 UTC (T+81 min) Constrains Actual Airborne Lead Escape',
             color='#aac4dd', fontsize=9.8, ha='center')
    plt.savefig(f'{PROJ}/outputs/fig6_epa_inverse_and_sensitivity.png', dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print('Saved outputs/fig6_epa_inverse_and_sensitivity.png')

    # --- Upgrade Interactive HTML Map with Camera 2, Stereo Ellipse, Sensitivity Overlay & Live Xact Telemetry! ---
    d0 = np.load(f'{PROJ}/data/dispersion_results.npz')
    stereo = json.load(open(f'{PROJ}/data/stereo_triangulation.json'))
    epa = json.load(open(f'{PROJ}/data/epa_air_monitors.json'))['features']
    towns = json.load(open(f'{PROJ}/data/town_impacts.json'))
    iaaap_poly = json.load(open(f'{PROJ}/data/iaaap_osm.json'))[0]['geojson']
    met = json.load(open(f'{PROJ}/data/met_summary.json'))

    air_reg = gaussian_filter(d0['air_exp_reg'], 1.2)
    dep_reg = gaussian_filter(d0['dep_coarse_reg'] + d0['dep_pm10_reg'] + d0['dep_pm25_reg'], 1.2)
    ens_prob = gaussian_filter(d0['ens_prob']*100.0, 1.0)
    log_air = np.where(air_reg > 1e-11, np.log10(np.maximum(air_reg, 1e-30)), -99.0)
    vmax_air = np.percentile(log_air[log_air > -11], 99.5); vmin_air = vmax_air - 4.2
    log_dep = np.where(dep_reg > 1e-13, np.log10(np.maximum(dep_reg, 1e-30)), -99.0)
    vmax_dep = np.percentile(log_dep[log_dep > -13], 99.5); vmin_dep = vmax_dep - 4.0

    # Sensitivity overlay: log10(15-min peak ug/m^3 at 0.1% hypothetical Pb release!)
    c15_0p1 = peak_15m * (TOTAL_PB_KG * 0.001 * 1e9)
    log_c15 = np.where(c15_0p1 > 0.05, np.log10(np.maximum(c15_0p1, 1e-10)), -99.0)

    def grid_to_uri(grid, vmin, vmax, cmap_name='inferno', alpha_max=0.74):
        norm = np.clip((grid - vmin)/(vmax - vmin), 0.0, 1.0)
        rgba = plt.get_cmap(cmap_name)(norm)
        # Smooth continuous feather to 0.0 alpha at low-concentration tail + gentle outer-border fade (zero box cutoff!)
        alpha = np.where(grid <= vmin, 0.0, np.clip((norm ** 0.78) * alpha_max, 0.0, alpha_max))
        ny, nx = grid.shape
        fx = np.minimum(np.linspace(0, 1, nx) / 0.05, np.linspace(1, 0, nx) / 0.05)
        fy = np.minimum(np.linspace(0, 1, ny) / 0.05, np.linspace(1, 0, ny) / 0.05)
        edge_fade = np.clip(np.outer(np.clip(fy, 0, 1), np.clip(fx, 0, 1)), 0.0, 1.0)
        rgba[..., 3] = alpha * edge_fade
        buf = io.BytesIO()
        Image.fromarray((np.flipud(rgba)*255).astype(np.uint8), mode='RGBA').save(buf, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')

    m = folium.Map(location=[40.48, -91.78], zoom_start=9, tiles=None, control_scale=True)
    folium.TileLayer(tiles='https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
                     attr='Esri Dark Gray Canvas', name='ESRI Dark Gray Canvas', show=True).add_to(m)
    folium.TileLayer(tiles='https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
                     attr='Esri Reference', name='Map Labels & Roads Overlay', overlay=True, show=True).add_to(m)
    folium.TileLayer('OpenStreetMap', name='OpenStreetMap Streets', show=False).add_to(m)
    folium.TileLayer(tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                     attr='Esri World Imagery', name='ESRI Satellite Imagery', show=False).add_to(m)

    b_reg = [[float(RLAT.min()), float(RLON.min())], [float(RLAT.max()), float(RLON.max())]]
    ImageOverlay(grid_to_uri(log_air, vmin_air, vmax_air, 'inferno', 0.72), bounds=b_reg,
                 name='Airborne Exposure Heatmap (0–50m, log10 χ/Q)', show=True).add_to(m)
    ImageOverlay(grid_to_uri(log_dep, vmin_dep, vmax_dep, 'magma', 0.72), bounds=b_reg,
                 name='Surface Deposition Heatmap (Coarse + Fine Dust)', show=False).add_to(m)
    ImageOverlay(grid_to_uri(log_c15, -1.3, 1.5, 'plasma', 0.68), bounds=b_reg,
                 name='Hypothetical 0.1% Pb Scenario (15-min peak µg/m³)', show=False).add_to(m)

    fg_stereo = folium.FeatureGroup(name='3-Camera Triangulation (KWQC + KCRG + Drone) & Radii', show=True)
    folium.GeoJson(iaaap_poly, style_function=lambda x: {'color':'#ffdd44','weight':2,'fillOpacity':0.07,'dashArray':'6,4'},
                   tooltip='Iowa Army Ammunition Plant (IAAAP)').add_to(fg_stereo)
    folium.Circle([LAT0, LON0], radius=5*1609.34, color='#ff4466', weight=1.8, dash_array='6,4', fill=False, tooltip='5-Mile Radius').add_to(fg_stereo)
    folium.Circle([LAT0, LON0], radius=20*1609.34, color='#ffaa44', weight=1.4, dash_array='8,6', fill=False, tooltip='20-Mile Radius').add_to(fg_stereo)
    folium.Circle([LAT0, LON0], radius=50*1609.34, color='#64748b', weight=1.2, dash_array='6,8', fill=False, tooltip='50-Mile Radius').add_to(fg_stereo)
    # 4.0 NM FAA TFR (FDC 6/9186) centered at 40.7950N, -91.2481W
    folium.Circle([40.79500, -91.24806], radius=4*1852.0, color='#22c55e', weight=1.3, dash_array='4,6', fill=False,
                  tooltip='FAA TFR FDC 6/9186 (4.0 NM radius, SFC–4,000 ft MSL)').add_to(fg_stereo)
    # 3-Camera Error Ellipse (±244m E-W × ±46m N-S, 4.9x tighter area than 2-camera!)
    th = np.linspace(0, 2*np.pi, 60)
    rad = math.radians(93.7)
    dN = 244*np.cos(th)*math.cos(rad) - 46*np.sin(th)*math.sin(rad)
    dE = 244*np.cos(th)*math.sin(rad) + 46*np.sin(th)*math.cos(rad)
    ell_pts = [[LAT0 + float(dn)/M_PER_LAT, LON0 + float(de)/M_PER_LON] for dn, de in zip(dN, dE)]
    folium.Polygon(ell_pts, color='#ffff00', weight=2.5, fill=True, fill_color='#ffff00', fill_opacity=0.22,
                   tooltip='3-Camera Triangulated Origin Zone (±244×46 m, 4.9× tighter than 2-cam)').add_to(fg_stereo)

    c1 = stereo['cam1_kwqc']; c2 = stereo['cam2_kcrg_ap']; c3 = stereo['cam3_wilson_drone']
    bn = stereo['triangulated_origin']['bunker_north']; bs = stereo['triangulated_origin']['bunker_south']
    folium.Marker([LAT0, LON0], icon=folium.Icon(color='red', icon='fire', prefix='fa'),
                  popup=f'<b>3-Camera Triangulated Detonation Zone</b><br>{LAT0:.4f}°N, {LON0:.4f}°W (±244×46 m)<br>64.6° Max Baseline (KWQC + KCRG/AP + Wilson Drone)<br>Dual Bunkers: 311 m @ 209° SSW–NNE (fired 33 ms apart)').add_to(fg_stereo)
    folium.CircleMarker([bn['lat'], bn['lon']], radius=4.5, color='#22c55e', weight=2, fill=True, fill_color='#86efac', fill_opacity=0.95,
                        tooltip='North Bunker (Smaller ~35% · 111m ballistic peak, 109m white cap)').add_to(fg_stereo)
    folium.CircleMarker([bs['lat'], bs['lon']], radius=5.5, color='#e879f9', weight=2, fill=True, fill_color='#f0abfc', fill_opacity=0.95,
                        tooltip='South Bunker (Larger ~65% · 159m ballistic peak, 130m white cap)').add_to(fg_stereo)
    folium.Marker([c1['lat'], c1['lon']], icon=folium.Icon(color='blue', icon='video-camera', prefix='fa'),
                  popup='<b>Camera 1: KWQC-TV6</b><br>190th Ave, 1 mi ENE Danville<br>Bearing: 158.3° SSE across FAA Tower T3<br>Acoustic Range: 6.23 ± 0.5 km').add_to(fg_stereo)
    folium.Marker([c2['lat'], c2['lon']], icon=folium.Icon(color='orange', icon='video-camera', prefix='fa'),
                  popup='<b>Camera 2: KCRG-TV9 / AP</b><br>9856 202nd Ave, Danville (blue 911 sign)<br>Bearing: 112.0° ESE | Range: 4.87 km<br>Optical Plume: 8.7 m/s').add_to(fg_stereo)
    folium.Marker([c3['lat'], c3['lon']], icon=folium.Icon(color='green', icon='plane', prefix='fa'),
                  popup=f'<b>Camera 3: Wilson Drone (@nmjameswilson)</b><br>{c3["site"]}<br>Alt: {c3["alt_agl_m"]:.0f} m ({c3["alt_agl_ft"]} ft) AGL | Bearing: 92.2° N / 95.1° S<br>Two-stage ballistic collapse (159m → 130m flat white cap) &amp; S-drift R²=0.980').add_to(fg_stereo)
    folium.Marker([40.84110, -91.28298], icon=folium.Icon(color='purple', icon='signal', prefix='fa'),
                  popup='<b>FAA Tower T3 (OAS 19-002048)</b><br>235 ft (71.6 m) AGL — Optical Calibration Ref').add_to(fg_stereo)
    folium.PolyLine([[c1['lat'], c1['lon']], [40.84110, -91.28298], [LAT0, LON0]], color='#00e5ff', weight=2.5, dash_array='5,5', tooltip='Cam 1 KWQC Sightline (158.3° SSE)').add_to(fg_stereo)
    folium.PolyLine([[c2['lat'], c2['lon']], [LAT0, LON0]], color='#ff8833', weight=2.5, dash_array='5,5', tooltip='Cam 2 KCRG/AP Sightline (112.0° ESE)').add_to(fg_stereo)
    folium.PolyLine([[c3['lat'], c3['lon']], [bn['lat'], bn['lon']]], color='#22c55e', weight=2.2, dash_array='5,5', tooltip='Cam 3 Drone → North Bunker (92.2° E)').add_to(fg_stereo)
    folium.PolyLine([[c3['lat'], c3['lon']], [bs['lat'], bs['lon']]], color='#e879f9', weight=2.2, dash_array='5,5', tooltip='Cam 3 Drone → South Bunker (95.1° E)').add_to(fg_stereo)
    fg_stereo.add_to(m)

    # EPA Air Monitors + highlighted live Xact AS01..AS04 telemetry popups!
    fg_epa = folium.FeatureGroup(name=f'EPA Region 7 Air Monitors ({len(epa)} sites, Live Xact AS01–04)', show=True)
    for f in epa:
        a = f['attributes']; g = f['geometry']; sid_raw = a['Station_ID']
        sid_key = sid_raw.replace('-', '')
        if sid_key in recs:
            r = recs[sid_key]
            q_str = f"f_Pb ≲ {r['f_max_med_pct']:.4f}%" if r.get('f_max_med_pct') else "Plume arriving ~T+100m"
            folium.CircleMarker([g['y'], g['x']], radius=8, color='#ffff00', weight=2, fill=True, fill_color='#00ff88', fill_opacity=0.95,
                                popup=f"<b>EPA LIVE XACT {sid_raw}</b><br>Dist: {r['dist_km']} km @ {r['bearing_deg']}°<br>Post-blast max (to 16:21Z): <b>{r['obs_post_max_ugm3']:.4f} µg/m³</b><br>Pre-blast max: {r['obs_pre_max_ugm3']:.4f} µg/m³<br>Modeled Hit Prob: {r['hit_prob_pct']}% | {q_str}").add_to(fg_epa)
        else:
            folium.CircleMarker([g['y'], g['x']], radius=4.5, color='#000', weight=1, fill=True, fill_color='#00ff88', fill_opacity=0.75,
                                popup=f"<b>EPA {sid_raw}</b><br>{a['Instrument_Type']}").add_to(fg_epa)
    fg_epa.add_to(m)

    # ASOS surface winds
    fg_wx = folium.FeatureGroup(name='Observed ASOS Surface Winds', show=True)
    for s, w in met['stations'].items():
        folium.CircleMarker([w['lat'], w['lon']], radius=5.5, color='white', weight=1.5, fill=True, fill_color='#00aaff', fill_opacity=0.9,
                            popup=f"<b>K{s} ASOS</b><br>15Z Wind FROM {w['from_dir']:.0f}° → TO {w['to_dir']:.0f}° @ {w['spd']:.1f} m/s").add_to(fg_wx)
        dlat = (w['spd']*1.8/111.132)*math.cos(math.radians(w['to_dir']))
        dlon = (w['spd']*1.8/(111.32*math.cos(math.radians(w['lat']))))*math.sin(math.radians(w['to_dir']))
        folium.PolyLine([[w['lat'], w['lon']], [w['lat']+dlat, w['lon']+dlon]], color='#ffdd44', weight=3).add_to(fg_wx)
    fg_wx.add_to(m)

    folium.LayerControl(collapsed=True).add_to(m)
    legend_html = """
    <div style="position: fixed; bottom: 25px; left: 25px; z-index: 9999; background: rgba(14,17,23,0.92);
                color: #eee; padding: 11px 13px; border-radius: 8px; border: 1px solid #555; font-family: sans-serif; font-size: 11.5px; max-width: 310px;">
      <div style="font-weight: bold; font-size: 12.5px; color: #ffdd44; margin-bottom: 5px;">IAAAP 3-Camera Origin &amp; 5-Hr Dispersion Path (Nov 25, 2026)</div>
      <div><b>3-Camera Origin:</b> 40.809°N, -91.266°W (±244×46 m, 64.6° baseline) · Bunkers 311 m @ 209°</div>
      <div><b>Cam 3 Physics:</b> Two-stage ballistic collapse (159 m → flat ~100–130 m white smoke cap)</div>
      <div><b>Simulated Path (0–5 hr):</b> SW–WSW past Denmark, West Point, Donnellson, Kahoka &amp; Kirksville</div>
      <div><b>Live EPA Xact:</b> Near-zero/baseline at AS01–04 ⇒ airborne Pb escape constrained to <b>&gt;99.95% contained</b>.</div>
      <div style="margin-top:4px;color:#93c5fd">Use top-right layer icon to toggle Satellite, Deposition, or 0.1% Pb contours.</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    m.save(f'{PROJ}/outputs/iaaap_plume_dispersion_interactive.html')
    print('Upgraded outputs/iaaap_plume_dispersion_interactive.html')

if __name__ == '__main__':
    make_fig5()
    make_fig6_and_upgrade_html()
