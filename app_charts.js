/**
 * Main Dashboard Controller:
 *  - Loads site_data/scene_static.json, terrain_dem.json, plume_3d.json, live_telemetry.json
 *  - Powers the 3D WebGL Observatory (app3d.js)
 *  - Renders live Plotly EPA Xact arrival & release-fraction sensitivity charts
 *  - Supports direct browser live refresh from CORS-enabled EPA ArcGIS & IEM APIs!
 */
import { Plume3DObservatory } from './app3d.js';

const TOTAL_PB_KG = 3050.0;
const state = {
  staticData: null,
  liveData: null,
  selectedStn: 'AS01',
  releasePct: 0.1, // default 0.1% Pb on interactive slider
  obs3d: null,
};

function fmtTimePost(sec) {
  const s = Math.round(sec);
  if (s < 120) return `T + ${s}s`;
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return h > 0 ? `T + ${h}h ${String(rm).padStart(2, '0')}m` : `T + ${m}m`;
}

function fmtCstClock(sec) {
  const base = new Date('2026-11-25T15:00:00Z');
  const d = new Date(base.getTime() + sec * 1000);
  // CST = UTC - 6h
  const cst = new Date(d.getTime() - 6 * 3600 * 1000);
  const hh = cst.getUTCHours(), mm = String(cst.getUTCMinutes()).padStart(2, '0'), ss = String(cst.getUTCSeconds()).padStart(2, '0');
  const ampm = hh >= 12 ? 'PM' : 'AM';
  const h12 = ((hh + 11) % 12) + 1;
  return `${h12}:${mm}:${ss} ${ampm} CST`;
}

function renderHeroKPIs() {
  const sum = state.liveData?.summary || {};
  const xact = state.liveData?.epa_xact || {};
  document.getElementById('live-status-text').textContent =
    `Updated ${state.liveData.fetched_at_utc.replace('T', ' ').replace('Z', ' UTC')}`;

  const latestTs = sum.xact_latest_obs_utc ? sum.xact_latest_obs_utc.slice(11, 16) + ' UTC' : 'N/A';
  document.getElementById('kpi-xact-val').textContent = `${(sum.xact_max_post_ugm3 ?? 0).toFixed(4)} µg/m³`;
  document.getElementById('kpi-xact-sub').textContent = `Max post-blast across ${sum.xact_stations_active || 4} Xact sites (latest ${latestTs})`;

  const f2 = sum.as02_inverse_bound_pct, f3 = sum.as03_inverse_bound_pct;
  document.getElementById('kpi-bound-val').textContent = f2 ? `≲ ${f2.toFixed(3)}% Pb` : '≲ 0.013% Pb';
  document.getElementById('kpi-bound-sub').textContent =
    `>99.95% contained (AS02 ≤${(xact.AS02?.q_max_kg ?? 0.39).toFixed(2)} kg; AS03 ≲${(f3 ?? 0.047).toFixed(3)}%)`;

  if (sum.kbrl_wind_from_deg != null) {
    document.getElementById('kpi-wind-val').textContent = `${Math.round(sum.kbrl_wind_from_deg)}° → ${Math.round(sum.kbrl_wind_to_deg)}° @ ${sum.kbrl_wind_ms} m/s`;
    document.getElementById('kpi-wind-sub').textContent = `KBRL Burlington ASOS (${(sum.kbrl_valid_utc || '').slice(11, 16)} UTC) · ${(sum.kbrl_wind_ms * 2.237).toFixed(1)} mph`;
  }
  document.getElementById('kpi-net-val').textContent =
    `${sum.epa_monitors_deployed || 43} EPA · ${sum.asos_stations_live || 10} ASOS · ${sum.rwis_stations_live || 5} RWIS`;
}

function renderStationChart() {
  if (!window.Plotly || !state.staticData || !state.liveData) return;
  const sid = state.selectedStn;
  const mod = state.staticData.receptors_model[sid];
  const live = state.liveData.epa_xact[sid];
  if (!mod || !live) return;

  const ts = mod.ts_min;
  const qSelectedUg = TOTAL_PB_KG * (state.releasePct / 100.0) * 1e9;
  const ySelMean = mod.mean_C_over_Q.map((v) => Math.max(v * qSelectedUg, 1e-5));
  const ySelP90  = mod.p90_C_over_Q.map((v) => Math.max(v * qSelectedUg, 1e-5));
  const ySelP25  = mod.p25_C_over_Q.map((v) => Math.max(v * qSelectedUg, 1e-5));
  const yRef001  = mod.mean_C_over_Q.map((v) => Math.max(v * TOTAL_PB_KG * 0.0001 * 1e9, 1e-5));

  const postSeries = (live.series || []).filter((r) => r.min_post_blast >= -25);
  const zPts = postSeries.filter((r) => r.value <= 0);
  const nzPts = postSeries.filter((r) => r.value > 0);

  const traces = [
    {
      x: [...ts, ...[...ts].reverse()],
      y: [...ySelP90, ...[...ySelP25].reverse()],
      fill: 'toself',
      fillcolor: 'rgba(255, 170, 51, 0.16)',
      line: { color: 'transparent' },
      name: `Modeled IQR (25–90%) @ ${state.releasePct}%`,
      hoverinfo: 'skip',
    },
    {
      x: ts, y: ySelMean,
      mode: 'lines',
      line: { color: '#ffbb33', width: 2.8 },
      name: `Selected: ${state.releasePct}% Pb (${(TOTAL_PB_KG * state.releasePct / 100).toFixed(2)} kg)`,
    },
    {
      x: ts, y: yRef001,
      mode: 'lines',
      line: { color: '#00e5ff', width: 1.6, dash: 'dot' },
      name: 'Ref: 0.01% Pb (0.31 kg)',
    },
    {
      x: zPts.map((r) => r.min_post_blast),
      y: zPts.map(() => 1.8e-4),
      mode: 'markers',
      marker: { color: 'rgba(0,0,0,0)', size: 7, line: { color: '#00ff88', width: 1.5 } },
      name: 'Live EPA Xact = 0 (non-detect)',
      text: zPts.map((r) => `${r.ts_utc}: 0.0000 µg/m³`),
      hoverinfo: 'text',
    },
    {
      x: nzPts.map((r) => r.min_post_blast),
      y: nzPts.map((r) => r.value),
      mode: 'markers',
      marker: { color: '#00ff88', symbol: 'diamond', size: 10, line: { color: '#000', width: 1 } },
      name: 'Live EPA Xact > 0 (trace)',
      text: nzPts.map((r) => `${r.ts_utc}: ${r.value.toFixed(5)} µg/m³`),
      hoverinfo: 'text',
    },
  ];

  const layout = {
    paper_bgcolor: '#0b111b',
    plot_bgcolor: '#111927',
    font: { color: '#cbd5e1', family: 'Inter, sans-serif', size: 11.5 },
    margin: { t: 42, r: 18, b: 68, l: 62 },
    title: { text: `Live EPA Xact Telemetry vs. Modeled Plume Arrival — ${sid} (${live.dist_km} km @ ${live.bearing_deg}°)`, font: { size: 13.5, color: '#fff' } },
    xaxis: { title: 'Minutes Post-Detonation (T = 0 is 15:00 UTC / 9:00 AM CST)', gridcolor: '#1e2c40', range: [-25, 165] },
    yaxis: { title: '5-Min Airborne Lead (µg/m³)', type: 'log', gridcolor: '#1e2c40', range: [-4, 2.3] },
    shapes: [
      { type: 'line', x0: -25, x1: 165, y0: 0.15, y1: 0.15, line: { color: '#e879f9', width: 1.5, dash: 'dash' } },
      { type: 'line', x0: 0, x1: 0, y0: 1e-4, y1: 200, line: { color: '#ffffff', width: 1, dash: 'dot' } },
    ],
    annotations: [
      { x: 142, y: Math.log10(0.18), text: '0.15 µg/m³ NAAQS ref', showarrow: false, font: { color: '#f0abfc', size: 10 } },
    ],
    legend: { orientation: 'h', y: -0.28, font: { size: 10.5 } },
  };
  Plotly.react('chart-xact', traces, layout, { responsive: true, displayModeBar: false });

  // Update dynamic consistency badge beside slider!
  const peakPred = Math.max(...mod.mean_C_over_Q) * qSelectedUg;
  const obsMax = live.post_blast_max;
  const badgeEl = document.getElementById('slider-verdict');
  if (live.f_max_pct && state.releasePct > live.f_max_pct * 1.5 && live.obs_count > 10 && sid !== 'AS04') {
    badgeEl.className = 'badge badge-red';
    badgeEl.textContent = `Ruled Out at ${sid}: predicts ${peakPred.toFixed(3)} µg/m³ vs ${obsMax.toFixed(4)} observed`;
  } else {
    badgeEl.className = 'badge badge-green';
    badgeEl.textContent = `Consistent with ${sid} telemetry (pred peak ${peakPred.toFixed(4)} vs ${obsMax.toFixed(4)} obs)`;
  }
}

function renderSensitivityChart() {
  if (!window.Plotly || !state.staticData) return;
  const sw = state.staticData.sensitivity_sweep;
  const fracs = sw.map((r) => r.release_pct);
  const pk5 = sw.map((r) => r.max_c15_5mi_ugm3);
  const pk20 = sw.map((r) => r.max_c15_20mi_ugm3);
  const d24 = sw.map((r) => r.c24_gt_0p15_km / 1.60934);

  const traces = [
    { x: fracs, y: pk5, mode: 'lines+markers', name: '15-Min Peak @ 5 mi (µg/m³)', line: { color: '#00e5ff', width: 2.6 }, marker: { size: 7 } },
    { x: fracs, y: pk20, mode: 'lines+markers', name: '15-Min Peak @ 20 mi (µg/m³)', line: { color: '#ffbb33', width: 2.4 }, marker: { size: 7 } },
    { x: fracs, y: d24, mode: 'lines+markers', yaxis: 'y2', name: '24-hr Avg ≥0.15 Reach (mi)', line: { color: '#ff4757', width: 2.2, dash: 'dash' } },
  ];
  const layout = {
    paper_bgcolor: '#0b111b',
    plot_bgcolor: '#111927',
    font: { color: '#cbd5e1', family: 'Inter, sans-serif', size: 11.5 },
    margin: { t: 42, r: 54, b: 68, l: 58 },
    title: { text: 'Downwind Concentration Scaling vs. Airborne Release Fraction', font: { size: 13.5, color: '#fff' } },
    xaxis: { title: 'Airborne Respirable Pb Release (% of 3,050 kg)', type: 'log', gridcolor: '#1e2c40' },
    yaxis: { title: '15-Min Peak Conc. (µg/m³)', type: 'log', gridcolor: '#1e2c40' },
    yaxis2: { title: '24-hr ≥0.15 Reach (mi)', overlaying: 'y', side: 'right', showgrid: false, range: [0, 8] },
    shapes: [
      { type: 'line', x0: state.releasePct, x1: state.releasePct, y0: 0, y1: 1, yref: 'paper', line: { color: '#00ff88', width: 2, dash: 'dot' } },
    ],
    legend: { orientation: 'h', y: -0.28, font: { size: 10.5 } },
  };
  Plotly.react('chart-sensitivity', traces, layout, { responsive: true, displayModeBar: false });
}

function renderLiveTables() {
  // 1. Live EPA Xact Table
  const xactBody = document.getElementById('tbl-xact-body');
  xactBody.innerHTML = '';
  for (const [sid, s] of Object.entries(state.liveData.epa_xact || {})) {
    const tr = document.createElement('tr');
    const stBadge = s.post_blast_max <= 0.003
      ? '<span class="badge badge-green">Baseline / Zero</span>'
      : '<span class="badge badge-amber">Trace Ambient</span>';
    tr.innerHTML = `
      <td><strong>${sid}</strong></td>
      <td>${s.dist_km} km @ ${s.bearing_deg}°</td>
      <td>${s.obs_count}</td>
      <td>${(s.latest_ts_utc || '').replace('T', ' ').replace('Z', 'Z')}</td>
      <td><code>${(s.latest_value ?? 0).toFixed(4)} µg/m³</code></td>
      <td><code>${s.pre_blast_max.toFixed(4)}</code> / <code>${s.post_blast_max.toFixed(4)}</code></td>
      <td>${s.f_max_pct ? `≲ ${s.f_max_pct.toFixed(4)}% (≤${s.q_max_kg} kg)` : 'Plume arriving ~T+98m'}</td>
      <td>${stBadge}</td>`;
    xactBody.appendChild(tr);
  }

  // 2. Live ASOS + RWIS Weather Table
  const wxBody = document.getElementById('tbl-wx-body');
  wxBody.innerHTML = '';
  const rows = [];
  for (const [k, st] of Object.entries(state.liveData.asos || {})) {
    const l = st.latest || {};
    rows.push({
      net: 'ASOS', id: k, dist: st.dist_km, brg: st.bearing_deg,
      time: (l.valid_utc || '').slice(11, 16) + ' UTC',
      dir: l.wind_from_deg != null ? `${Math.round(l.wind_from_deg)}° → ${Math.round(l.wind_to_deg)}°` : '—',
      spd: l.wind_ms != null ? `${l.wind_ms} m/s (${(l.wind_ms * 2.237).toFixed(1)} mph)` : '—',
      temp: l.temp_c != null ? `${l.temp_c.toFixed(1)}°C` : '—',
    });
  }
  for (const [k, rw] of Object.entries(state.liveData.rwis || {})) {
    rows.push({
      net: 'IA RWIS', id: `${k} (${rw.name})`, dist: rw.dist_km, brg: rw.bearing_deg,
      time: (rw.valid_utc || '').slice(11, 16) + ' UTC',
      dir: rw.wind_from_deg != null ? `${Math.round(rw.wind_from_deg)}° → ${Math.round(rw.wind_to_deg)}°` : '—',
      spd: rw.wind_ms != null ? `${rw.wind_ms} m/s (${(rw.wind_ms * 2.237).toFixed(1)} mph)` : '—',
      temp: rw.temp_f != null ? `${((rw.temp_f - 32) * 5 / 9).toFixed(1)}°C` : '—',
    });
  }
  rows.sort((a, b) => a.dist - b.dist);
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><span class="badge ${r.net === 'ASOS' ? 'badge-cyan' : 'badge-amber'}">${r.net}</span></td>
      <td><strong>${r.id}</strong></td>
      <td>${r.dist} km @ ${r.brg}°</td>
      <td>${r.time}</td>
      <td>${r.dir}</td>
      <td>${r.spd}</td>
      <td>${r.temp}</td>`;
    wxBody.appendChild(tr);
  }

  // 3. Community Table
  const tBody = document.getElementById('tbl-towns-body');
  if (tBody && tBody.children.length === 0) {
    for (const t of state.staticData.towns) {
      const probBadge = t.ens_prob_pct >= 60 ? 'badge-red' : (t.ens_prob_pct >= 15 ? 'badge-amber' : 'badge-green');
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${t.town}</strong></td>
        <td>${t.dist_mi} mi (${t.dist_km} km) @ ${t.bearing_deg}°</td>
        <td><span class="badge ${probBadge}">${t.ens_prob_pct}%</span></td>
        <td>${t.mean_arrival_min != null ? `~${fmtCstClock(t.mean_arrival_min * 60)} (+${Math.round(t.mean_arrival_min)}m)` : 'Outside path'}</td>
        <td><code>${t.chi_over_Q_s_m3 > 0 ? t.chi_over_Q_s_m3.toExponential(2) : '0'}</code></td>`;
      tBody.appendChild(tr);
    }
  }
}

async function liveBrowserRefresh() {
  const btn = document.getElementById('btn-live-refresh');
  btn.disabled = true;
  btn.textContent = 'Refreshing...';
  try {
    // 1. Re-fetch latest daemon-published JSON (cache-busted)
    const r0 = await fetch(`site_data/live_telemetry.json?t=${Date.now()}`);
    if (r0.ok) state.liveData = await r0.json();

    // 2. Direct live CORS query to EPA Region 7 ArcGIS FeatureServer for zero-latency Xact readings!
    const epaUrl = 'https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/IAAAP_Xact_Data_Public_Viewer/FeatureServer/0/query?where=1%3D1&orderByFields=Mon_Date_Time+DESC&outFields=*&returnGeometry=true&outSR=4326&resultRecordCount=400&f=json';
    const rEpa = await fetch(epaUrl);
    if (rEpa.ok) {
      const ej = await rEpa.json();
      const by = {};
      for (const ft of (ej.features || [])) {
        const a = ft.attributes || {};
        const sid = String(a.Air_Station_ID || '').trim();
        if (!sid || a.Mon_Date_Time == null) continue;
        const dt = new Date(a.Mon_Date_Time);
        const rec = {
          ts_utc: dt.toISOString().replace('.000Z', 'Z'),
          min_post_blast: Math.round(((a.Mon_Date_Time - Date.parse('2026-11-25T15:00:00Z')) / 60000) * 10) / 10,
          value: Number(a.Mon_Results || 0),
        };
        by[sid] = by[sid] || [];
        by[sid].push(rec);
      }
      for (const [sid, list] of Object.entries(by)) {
        if (!state.liveData.epa_xact[sid]) continue;
        const dedup = Object.values(Object.fromEntries(list.map((r) => [r.ts_utc, r]))).sort((a, b) => a.ts_utc.localeCompare(b.ts_utc));
        const st = state.liveData.epa_xact[sid];
        st.series = dedup;
        st.obs_count = dedup.length;
        st.latest_ts_utc = dedup[dedup.length - 1].ts_utc;
        st.latest_value = dedup[dedup.length - 1].value;
        st.post_blast_max = Math.max(0, ...dedup.filter((r) => r.min_post_blast >= 0).map((r) => r.value));
      }
      state.liveData.fetched_at_utc = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
      state.liveData.summary.xact_latest_obs_utc = Object.values(state.liveData.epa_xact)
        .map((s) => s.latest_ts_utc).filter(Boolean).sort().pop();
      state.liveData.summary.xact_max_post_ugm3 = Math.max(0, ...Object.values(state.liveData.epa_xact).map((s) => s.post_blast_max));
    }

    renderHeroKPIs();
    renderStationChart();
    renderLiveTables();
    if (state.obs3d) state.obs3d.updateLiveTelemetry(state.liveData);
  } catch (err) {
    console.warn('Live browser refresh partial fallback:', err);
  } finally {
    btn.disabled = false;
    btn.textContent = '↻ Live Refresh';
  }
}

async function boot() {
  const [staticRes, demRes, plumeRes, liveRes] = await Promise.all([
    fetch('site_data/scene_static.json'),
    fetch('site_data/terrain_dem.json'),
    fetch('site_data/plume_3d.json'),
    fetch(`site_data/live_telemetry.json?t=${Date.now()}`),
  ]);
  state.staticData = await staticRes.json();
  const demData = await demRes.json();
  const plumeData = await plumeRes.json();
  state.liveData = await liveRes.json();

  renderHeroKPIs();
  renderLiveTables();
  renderStationChart();
  renderSensitivityChart();

  // Initialize 3D WebGL Observatory
  const sliderTime = document.getElementById('slider-time');
  const readoutTime = document.getElementById('readout-time');
  const readoutPbl = document.getElementById('readout-pbl');
  const inspBox = document.getElementById('hud-inspector');
  const inspTitle = document.getElementById('insp-title');
  const inspBody = document.getElementById('insp-body');

  const obs = new Plume3DObservatory(document.getElementById('canvas-3d'), {
    onTick: (sec, pblM, airPct) => {
      sliderTime.value = Math.round(sec);
      readoutTime.textContent = `${fmtTimePost(sec)} · ${fmtCstClock(sec)}`;
      readoutPbl.textContent = `PBL: ${Math.round(pblM)} m · Airborne: ${airPct}%`;
    },
    onSelect: (info) => {
      inspTitle.textContent = info.title;
      inspBody.innerHTML = info.body;
      inspBox.classList.add('show');
    },
  });
  state.obs3d = obs;
  await obs.init(state.staticData, demData, plumeData, state.liveData);
  document.getElementById('loading-3d').style.display = 'none';

  // Wire 3D HUD controls
  document.getElementById('insp-close').onclick = () => inspBox.classList.remove('show');
  const playBtn = document.getElementById('btn-play');
  playBtn.onclick = () => {
    obs.playing = !obs.playing;
    playBtn.textContent = obs.playing ? '❚❚ Pause' : '▶ Play';
  };
  sliderTime.oninput = (e) => {
    obs.playing = false;
    playBtn.textContent = '▶ Play';
    obs.simSec = Number(e.target.value);
    obs.updateParticlesAtTime(obs.simSec);
  };
  document.getElementById('sel-speed').onchange = (e) => { obs.speedMul = Number(e.target.value); };
  document.getElementById('sel-base').onchange = (e) => obs.setBaseMap(e.target.value);
  document.getElementById('sel-drape').onchange = (e) => obs.setDrape(e.target.value);
  document.getElementById('sel-color').onchange = (e) => obs.setColorMode(e.target.value);
  document.getElementById('slider-exag').oninput = (e) => {
    const v = Number(e.target.value);
    document.getElementById('lbl-exag').textContent = `${v}×`;
    obs.applyVerticalExaggeration(v);
  };
  for (const [id, layer] of [
    ['chk-sightlines', 'sightlines'], ['chk-sensors', 'sensors'],
    ['chk-winds', 'winds'], ['chk-lids', 'lids'], ['chk-labels', 'labels'],
  ]) {
    document.getElementById(id).onchange = (e) => obs.setLayerVisible(layer, e.target.checked);
  }
  document.querySelectorAll('[data-preset]').forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll('[data-preset]').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      obs.flyToPreset(btn.dataset.preset);
    };
  });
  const stageWrap = document.getElementById('stage-3d-wrap');
  document.getElementById('btn-fs').onclick = () => {
    stageWrap.classList.toggle('fullscreen');
    obs._onResize();
  };

  // Wire Plotly Explorer controls
  document.querySelectorAll('.stn-tab').forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll('.stn-tab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.selectedStn = btn.dataset.stn;
      renderStationChart();
    };
  });
  const relSlider = document.getElementById('slider-release');
  relSlider.oninput = (e) => {
    // Log-scale slider -3..1 => 0.001%..10%
    state.releasePct = Number((10 ** Number(e.target.value)).toPrecision(2));
    document.getElementById('lbl-release').textContent = `${state.releasePct}% (${(TOTAL_PB_KG * state.releasePct / 100).toFixed(2)} kg Pb)`;
    renderStationChart();
    renderSensitivityChart();
  };

  // Wire Live Refresh & Lightbox
  document.getElementById('btn-live-refresh').onclick = liveBrowserRefresh;
  const lb = document.getElementById('lightbox');
  const lbImg = document.getElementById('lightbox-img');
  document.querySelectorAll('.fig-card img').forEach((img) => {
    img.onclick = () => { lbImg.src = img.src; lb.classList.add('open'); };
  });
  lb.onclick = () => lb.classList.remove('open');
}

boot().catch((err) => {
  console.error('Boot error:', err);
  const el = document.getElementById('loading-3d');
  if (el) el.textContent = `Error loading 3D observatory: ${err.message}`;
});
