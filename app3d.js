/**
 * Interactive 3D WebGL Atmospheric Plume & Live Sensor Observatory (Three.js ES Module)
 * Coordinate system in Three.js (1 unit = 1 km):
 *   X = East (+km from stereo origin 40.8099N, -91.2668W)
 *   Y = Altitude (+km AGL, scaled by verticalExag slider!)
 *   Z = South (-Y_north km! So North is -Z, South is +Z, East is +X, West is -X)
 */
import * as THREE from 'https://esm.sh/three@0.169.0';
import { OrbitControls } from 'https://esm.sh/three@0.169.0/examples/jsm/controls/OrbitControls.js';

export class Plume3DObservatory {
  constructor(canvasEl, hudCallbacks) {
    this.canvas = canvasEl;
    this.cb = hudCallbacks || {};
    this.vertExag = 12.0; // 12x vertical exaggeration makes 0-1,075m shear & terrain crystal clear!
    this.playing = true;
    this.speedMul = 20.0; // 20x speed -> 4 hours plays cleanly in ~25 seconds (slower early!)
    this.simSec = 660.0;  // Start at T+11m right as projected plume crosses AS01 (live 0.0021 ug/m3 trace!)
    this.maxSec = 18000.0;
    this.colorMode = 'regime'; // 'regime' | 'altitude'
    this.plumeMode = 'assimilated'; // 'assimilated' | 'forecast'
    this.pickables = [];
    this.allLabels = [];
    this.xactVisuals = {};
    this.windVisuals = [];
    this.shearMasts = [];
    this.flowPulses = [];
    this.runoffParticles = [];
    this.runoffTrack = null;
    this.runoffSimActive = false;
    this.runoffPct = 0.10;
    this._tmpVec = new THREE.Vector3();
    this._tmpDir = new THREE.Vector3();
    this._tmpCol = new THREE.Color();
    this._colDilute = new THREE.Color(0x38bdf8);
    this._upVec = new THREE.Vector3(0, 1, 0);
    this.latestXactObsSec = 6600.0;
    this.windObsCutoffSec = 6600.0;
    this.assimShiftInfo = null;
    this.sensorGroup = new THREE.Group();
    this.windGroup = new THREE.Group();
    this.hydroGroup = new THREE.Group();
    this.runoffGroup = new THREE.Group();
    this.runoffGroup.visible = false;
    this.usgsGroup = new THREE.Group();
    this.sightlineGroup = new THREE.Group();
    this.lidGroup = new THREE.Group();
    this.labelGroup = new THREE.Group();
    this.cameraTargetGoal = null;
    this.cameraPosGoal = null;

    this._initRenderer();
    this._initLighting();
    this._initEvents();
  }

  _initRenderer() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x060a10);
    this.scene.fog = new THREE.FogExp2(0x060a10, 0.0018);

    const w = this.canvas.clientWidth || 1200;
    const h = this.canvas.clientHeight || 700;
    this.camera = new THREE.PerspectiveCamera(46, w / h, 0.05, 700);
    this.camera.position.set(22, 22, 31);

    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, powerPreference: 'high-performance' });
    this.renderer.setSize(w, h, false);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;
    this.controls.maxPolarAngle = Math.PI * 0.492;
    this.controls.minDistance = 0.8;
    this.controls.maxDistance = 300;
    this.controls.target.set(-14, 1.5, 11);
    this.controls.update();

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.clock = new THREE.Clock();

    this.scene.add(this.sensorGroup, this.windGroup, this.hydroGroup, this.runoffGroup, this.usgsGroup, this.sightlineGroup, this.lidGroup, this.labelGroup);
  }

  _initLighting() {
    this.scene.add(new THREE.AmbientLight(0xcbd5e1, 1.15));
    const sun = new THREE.DirectionalLight(0xfff1d6, 1.45);
    // Morning Nov 25 9AM CST sun: Azimuth 138° SE, Elevation 16.7°
    sun.position.set(45, 22, 50);
    this.scene.add(sun);
    const fill = new THREE.DirectionalLight(0x38bdf8, 0.45);
    fill.position.set(-40, 25, -35);
    this.scene.add(fill);
  }

  _initEvents() {
    this._onResize = () => {
      const parent = this.canvas.parentElement;
      if (!parent) return;
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h, false);
    };
    window.addEventListener('resize', this._onResize);

    let downPos = { x: 0, y: 0 };
    this.canvas.addEventListener('pointerdown', (e) => {
      downPos = { x: e.clientX, y: e.clientY };
      this.cameraPosGoal = null;
      this.cameraTargetGoal = null;
    });
    this.canvas.addEventListener('pointerup', (e) => {
      const dist = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y);
      if (dist > 6) return; // Dragged orbit, not a click
      const rect = this.canvas.getBoundingClientRect();
      this.pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      this.pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      this.raycaster.setFromCamera(this.pointer, this.camera);
      const hits = this.raycaster.intersectObjects(this.pickables, false);
      if (hits.length > 0 && this.cb.onSelect) {
        const ud = hits[0].object.userData;
        const body = typeof ud.getBody === 'function' ? ud.getBody(this.simSec) : ud.body;
        this.cb.onSelect({ ...ud, body });
      }
    });
  }

  // Convert lat/lon to world (x_km, z_km) where z_km = -y_north_km
  ll2xz(lat, lon) {
    const lat0 = this.staticData.origin.lat;
    const lon0 = this.staticData.origin.lon;
    const mLat = 111.132;
    const mLon = 111.320 * Math.cos((lat0 * Math.PI) / 180);
    return { x: (lon - lon0) * mLon, z: -(lat - lat0) * mLat };
  }

  // Bilinear elevation lookup (meters relative to IAAAP ref 215m -> returns km * vertExag)
  terrainY(xKm, zKm) {
    if (!this.dem) return 0;
    const yNorth = -zKm;
    const { nx, ny, wx_min_km, wx_max_km, wy_min_km, wy_max_km, ref_elev_m, elev_m } = this.dem;
    const fx = THREE.MathUtils.clamp((xKm - wx_min_km) / (wx_max_km - wx_min_km), 0, 1) * (nx - 1);
    const fy = THREE.MathUtils.clamp((wy_max_km - yNorth) / (wy_max_km - wy_min_km), 0, 1) * (ny - 1);
    const ix = Math.min(Math.floor(fx), nx - 2), iy = Math.min(Math.floor(fy), ny - 2);
    const tx = fx - ix, ty = fy - iy;
    const e00 = elev_m[iy * nx + ix], e10 = elev_m[iy * nx + ix + 1];
    const e01 = elev_m[(iy + 1) * nx + ix], e11 = elev_m[(iy + 1) * nx + ix + 1];
    const e = (e00 * (1 - tx) + e10 * tx) * (1 - ty) + (e01 * (1 - tx) + e11 * tx) * ty;
    return ((e - ref_elev_m) / 1000.0) * this.vertExag;
  }

  async init(staticData, demData, plumeData, liveData) {
    this.staticData = staticData;
    this.dem = demData;
    this.forecastPlume = plumeData;
    this.plume = plumeData;
    this.maxSec = plumeData.times_sec[plumeData.times_sec.length - 1];

    const loader = new THREE.TextureLoader();
    const loadTex = (url) => new Promise((res) => loader.load(url, (t) => { t.colorSpace = THREE.SRGBColorSpace; res(t); }, undefined, () => res(null)));
    const [texDark, texSat, drapeExp, drapeDep, drapeSens] = await Promise.all([
      loadTex('assets/textures/terrain_dark.jpg'),
      loadTex('assets/textures/terrain_sat.jpg'),
      loadTex('assets/textures/drape_exposure.png'),
      loadTex('assets/textures/drape_deposition.png'),
      loadTex('assets/textures/drape_sensitivity.png'),
    ]);
    this.textures = { dark: texDark, sat: texSat, exposure: drapeExp, deposition: drapeDep, sensitivity: drapeSens };

    this._buildTerrain();
    this._buildStaticOverlays();
    this._buildParticles();
    this._buildAtmosphericLids();
    if (liveData) this.updateLiveTelemetry(liveData);

    this.applyVerticalExaggeration(this.vertExag);
    this.updateParticlesAtTime(this.simSec);
    this._animate();
  }

  // Deterministic Mulberry32 PRNG + Box-Muller for instant (<35ms) in-browser live-wind re-assimilation
  _buildAssimilatedPlume(windAssim) {
    if (!windAssim || !windAssim.u_assim || !windAssim.z_grid_m || !windAssim.times_min) return null;
    let seed = 202611253 >>> 0;
    const rand = () => {
      seed = (seed + 0x6D2B79F5) >>> 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const randn = () => {
      const u1 = Math.max(1e-7, rand());
      return Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * rand());
    };

    const zGrid = windAssim.z_grid_m;
    const nZ = zGrid.length;
    const tMinArr = windAssim.times_min;
    const nT = tMinArr.length;
    const uAss = windAssim.u_assim;
    const vAss = windAssim.v_assim;
    const pblArr = windAssim.pbl_m;

    const N_C = 700, N_10 = 850, N_25 = 850;
    const N = N_C + N_10 + N_25;
    const regimes = new Uint8Array(N);
    const isBallistic = new Uint8Array(N);
    const vs = new Float32Array(N);
    const pStick = new Float32Array(N);
    const x = new Float32Array(N);
    const y = new Float32Array(N);
    const z = new Float32Array(N);
    const zApex = new Float32Array(N);
    const wFallBall = new Float32Array(N);
    const wBuoy = new Float32Array(N);
    const cdArr = new Float32Array(N);
    const sdArr = new Float32Array(N);
    const spdArr = new Float32Array(N);
    const active = new Uint8Array(N);

    // 3-Camera verified Dual-Bunker geometry: 311 m along 208.6° SSW–NNE diagonal magazine loop
    const bSin = Math.sin((208.6 * Math.PI) / 180.0);
    const bCos = Math.cos((208.6 * Math.PI) / 180.0);
    for (let i = 0; i < N; i++) {
      active[i] = 1;
      const r = i < N_C ? 0 : (i < N_C + N_10 ? 1 : 2);
      regimes[i] = r;
      if (i < 400) isBallistic[i] = 1; // Dark soil/gel ballistic ejecta jets peaking @ T+4.5s & collapsing to pad!
      if (r === 0) {
        vs[i] = 10 ** (Math.log10(0.035) + rand() * (Math.log10(0.30) - Math.log10(0.035)));
        pStick[i] = 1.0;
      } else if (r === 1) {
        vs[i] = 10 ** (Math.log10(0.0012) + rand() * (Math.log10(0.016) - Math.log10(0.0012)));
        pStick[i] = 0.30;
      } else {
        vs[i] = 10 ** (Math.log10(0.00005) + rand() * (Math.log10(0.0006) - Math.log10(0.00005)));
        pStick[i] = 0.06;
      }
      const bunker = rand() < 0.35 ? -155.5 : 155.5; // North small (-155.5m) vs South large (+155.5m)
      x[i] = bunker * bSin + randn() * 35.0;
      y[i] = bunker * bCos + randn() * 35.0;
      z[i] = 5.0 + rand() * 17.0;
      const apex = bunker < 0 ? (55.0 + rand() * 56.0) : (70.0 + rand() * 89.0); // 111m N / 159m S ballistic peak
      zApex[i] = apex;
      wFallBall[i] = apex / (5.5 + rand() * 4.0);
      // Stabilized white smoke cap levels off flat at ~15-108m (N) / ~20-130m (S), +10% wispy thermal tail to 175m
      let wb = bunker < 0 ? (0.8 + rand() * 6.0) : (1.2 + rand() * 7.4);
      if (r === 0 && !isBallistic[i]) wb *= 0.65;
      if (r >= 1 && bunker > 0 && rand() < 0.10) wb = 8.6 + rand() * 3.6;
      wBuoy[i] = wb;
      const dth = (randn() * 5.5 * Math.PI) / 180.0;
      cdArr[i] = Math.cos(dth);
      sdArr[i] = Math.sin(dth);
      spdArr[i] = 1.0 + randn() * 0.08;
    }

    const kfSec = this.forecastPlume.times_sec;
    const kfSet = new Set(kfSec);
    const frames = [];
    const framesPbl = [];

    // Use 4-node spatially varying HRRR 3 km + ASOS/RWIS field (IAAAP, Kahoka, Kirksville, Ottumwa) if present!
    const nodes = (windAssim.spatial_nodes && windAssim.spatial_nodes.length > 0)
      ? windAssim.spatial_nodes
      : [{ id: 'IAAAP', x_km: 0, y_km: 0, u_assim: uAss, v_assim: vAss, pbl_m: pblArr }];
    const nNodes = nodes.length;
    const nodeX = nodes.map((n) => n.x_km * 1000.0);
    const nodeY = nodes.map((n) => n.y_km * 1000.0);
    const uNodeT = Array.from({ length: nNodes }, () => new Float32Array(nZ));
    const vNodeT = Array.from({ length: nNodes }, () => new Float32Array(nZ));
    const pblNodeT = new Float32Array(nNodes);
    const wNode = new Float32Array(nNodes);

    const interpZ = (zz, arr) => {
      if (zz <= zGrid[0]) return arr[0];
      if (zz >= zGrid[nZ - 1]) return arr[nZ - 1];
      for (let k = 0; k < nZ - 1; k++) {
        if (zz <= zGrid[k + 1]) {
          const frac = (zz - zGrid[k]) / (zGrid[k + 1] - zGrid[k]);
          return arr[k] * (1.0 - frac) + arr[k + 1] * frac;
        }
      }
      return arr[nZ - 1];
    };

    const snap = (pblVal) => {
      const flat = new Int32Array(N * 3);
      for (let i = 0; i < N; i++) {
        const j = i * 3;
        flat[j] = Math.round(x[i] * 0.1);
        flat[j + 1] = Math.round(y[i] * 0.1);
        flat[j + 2] = active[i] ? Math.round(Math.max(0, Math.min(1200, z[i]))) : 0;
      }
      frames.push(flat);
      framesPbl.push(Math.round(pblVal * 10) / 10);
    };

    snap(pblArr[0]);
    let tCur = 0;
    const tEnd = this.maxSec || 18000;
    const L2_SPATIAL = 30000.0 * 30000.0; // 30 km smooth IDW kernel across the 192 km HRRR spatial network
    while (tCur < tEnd) {
      const dt = tCur < 30 ? 2.5 : (tCur < 120 ? 5.0 : 30.0);
      tCur += dt;
      const tMin = tCur / 60.0;
      const idxLo = Math.min(Math.floor(tMin / 10.0), nT - 2);
      const tf = Math.min(1.0, (tMin - tMinArr[idxLo]) / 10.0);
      for (let n = 0; n < nNodes; n++) {
        const nd = nodes[n];
        for (let k = 0; k < nZ; k++) {
          uNodeT[n][k] = nd.u_assim[idxLo][k] * (1.0 - tf) + nd.u_assim[idxLo + 1][k] * tf;
          vNodeT[n][k] = nd.v_assim[idxLo][k] * (1.0 - tf) + nd.v_assim[idxLo + 1][k] * tf;
        }
        pblNodeT[n] = Math.min(1075.0, Math.max(285.0, nd.pbl_m[idxLo] * (1.0 - tf) + nd.pbl_m[idxLo + 1] * tf));
      }
      const pblRef = pblNodeT[0];
      const conv = Math.min(1.0, (tCur / 3600.0) / 2.0);
      const sigH = Math.sqrt(2.0 * (16.0 + 36.0 * conv) * dt);
      const kzBase = 3.2 + 14.0 * conv;
      const buoyDecay = tCur <= 90 ? Math.exp(-tCur / 14.0) : 0.0;

      for (let i = 0; i < N; i++) {
        if (!active[i]) continue;
        const xi = x[i], yi = y[i], za = z[i];
        let um = 0.0, vm = 0.0, pblLoc = pblRef;
        if (nNodes === 1) {
          um = interpZ(za, uNodeT[0]);
          vm = interpZ(za, vNodeT[0]);
        } else {
          let wSum = 0.0;
          for (let n = 0; n < nNodes; n++) {
            const dx = xi - nodeX[n], dy = yi - nodeY[n];
            const w = 1.0 / (dx * dx + dy * dy + L2_SPATIAL);
            wNode[n] = w;
            wSum += w;
          }
          const invW = 1.0 / wSum;
          pblLoc = 0.0;
          for (let n = 0; n < nNodes; n++) {
            const wn = wNode[n] * invW;
            um += wn * interpZ(za, uNodeT[n]);
            vm += wn * interpZ(za, vNodeT[n]);
            pblLoc += wn * pblNodeT[n];
          }
        }
        const ur = spdArr[i] * (um * cdArr[i] + vm * sdArr[i]);
        const vr = spdArr[i] * (-um * sdArr[i] + vm * cdArr[i]);
        const kz = kzBase * Math.max(0.15, Math.min(1.0, 1.0 - 0.7 * (za / 1075.0) ** 2));
        let uEff = ur, vEff = vr, vzPart = wBuoy[i] * buoyDecay - vs[i];
        let zNoise = randn() * Math.sqrt(2.0 * kz * dt);
        if (isBallistic[i]) {
          if (tCur <= 5.0) {
            vzPart = (zApex[i] - 12.0) / 5.0;
            uEff = ur * 0.25; vEff = vr * 0.25;
          } else {
            vzPart = -wFallBall[i];
            uEff = ur * 0.35; vEff = vr * 0.35;
          }
          zNoise = randn() * 2.0;
        }
        x[i] = xi + uEff * dt + randn() * sigH;
        y[i] = yi + vEff * dt + randn() * sigH;
        let zn = za + vzPart * dt + zNoise;
        if (zn > pblLoc) zn = Math.max(-50.0, Math.min(1075.0, pblLoc - (zn - pblLoc) * 0.85));
        if (zn <= 0.0) {
          if (rand() < pStick[i]) {
            active[i] = 0;
            zn = 0.0;
          } else {
            zn = Math.abs(zn);
          }
        }
        z[i] = zn;
      }
      const tInt = Math.round(tCur);
      if (Math.abs(tCur - tInt) < 1e-6 && kfSet.has(tInt)) snap(pblRef);
    }

    // Measure airborne fine-plume centroid shift vs. initial 15Z forecast at T+4h (14,400s, capturing far-field NE Missouri curl!)
    const idxCmp = kfSec.indexOf(14400) >= 0 ? kfSec.indexOf(14400) : kfSec.indexOf(7200);
    if (idxCmp >= 0 && this.forecastPlume.frames[idxCmp]) {
      const fA = frames[idxCmp], fF = this.forecastPlume.frames[idxCmp];
      let ax = 0, ay = 0, fx = 0, fy = 0, ca = 0, cf = 0;
      for (let i = N_C; i < N; i++) {
        const j = i * 3;
        if (fA[j + 2] > 2) { ax += fA[j] * 0.01; ay += fA[j + 1] * 0.01; ca++; }
        if (fF[j + 2] > 2) { fx += fF[j] * 0.01; fy += fF[j + 1] * 0.01; cf++; }
      }
      if (ca > 0 && cf > 0) {
        const dx = ax / ca - fx / cf, dy = ay / ca - fy / cf;
        const distKm = Math.hypot(dx, dy);
        const brg = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
        const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
        this.assimShiftInfo = {
          dist_km: Math.round(distKm * 10) / 10,
          compass: dirs[Math.round(brg / 45) % 8],
          cutoff_min: windAssim.obs_cutoff_min,
          cutoff_utc: windAssim.obs_cutoff_utc,
          n_nodes: nNodes,
          cmp_hr: Math.round(kfSec[idxCmp] / 3600),
        };
      }
    }

    return {
      n_particles: N,
      coord_scale_m: 10.0,
      times_sec: kfSec,
      pbl_m: framesPbl,
      regimes: Array.from(regimes),
      frames,
    };
  }

  setPlumeMode(mode) {
    this.plumeMode = mode;
    this.plume = (mode === 'assimilated' && this.assimilatedPlume) ? this.assimilatedPlume : this.forecastPlume;
    this.updateParticlesAtTime(this.simSec);
  }

  _buildTerrain() {
    const { wx_min_km, wx_max_km, wy_min_km, wy_max_km, nx, ny } = this.dem;
    const W = wx_max_km - wx_min_km; // 76 km
    const H = wy_max_km - wy_min_km; // 60 km
    const cx = (wx_min_km + wx_max_km) / 2; // -26 km
    const cz = -(wy_min_km + wy_max_km) / 2; // +16 km

    // Base Terrain Mesh
    this.terrainGeo = new THREE.PlaneGeometry(W, H, nx - 1, ny - 1);
    this.terrainGeo.rotateX(-Math.PI / 2);
    this.terrainMat = new THREE.MeshStandardMaterial({
      map: this.textures.dark,
      roughness: 0.92,
      metalness: 0.05,
    });
    this.terrainMesh = new THREE.Mesh(this.terrainGeo, this.terrainMat);
    this.terrainMesh.position.set(cx, 0, cz);
    this.scene.add(this.terrainMesh);

    // Heatmap Drape Overlay Mesh (slightly offset above terrain to avoid z-fighting!)
    this.drapeGeo = new THREE.PlaneGeometry(W, H, nx - 1, ny - 1);
    this.drapeGeo.rotateX(-Math.PI / 2);
    this.drapeMat = new THREE.MeshBasicMaterial({
      map: this.textures.exposure,
      transparent: true,
      opacity: 0.88,
      depthWrite: false,
    });
    this.drapeMesh = new THREE.Mesh(this.drapeGeo, this.drapeMat);
    this.drapeMesh.position.set(cx, 0.06, cz);
    this.scene.add(this.drapeMesh);

    // Subtle metric coordinate grid around widened ground
    const grid = new THREE.GridHelper(Math.max(W, H) * 1.15, 24, 0x1e2d42, 0x121c2a);
    grid.position.set(cx, -0.8, cz);
    this.scene.add(grid);
  }

  _makeSpriteLabel(text, color = '#ffffff', bg = 'rgba(8,12,18,0.82)', fontSize = 26, opts = {}) {
    const c = document.createElement('canvas');
    const ctx = c.getContext('2d');
    ctx.font = `700 ${fontSize}px Inter, sans-serif`;
    const pad = 12;
    const tw = ctx.measureText(text).width;
    c.width = tw + pad * 2;
    c.height = fontSize + pad * 1.4;
    ctx.fillStyle = bg;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(2, 2, c.width - 4, c.height - 4, 8);
    ctx.fill();
    ctx.stroke();
    ctx.font = `700 ${fontSize}px Inter, sans-serif`;
    ctx.fillStyle = color;
    ctx.textBaseline = 'middle';
    ctx.fillText(text, pad, c.height / 2 + 1);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
    const sp = new THREE.Sprite(mat);
    const aspect = c.width / c.height;
    const sizeMul = opts.sizeMul ?? 1.0;
    sp.center.set(0.5, 0.08);
    sp.scale.set(1.10 * sizeMul * aspect, 1.10 * sizeMul, 1);
    sp.renderOrder = 10;
    sp.userData.labelAspect = aspect;
    sp.userData.labelSizeMul = sizeMul;
    sp.userData.groupTag = opts.groupTag || 'static';
    this.allLabels.push(sp);
    return sp;
  }

  _makeDynamicSprite(initialText, color = '#6ee7b7', bg = 'rgba(6,22,16,0.92)', opts = {}) {
    const c = document.createElement('canvas');
    c.width = 430;
    c.height = 50;
    const ctx = c.getContext('2d');
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const draw = (text, fgColor, bgColor, dashed = false) => {
      ctx.clearRect(0, 0, c.width, c.height);
      ctx.font = '700 20px Inter, sans-serif';
      const pad = 14;
      const tw = Math.min(c.width - pad * 2, ctx.measureText(text).width);
      const boxW = Math.min(c.width - 4, tw + pad * 2);
      const x0 = (c.width - boxW) / 2;
      ctx.fillStyle = bgColor;
      ctx.strokeStyle = fgColor;
      ctx.lineWidth = 2.4;
      ctx.setLineDash(dashed ? [6, 4] : []);
      ctx.beginPath();
      ctx.roundRect(x0, 4, boxW, c.height - 8, 9);
      ctx.fill();
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = fgColor;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, c.width / 2, c.height / 2 + 1, boxW - pad);
      tex.needsUpdate = true;
    };
    draw(initialText, color, bg, false);
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
    const sp = new THREE.Sprite(mat);
    const aspect = c.width / c.height;
    const sizeMul = opts.sizeMul ?? 1.08;
    sp.center.set(0.5, 0.08);
    sp.scale.set(1.15 * sizeMul * aspect, 1.15 * sizeMul, 1);
    sp.renderOrder = 14;
    sp.userData.labelAspect = aspect;
    sp.userData.labelSizeMul = sizeMul;
    sp.userData.groupTag = opts.groupTag || 'live';
    this.allLabels.push(sp);
    return { sprite: sp, draw };
  }

  _lookupXactAtSec(xact, sec) {
    const ser = xact.series || [];
    if (!ser.length) {
      return { status: 'future', value: null, obsMin: null, obsTs: null, latestSec: 0 };
    }
    const latestRec = ser[ser.length - 1];
    const latestSec = Math.max(0, (latestRec.min_post_blast ?? 0) * 60);
    if (sec > latestSec + 180) {
      return {
        status: 'future',
        value: null,
        obsMin: latestRec.min_post_blast,
        obsTs: latestRec.ts_utc,
        latestSec,
      };
    }
    // Find the most recent 5-minute observation at or before playback time (with +30s bin edge tolerance)
    let chosen = ser[0];
    for (let i = 0; i < ser.length; i++) {
      if ((ser[i].min_post_blast * 60) <= sec + 30) {
        chosen = ser[i];
      } else {
        break;
      }
    }
    const val = Number(chosen.value || 0);
    return {
      status: val > 0 ? 'trace' : 'zero',
      value: val,
      obsMin: chosen.min_post_blast,
      obsTs: chosen.ts_utc,
      latestSec,
    };
  }

  _buildStaticOverlays() {
    const s = this.staticData;

    // 1. IAAAP Perimeter Polygon (gold dashed loop)
    for (const ring of s.iaaap_rings_km) {
      const pts = ring.map(([x, y]) => new THREE.Vector3(x, 0.10, -y));
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      this.sightlineGroup.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0xffdd44, transparent: true, opacity: 0.85 })));
    }

    // 2. 5-mile (8.05 km) and 20-mile (32.19 km) range rings
    const addRing = (radiusKm, color) => {
      const pts = [];
      for (let i = 0; i <= 96; i++) {
        const a = (i / 96) * Math.PI * 2;
        pts.push(new THREE.Vector3(Math.cos(a) * radiusKm, 0.08, Math.sin(a) * radiusKm));
      }
      this.sightlineGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.55 })));
    };
    addRing(8.047, 0xff4757);
    addRing(32.187, 0xffaa33);

    // 3. 3-Camera Triangulated Origin Beacon + Tightened ±244×46 m Error Ellipse + Dual Bunkers (311 m @ 209°)!
    const originSphere = new THREE.Mesh(
      new THREE.SphereGeometry(0.26, 20, 20),
      new THREE.MeshBasicMaterial({ color: 0xff2244 })
    );
    originSphere.position.set(0, 0.25, 0);
    originSphere.userData = {
      type: 'origin',
      title: '3-Camera Triangulated Detonation Zone',
      body: `<div style="margin-bottom:5px"><span class="tag-live">3-Camera Video-Measured</span></div>` +
            `<b>Coordinates:</b> ${s.origin.lat.toFixed(4)}°N, ${s.origin.lon.toFixed(4)}°W (±244×46 m, 4.9× tighter than 2-cam)<br>` +
            `<b>Triangulation Baseline:</b> 64.6° max (KWQC + KCRG/AP + Wilson Drone)<br>` +
            `<b>Dual-Bunker Vector:</b> 311 m @ 209° SSW–NNE (fired 33 ms apart)<br>` +
            `<b>Ballistic Collapse (Cam 3):</b> Dark debris peaks 111–159 m @ T+4.4s → collapses to pad; white cap stabilizes flat at ~100–130 m<br>` +
            `<b>Event Time:</b> 2026-11-25 15:00 UTC (9:00 AM CST) · ~3,050 kg Pb`
    };
    this.sightlineGroup.add(originSphere);
    this.pickables.push(originSphere);

    // Individual North & South Bunker beacons (311 m @ 208.6° diagonal loop)
    if (s.bunkers) {
      for (const [key, b, col] of [
        ['north', s.bunkers.north, 0x4ade80],
        ['south', s.bunkers.south, 0xf472b6],
      ]) {
        const bm = new THREE.Mesh(new THREE.SphereGeometry(0.14, 14, 14), new THREE.MeshBasicMaterial({ color: col }));
        bm.position.set(b.x_km, 0.22, -b.y_km);
        bm.userData = {
          type: 'origin',
          title: b.label,
          body: `<div style="margin-bottom:5px"><span class="tag-live">3-Camera Resolved Bunker</span></div>` +
                `<b>Coords:</b> ${b.lat.toFixed(4)}°N, ${b.lon.toFixed(4)}°W<br>` +
                (key === 'north'
                  ? `<b>Cam 3 Optics:</b> Fires T+33 ms · Ballistic peak 111 m → stabilized white cap ~108 m`
                  : `<b>Cam 3 Optics:</b> Fires T+0 ms (condensation flash) · Ballistic peak 159 m → stabilized white cap ~130 m`)
        };
        this.sightlineGroup.add(bm);
        this.pickables.push(bm);
      }
    }

    const ellPts = [];
    const rot = (93.7 * Math.PI) / 180;
    for (let i = 0; i <= 64; i++) {
      const th = (i / 64) * Math.PI * 2;
      const dN = 0.244 * Math.cos(th) * Math.cos(rot) - 0.046 * Math.sin(th) * Math.sin(rot);
      const dE = 0.244 * Math.cos(th) * Math.sin(rot) + 0.046 * Math.sin(th) * Math.cos(rot);
      ellPts.push(new THREE.Vector3(dE, 0.14, -dN));
    }
    this.sightlineGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(ellPts), new THREE.LineBasicMaterial({ color: 0xffff00 })));

    // 3b. On-Site Ballistic Containment Halo (r = 0.65 km enclosing both bunkers & soil/gel fallback pad)
    const haloGeo = new THREE.CircleGeometry(0.65, 48);
    haloGeo.rotateX(-Math.PI / 2);
    this.ballisticHaloDisc = new THREE.Mesh(
      haloGeo,
      new THREE.MeshBasicMaterial({ color: 0xf59e0b, transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthWrite: false })
    );
    this.ballisticHaloDisc.position.set(0, 0.13, 0);
    this.ballisticHaloDisc.userData = {
      type: 'ballistic_halo',
      title: 'On-Site Ballistic Containment Zone (~0.65 km)',
      body: `<div style="margin-bottom:5px"><span class="tag-live">● Mass-Balance &amp; Cam 3 Verified</span></div>` +
            `<b>Mass Retained On-Site:</b> <strong style="color:#fbbf24">&gt;99.95% (~3,049 kg of ~3,050 kg Pb)</strong> stayed inside IAAAP (live EPA Xact <code>AS02</code> bounds airborne downwind escape to ≲0.028% / ≤0.85 kg).<br>` +
            `<b>Cam 3 Drone Forensics:</b> Dark soil/gel ballistic debris peaked at 111 m (N) / 159 m (S) at T+4.4 s and collapsed back onto the pad within 10–15 s.<br>` +
            `<b>Soil &amp; Runoff Context:</b> Sits inside <b>Long Creek (HUC12 070801071005)</b>, draining 6.1 km SSE into Skunk River — making Army pad capping/remediation before heavy rain the key long-term environmental safeguard.`,
    };
    this.sightlineGroup.add(this.ballisticHaloDisc);
    this.pickables.push(this.ballisticHaloDisc);

    const haloRingPts = [];
    for (let i = 0; i <= 64; i++) {
      const a = (i / 64) * Math.PI * 2;
      haloRingPts.push(new THREE.Vector3(Math.cos(a) * 0.65, 0, Math.sin(a) * 0.65));
    }
    this.ballisticHaloRing = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(haloRingPts),
      new THREE.LineBasicMaterial({ color: 0xfbbf24, transparent: true, opacity: 0.92 })
    );
    this.ballisticHaloRing.position.set(0, 0.15, 0);
    this.sightlineGroup.add(this.ballisticHaloRing);

    // 4.0 NM FAA TFR Ring (FDC 6/9186 centered at 40.7950N, -91.2481W)
    const tfrCenter = this.ll2xz(40.79500, -91.24806);
    const tfrPts = [];
    for (let i = 0; i <= 96; i++) {
      const a = (i / 96) * Math.PI * 2;
      tfrPts.push(new THREE.Vector3(tfrCenter.x + Math.cos(a) * 7.408, 0.09, tfrCenter.z + Math.sin(a) * 7.408));
    }
    this.sightlineGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(tfrPts), new THREE.LineBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 0.45 })));

    // 4. Camera 1 (KWQC), FAA Tower T3, Camera 2 (KCRG/AP), Camera 3 (Wilson Drone) + 3D Sightlines!
    const addCam = (cam, color, name, desc) => {
      const x = cam.x_km, z = -cam.y_km;
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(0.45, 0.35, 0.45),
        new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.45 })
      );
      mesh.position.set(x, 0.32, z);
      mesh.userData = { type: 'camera', title: name, body: `<div style="margin-bottom:5px"><span class="tag-live">Video-Calibrated</span></div>` + desc };
      this.sightlineGroup.add(mesh);
      this.pickables.push(mesh);

      const linePts = [new THREE.Vector3(x, 0.30, z), new THREE.Vector3(0, 0.30, 0)];
      this.sightlineGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(linePts), new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.85 })));

      const lbl = this._makeSpriteLabel(name, '#' + color.toString(16).padStart(6, '0'), 'rgba(8,12,18,0.88)', 22, { sizeMul: 0.90 });
      lbl.position.set(x, 0.95, z);
      this.sightlineGroup.add(lbl);
    };

    addCam(s.cam1_kwqc, 0x00e5ff, 'Cam 1: KWQC (190th Ave)',
      `<b>Site:</b> ${s.cam1_kwqc.site}<br><b>Bearing:</b> ${s.cam1_kwqc.bearing_deg}° SSE across FAA Tower T3<br>` +
      `<b>Acoustic Range:</b> 6.23 ± 0.5 km (boom @ Δt=18.8s)<br><b>Tracked Plumes:</b> P1 4.9 m/s, P2 7.2 m/s (R²=0.999)`);

    addCam(s.cam2_kcrg, 0xff8833, 'Cam 2: KCRG/AP (9856 202nd)',
      `<b>Site:</b> ${s.cam2_kcrg.site}<br><b>Bearing:</b> ${s.cam2_kcrg.bearing_deg}° ESE | Range: ${s.cam2_kcrg.dist_km} km<br>` +
      `<b>Tracked Upper Puff:</b> ${s.cam2_kcrg.v_true_ms} m/s (R²=${s.cam2_kcrg.r2})`);

    // Cam 3: James Wilson Aerial Drone (@nmjameswilson) hovering at 101 m AGL on 80th St!
    if (s.cam3_wilson) {
      const c3 = s.cam3_wilson;
      const x3 = c3.x_km, z3 = -c3.y_km;
      this.cam3Mesh = new THREE.Mesh(
        new THREE.OctahedronGeometry(0.34, 0),
        new THREE.MeshStandardMaterial({ color: 0x22c55e, emissive: 0x22c55e, emissiveIntensity: 0.65 })
      );
      this.cam3Mesh.position.set(x3, 1.2, z3);
      this.cam3Mesh.userData = {
        type: 'camera',
        title: 'Cam 3: Wilson Drone (@nmjameswilson)',
        body: `<div style="margin-bottom:5px"><span class="tag-live">Aerial Drone · 6-Landmark Calibrated</span></div>` +
              `<b>Hover Pos:</b> ${c3.lat.toFixed(4)}°N, ${c3.lon.toFixed(4)}°W @ ${c3.alt_agl_m.toFixed(0)} m (${c3.alt_agl_ft} ft) AGL<br>` +
              `<b>Position:</b> ${c3.site}<br>` +
              `<b>Dual-Bunker Rays:</b> ${c3.bearing_north_bunker_deg}° N / ${c3.bearing_south_bunker_deg}° S (range ${c3.dist_km} km)<br>` +
              `<b>Key Physics:</b> 33 ms simultaneous fire · 159 m ballistic debris collapse → flat ~100–130 m white cap · S-drift 2.75→4.35 m/s (R²=0.980)`
      };
      this.sightlineGroup.add(this.cam3Mesh);
      this.pickables.push(this.cam3Mesh);

      this.cam3Stem = new THREE.Mesh(
        new THREE.CylinderGeometry(0.025, 0.025, 1.0, 8),
        new THREE.MeshBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 0.55 })
      );
      this.cam3Stem.position.set(x3, 0.5, z3);
      this.sightlineGroup.add(this.cam3Stem);

      const bn = s.bunkers?.north || { x_km: 0, y_km: 0 };
      const bs = s.bunkers?.south || { x_km: 0, y_km: 0 };
      const mkRay = (tx, tz, col) => {
        const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(x3, 1.2, z3), new THREE.Vector3(tx, 0.22, tz)]);
        const ln = new THREE.Line(g, new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.85 }));
        this.sightlineGroup.add(ln);
        return ln;
      };
      this.cam3RayN = mkRay(bn.x_km, -bn.y_km, 0x4ade80);
      this.cam3RayS = mkRay(bs.x_km, -bs.y_km, 0xf472b6);

      this.cam3Label = this._makeSpriteLabel('Cam 3: Wilson Drone (331 ft)', '#4ade80', 'rgba(6,22,12,0.9)', 22, { sizeMul: 0.90 });
      this.cam3Label.position.set(x3, 1.85, z3);
      this.sightlineGroup.add(this.cam3Label);
    }

    // 3D FAA Tower T3 (actual 71.6 m height scaled by vertExag!)
    const t3x = s.tower_t3.x_km, t3z = -s.tower_t3.y_km;
    this.towerT3Mesh = new THREE.Mesh(
      new THREE.CylinderGeometry(0.035, 0.06, 1.0, 10),
      new THREE.MeshStandardMaterial({ color: 0xff44ff, emissive: 0xff22cc, emissiveIntensity: 0.65 })
    );
    this.towerT3Mesh.position.set(t3x, 0.5, t3z);
    this.towerT3Mesh.userData = {
      type: 'tower',
      title: 'FAA Tower T3 (OAS 19-002048)',
      body: `<div style="margin-bottom:5px"><span class="tag-live">FAA DOF / Video Calibration</span></div>` +
            `<b>Location:</b> 40.8411°N, -91.2830°W<br><b>Height:</b> 235 ft (71.6 m) AGL<br><b>Role:</b> Optical pixel-scale calibration landmark (307 px in KWQC feed)`
    };
    this.sightlineGroup.add(this.towerT3Mesh);
    this.pickables.push(this.towerT3Mesh);
    const t3Lbl = this._makeSpriteLabel('Tower T3 (235 ft)', '#ff88ff', 'rgba(8,12,18,0.85)', 20, { sizeMul: 0.85 });
    t3Lbl.position.set(t3x, 1.25, t3z);
    this.sightlineGroup.add(t3Lbl);
    this.towerT3Label = t3Lbl;

    // 5. Key Towns 3D Labels (including widened NE Missouri / SE Iowa far-downwind towns!)
    const showTowns = new Set(['Middletown, IA', 'Danville, IA', 'Burlington, IA', 'Augusta, IA', 'Denmark, IA',
                               'West Point, IA', 'Fort Madison, IA', 'Donnellson, IA', 'Farmington, IA', 'Kahoka, MO',
                               'Keokuk, IA', 'Memphis, MO', 'Bloomfield, IA', 'Edina, MO', 'Kirksville, MO', 'Ottumwa, IA', 'Quincy, IL']);
    for (const t of s.towns) {
      if (!showTowns.has(t.town)) continue;
      const short = t.town.replace(', IA', '').replace(', MO', ' (MO)');
      const sp = this._makeSpriteLabel(short, '#e2e8f0', 'rgba(10,15,24,0.72)', 20, { sizeMul: 0.78 });
      sp.position.set(t.x_km, 0.65, -t.y_km);
      this.labelGroup.add(sp);
    }

    // 6. Real USGS NHD Waterways & Long Creek HUC12 Subwatershed directly under blast pad!
    this._rebuildHydroOverlays();
  }

  _rebuildHydroOverlays() {
    while (this.hydroGroup.children.length) this.hydroGroup.remove(this.hydroGroup.children[0]);
    this.allLabels = this.allLabels.filter((sp) => sp.userData.groupTag !== 'hydro');
    this.pickables = this.pickables.filter((m) => m.userData.type !== 'water_intake');
    this.flowPulses = [];
    const s = this.staticData;
    if (!s || !s.hydrology) return;
    const huc = s.hydrology.blast_huc12;
    if (huc && huc.rings_km) {
      const hucMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.72 });
      for (const ring of huc.rings_km) {
        const pts = ring.map(([x, y]) => new THREE.Vector3(x, this.terrainY(x, -y) + 0.14, -y));
        this.hydroGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), hucMat));
      }
    }
    const streamStyles = {
      'Long Creek':        { color: 0x22d3ee, opacity: 0.95, isMajor: false, coneColor: 0x22d3ee },
      'Brush Creek':       { color: 0x38bdf8, opacity: 0.88, isMajor: false, coneColor: 0x38bdf8 },
      'Spring Creek':      { color: 0x60a5fa, opacity: 0.78, isMajor: false, coneColor: 0x60a5fa },
      'Lost Creek':        { color: 0x38bdf8, opacity: 0.80, isMajor: false, coneColor: 0x38bdf8 },
      'Skunk River':       { color: 0x0ea5e9, opacity: 0.94, isMajor: true,  coneColor: 0x22d3ee },
      'Des Moines River':  { color: 0x3b82f6, opacity: 0.84, isMajor: true,  coneColor: 0x60a5fa },
      'Fox River':         { color: 0x60a5fa, opacity: 0.74, isMajor: true,  coneColor: 0x60a5fa },
      'Mississippi River': { color: 0x2563eb, opacity: 0.90, isMajor: true,  coneColor: 0x60a5fa },
    };

    const { wx_min_km, wx_max_km, wy_min_km, wy_max_km } = this.dem;
    const majorConeGeo = new THREE.ConeGeometry(0.17, 0.56, 7);
    const creekConeGeo = new THREE.ConeGeometry(0.13, 0.44, 7);

    // Stitch USGS NHD flowline segments (already oriented upstream -> downstream!) into continuous chains
    const stitchNhdChains = (rawPaths) => {
      const rem = rawPaths.filter((p) => p.length >= 2).map((p) => p.slice());
      const chains = [];
      const tol = 0.35;
      while (rem.length) {
        let chain = rem.shift();
        let changed = true;
        while (changed) {
          changed = false;
          for (let i = 0; i < rem.length; i++) {
            const p = rem[i];
            const cEnd = chain[chain.length - 1];
            const pStart = p[0];
            if (Math.hypot(cEnd[0] - pStart[0], cEnd[1] - pStart[1]) < tol) {
              chain.push(...p.slice(1));
              rem.splice(i, 1);
              changed = true;
              break;
            }
            const pEnd = p[p.length - 1];
            const cStart = chain[0];
            if (Math.hypot(pEnd[0] - cStart[0], pEnd[1] - cStart[1]) < tol) {
              chain = p.slice(0, -1).concat(chain);
              rem.splice(i, 1);
              changed = true;
              break;
            }
          }
        }
        chains.push(chain);
      }
      return chains;
    };

    const stitchedByName = {};
    for (const [wname, paths] of Object.entries(s.hydrology.waterways_km || {})) {
      const st = streamStyles[wname];
      if (!st) continue;
      const mat = new THREE.LineBasicMaterial({ color: st.color, transparent: true, opacity: st.opacity });
      for (const poly of paths) {
        if (poly.length < 2) continue;
        const pts = poly.map(([x, y]) => new THREE.Vector3(x, this.terrainY(x, -y) + 0.11, -y));
        this.hydroGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
      }

      // Populate uniform-spaced structural water-flow direction chevrons along EVERY stitched NHD river/creek!
      const coneMat = new THREE.MeshBasicMaterial({ color: st.coneColor, transparent: true, opacity: 0.90 });
      const coneGeo = st.isMajor ? majorConeGeo : creekConeGeo;
      const spacingKm = st.isMajor ? 5.8 : 4.0;
      const speedKmS = st.isMajor ? 1.55 : 1.10;
      const chains = stitchNhdChains(paths);
      stitchedByName[wname] = chains;
      for (const chain of chains) {
        // Clip vertices to terrain DEM bounds so cones never float past the map edge
        const clipped = chain.filter(([x, y]) => x >= wx_min_km && x <= wx_max_km && y >= wy_min_km && y <= wy_max_km);
        if (clipped.length < 2) continue;
        const pts3d = clipped.map(([x, yN]) => new THREE.Vector3(x, this.terrainY(x, -yN) + 0.19, -yN));
        const cumLen = new Float32Array(pts3d.length);
        let acc = 0;
        for (let i = 1; i < pts3d.length; i++) {
          acc += Math.hypot(pts3d[i].x - pts3d[i - 1].x, pts3d[i].z - pts3d[i - 1].z);
          cumLen[i] = acc;
        }
        if (acc < 3.5) continue; // Skip tiny sub-4 km islands/braids
        const count = Math.max(2, Math.round(acc / spacingKm));
        const chainTrack = { pts3d, cumLen, totalLen: acc, speedKmS };
        for (let i = 0; i < count; i++) {
          const cone = new THREE.Mesh(coneGeo, coneMat);
          this.hydroGroup.add(cone);
          this.flowPulses.push({ mesh: cone, track: chainTrack, phase: i / count });
        }
      }
    }

    // Build opt-in What-If Storm-Wash Runoff Fate Layer along exact IAAAP -> Long Creek -> Skunk -> Mississippi corridor!
    this._rebuildRunoffOverlay(stitchedByName);

    // 3 Clickable Municipal Drinking-Water Intake Beacons (visually showing upstream vs RO vs mainstem dilution!)
    const intakes = [
      {
        lat: 40.8165, lon: -91.0970, color: 0x10b981, label: 'Burlington Water (UPSTREAM ✓)', fg: '#6ee7b7', bg: 'rgba(5,28,20,0.92)', lblDy: 1.05,
        title: 'Burlington Municipal Waterworks (UPSTREAM)',
        body: `<div style="margin-bottom:5px"><span class="tag-live">● Hydrologically Upstream · Isolated</span></div>` +
              `<b>Source:</b> ~80% Mississippi River intake (<b>~12 km UPSTREAM</b> of the Skunk River confluence) + ~20% Pleistocene sand/gravel wells.<br>` +
              `<b>Pathway Status:</b> Skunk River water flows <b>south</b> away from Burlington; airborne winds blew <b>SW</b> away from town.<br>` +
              `<b>Drinking-Water Risk:</b> <strong style="color:#34d399">Hydrologically isolated (No blast runoff path)</strong>.`,
      },
      {
        lat: 40.6230, lon: -91.3080, color: 0x38bdf8, label: 'Ft. Madison Water (RO Wells ✓)', fg: '#7dd3fc', bg: 'rgba(6,22,36,0.92)', lblDy: 1.35,
        title: 'Fort Madison Municipal Water (Alluvial Wells + RO)',
        body: `<div style="margin-bottom:5px"><span class="tag-live">● Alluvial Aquifer + Reverse Osmosis</span></div>` +
              `<b>Source:</b> Municipal alluvial wells treated with <b>Reverse Osmosis (RO)</b> membranes (no direct raw Mississippi River intake).<br>` +
              `<b>Treatment Barrier:</b> Natural sand/gravel riverbank filtration + RO rejects &gt;98–99% of dissolved metals and 100% of particulate lead.<br>` +
              `<b>Drinking-Water Risk:</b> <strong style="color:#38bdf8">Negligible (Multi-barrier well + RO protection)</strong>.`,
      },
      {
        lat: 40.3980, lon: -91.3750, color: 0x60a5fa, label: 'Keokuk Water (~50k cfs Dilution)', fg: '#93c5fd', bg: 'rgba(8,18,36,0.90)', lblDy: 1.05,
        title: 'Keokuk Municipal Waterworks (Downstream Mississippi)',
        body: `<div style="margin-bottom:5px"><span class="tag-live">● Downstream Mainstem · Massive Dilution</span></div>` +
              `<b>Source:</b> Mississippi River mainstem ~50 km downstream of the Skunk River confluence.<br>` +
              `<b>Dilution + Treatment:</b> ~49,000–50,000 cfs Nov Mississippi flow (~15× Skunk River, ~1,000× Long Creek stormflow) plus municipal coagulation/filtration (&gt;95% particulate Pb removal).<br>` +
              `<b>Screening Level:</b> Even a hypothetical 0.1% pad storm-wash pulse dilutes to <b>&lt;0.1 µg/L</b> (vs. 15 µg/L EPA action level).`,
      },
    ];
    for (const w of intakes) {
      const { x, z } = this.ll2xz(w.lat, w.lon);
      const baseY = this.terrainY(x, z);
      const pin = new THREE.Mesh(
        new THREE.CylinderGeometry(0.24, 0.14, 0.72, 12),
        new THREE.MeshStandardMaterial({ color: w.color, emissive: w.color, emissiveIntensity: 0.65 })
      );
      pin.position.set(x, baseY + 0.42, z);
      pin.userData = { type: 'water_intake', title: w.title, body: w.body };
      this.hydroGroup.add(pin);
      this.pickables.push(pin);

      const lbl = this._makeSpriteLabel(w.label, w.fg, w.bg, 19, { sizeMul: 0.80, groupTag: 'hydro' });
      lbl.position.set(x, baseY + (w.lblDy || 1.05), z);
      this.hydroGroup.add(lbl);
    }
  }

  _rebuildRunoffOverlay(stitchedByName) {
    while (this.runoffGroup.children.length) this.runoffGroup.remove(this.runoffGroup.children[0]);
    this.allLabels = this.allLabels.filter((sp) => sp.userData.groupTag !== 'runoff');
    this.pickables = this.pickables.filter((m) => m.userData.type !== 'runoff_zone');
    this.runoffParticles = [];
    this.runoffTrack = null;

    const lcList = stitchedByName['Long Creek'] || [];
    const skList = stitchedByName['Skunk River'] || [];
    const msList = stitchedByName['Mississippi River'] || [];
    const lc = lcList.find((c) => c.some((p) => Math.hypot(p[0], p[1]) < 3.0));
    const sk = [...skList].sort((a, b) => b.length - a.length)[0];
    const ms = [...msList].sort((a, b) => b.length - a.length)[0];
    if (!lc || !sk || !ms) return;

    const closestIdx = (arr, tx, ty) => {
      let best = 0, dMin = 1e9;
      for (let i = 0; i < arr.length; i++) {
        const d = Math.hypot(arr[i][0] - tx, arr[i][1] - ty);
        if (d < dMin) { dMin = d; best = i; }
      }
      return best;
    };

    const lcRun = lc.slice(closestIdx(lc, 0, 0));
    const lcEnd = lcRun[lcRun.length - 1];
    const skRun = sk.slice(closestIdx(sk, lcEnd[0], lcEnd[1]));
    const skEnd = skRun[skRun.length - 1];
    const msRunAll = ms.slice(closestIdx(ms, skEnd[0], skEnd[1]));
    // Trim Mississippi corridor cleanly just south of Keokuk (y >= -52 km)
    const msRun = msRunAll.filter(([, y]) => y >= -52.0);

    // Combine exact overland pad swale -> Long Creek -> Lower Skunk -> Downstream Mississippi
    const rawPts = [[0.0, 0.0], ...lcRun, ...skRun.slice(1), ...msRun];
    const idxLcEnd = 1 + lcRun.length - 1;
    const idxSkEnd = idxLcEnd + Math.max(0, skRun.length - 1);
    const pts3d = rawPts.map(([x, yN]) => new THREE.Vector3(x, this.terrainY(x, -yN) + 0.24, -yN));
    const cumLen = new Float32Array(pts3d.length);
    let acc = 0;
    for (let i = 1; i < pts3d.length; i++) {
      acc += Math.hypot(pts3d[i].x - pts3d[i - 1].x, pts3d[i].z - pts3d[i - 1].z);
      cumLen[i] = acc;
    }
    const dLongCreekEnd = cumLen[idxLcEnd];
    const dSkunkEnd = cumLen[idxSkEnd];
    this.runoffTrack = { pts3d, cumLen, totalLen: acc, dLongCreekEnd, dSkunkEnd };

    // 1. Glowing Amber Streambed Sediment-Settling Ribbon along Long Creek (where ~90% of silt-bound Pb deposits!)
    const lcCurvePts = pts3d.slice(0, idxLcEnd + 1);
    if (lcCurvePts.length >= 2) {
      const lcTube = new THREE.Mesh(
        new THREE.TubeGeometry(new THREE.CatmullRomCurve3(lcCurvePts), 48, 0.16, 6, false),
        new THREE.MeshBasicMaterial({ color: 0xf59e0b, transparent: true, opacity: 0.48, depthWrite: false })
      );
      this.runoffGroup.add(lcTube);
    }
    // 2. Cooler Diluted Corridor Ribbon along Lower Skunk River (65x live USGS dilution zone)
    const skCurvePts = pts3d.slice(idxLcEnd, idxSkEnd + 1);
    if (skCurvePts.length >= 2) {
      const skTube = new THREE.Mesh(
        new THREE.TubeGeometry(new THREE.CatmullRomCurve3(skCurvePts), 40, 0.13, 6, false),
        new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.28, depthWrite: false })
      );
      this.runoffGroup.add(skTube);
    }

    // 3. Clickable Runoff Fate Milestone Rings (live-computed from USGS Skunk River flow & selected wash-off %)
    const calcRunoffStats = () => {
      const skunkM3s = (this.liveData?.usgs_gauges || []).find((g) => g.site_id === '05474000')?.discharge_m3s || 92.3;
      const skunkCfs = Math.round(skunkM3s / 0.0283168);
      const mobKg = 3049.0 * (this.runoffPct / 100.0);
      const pulseSec = 6 * 3600;
      return {
        mobKg,
        skunkCfs,
        dilSkunk: Math.round(skunkM3s / 1.4),
        lcTot: (mobKg * 1e6) / (1.4 * pulseSec),
        skTot: (mobKg * 1e6) / (skunkM3s * pulseSec),
        msTot: (mobKg * 1e6) / (1400.0 * pulseSec),
      };
    };

    const addMilestone = (distKm, colorHex, labelTxt, fg, bg, title, bodyFn) => {
      const pos = new THREE.Vector3();
      this._sampleTrackAtDist(pts3d, cumLen, distKm, pos, null);
      const ringGeo = new THREE.RingGeometry(0.32, 0.56, 24);
      ringGeo.rotateX(-Math.PI / 2);
      const ring = new THREE.Mesh(
        ringGeo,
        new THREE.MeshBasicMaterial({ color: colorHex, side: THREE.DoubleSide, transparent: true, opacity: 0.85, depthWrite: false })
      );
      ring.position.set(pos.x, pos.y + 0.04, pos.z);
      ring.userData = {
        type: 'runoff_zone',
        title,
        getBody: () => bodyFn(calcRunoffStats()),
      };
      this.runoffGroup.add(ring);
      this.pickables.push(ring);

      const lbl = this._makeSpriteLabel(labelTxt, fg, bg, 18, { sizeMul: 0.78, groupTag: 'runoff' });
      lbl.position.set(pos.x, pos.y + 0.95, pos.z);
      this.runoffGroup.add(lbl);
    };

    addMilestone(
      dLongCreekEnd * 0.58, 0xf59e0b, 'What-If: Long Creek Settling (~90% Silt)', '#fcd34d', 'rgba(42,22,4,0.92)',
      'Hypothetical Stage 1: Long Creek Sediment Settling Zone',
      (st) => `<div style="margin-bottom:5px"><span class="tag-proj">⚠ Hypothetical Storm-Wash (${this.runoffPct}% = ${st.mobKg.toFixed(2)} kg / 6h)</span></div>` +
              `<b>Current Weather:</b> Dry (0 mm rain) — <b>NOT an active spill</b>.<br>` +
              `<b>If Uncapped Rain Hits:</b> ~90% of washed lead binds tightly to clay/silt (<code>Kd ~500–2,000 L/kg</code>) and settles onto Long Creek's streambed inside the first 6–8 km.<br>` +
              `<b>Modeled Storm Pulse (~50 cfs):</b> <strong style="color:#fbbf24">${st.lcTot.toFixed(1)} µg/L total</strong> (~${(st.lcTot * 0.1).toFixed(1)} µg/L dissolved).`
    );
    addMilestone(
      dLongCreekEnd + (dSkunkEnd - dLongCreekEnd) * 0.52, 0x22d3ee, 'What-If: Skunk River (~65× Dilution)', '#67e8f9', 'rgba(4,24,34,0.92)',
      'Hypothetical Stage 2: Skunk River Instant Dilution Zone',
      (st) => `<div style="margin-bottom:5px"><span class="tag-proj">⚠ Hypothetical Storm-Wash (${this.runoffPct}% = ${st.mobKg.toFixed(2)} kg / 6h)</span></div>` +
              `<b>Live USGS Skunk Flow:</b> <strong style="color:#22d3ee">${st.skunkCfs.toLocaleString()} cfs</strong> (~<b>${st.dilSkunk}×</b> dilution vs. Long Creek stormflow).<br>` +
              `<b>Modeled Storm Pulse:</b> <strong style="color:#67e8f9">${st.skTot.toFixed(2)} µg/L total</strong> (~${(st.skTot * 0.1).toFixed(2)} µg/L dissolved) vs. <b>15 µg/L</b> EPA drinking water action level.`
    );
    addMilestone(
      Math.min(acc - 4, dSkunkEnd + 16.0), 0x60a5fa, 'What-If: Mississippi (~1,000× Dilution)', '#93c5fd', 'rgba(8,18,36,0.90)',
      'Hypothetical Stage 3: Mississippi Mainstem Dilution',
      (st) => `<div style="margin-bottom:5px"><span class="tag-proj">⚠ Hypothetical Storm-Wash (${this.runoffPct}% = ${st.mobKg.toFixed(2)} kg / 6h)</span></div>` +
              `<b>Mississippi Mainstem (~50,000 cfs):</b> ~<b>1,000×</b> dilution vs. Long Creek.<br>` +
              `<b>Modeled Pulse:</b> <strong style="color:#93c5fd">${st.msTot.toFixed(3)} µg/L total</strong> (~${(st.msTot * 0.1).toFixed(4)} µg/L dissolved) — indistinguishable from river background.<br>` +
              `<b>Drinking Water:</b> Burlington sits <b>12 km UPSTREAM</b> (clean blue); Fort Madison uses <b>Alluvial Wells + RO</b>.`
    );

    // 4. Animated Wash-Off Transport Packets (56 Amber Silt-Bound ~90% + 16 Violet Dissolved ~10%)
    const pktGeo = new THREE.SphereGeometry(0.24, 10, 10);
    const totalPkts = 72;
    for (let i = 0; i < totalPkts; i++) {
      const isDissolved = i >= 56;
      // Deterministic pseudo-random jitter so every rebuild is stable
      const r1 = ((i * 73 + 17) % 97) / 97;
      const r2 = ((i * 41 + 29) % 89) / 89;
      // ~46% of heavy silt packets settle & stop inside Long Creek (visually proving creek-bed sediment trapping!)
      const settlesInCreek = !isDissolved && i < 26;
      const maxDist = settlesInCreek
        ? dLongCreekEnd * (0.22 + 0.78 * r1)
        : (isDissolved ? acc : Math.min(acc, dSkunkEnd + 24.0 * r1));
      const mat = new THREE.MeshBasicMaterial({
        color: isDissolved ? 0xc084fc : 0xfbbf24,
        transparent: true,
        opacity: 0.9,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(pktGeo, mat);
      this.runoffGroup.add(mesh);
      this.runoffParticles.push({
        mesh,
        mat,
        isDissolved,
        settlesInCreek,
        phase: (i * 0.137 + r2 * 0.5) % 1.0,
        maxDist,
        speedKmS: isDissolved ? 3.2 : (settlesInCreek ? 1.45 : 2.15),
        lateralOffset: (r2 - 0.5) * 0.32,
      });
    }
    this.runoffGroup.visible = this.runoffSimActive;
  }

  _sampleTrackAtDist(pts3d, cumLen, dist, outPos, outDir) {
    const clamped = THREE.MathUtils.clamp(dist, 0, cumLen[cumLen.length - 1]);
    let lo = 0, hi = cumLen.length - 2;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (cumLen[mid] <= clamped) lo = mid; else hi = mid - 1;
    }
    const segLen = cumLen[lo + 1] - cumLen[lo];
    const f = segLen > 1e-6 ? (clamped - cumLen[lo]) / segLen : 0;
    const p0 = pts3d[lo], p1 = pts3d[lo + 1];
    outPos.set(
      p0.x + (p1.x - p0.x) * f,
      p0.y + (p1.y - p0.y) * f,
      p0.z + (p1.z - p0.z) * f
    );
    if (outDir) {
      outDir.subVectors(p1, p0);
      if (outDir.lengthSq() > 1e-8) outDir.normalize();
    }
  }

  setRunoffSim(enabled, pct = null) {
    this.runoffSimActive = !!enabled;
    if (pct != null && Number.isFinite(Number(pct))) this.runoffPct = Number(pct);
    this.runoffGroup.visible = this.runoffSimActive;
  }

  _buildParticles() {
    const N = this.plume.n_particles;
    this.partPositions = new Float32Array(N * 3);
    this.partColors = new Float32Array(N * 3);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(this.partPositions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(this.partColors, 3));

    // Create crisp soft glowing circular particle sprite texture!
    const c = document.createElement('canvas');
    c.width = 64; c.height = 64;
    const ctx = c.getContext('2d');
    const g = ctx.createRadialGradient(32, 32, 2, 32, 32, 30);
    g.addColorStop(0, 'rgba(255,255,255,1)');
    g.addColorStop(0.35, 'rgba(255,255,255,0.85)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 64, 64);
    const pTex = new THREE.CanvasTexture(c);

    const mat = new THREE.PointsMaterial({
      size: 0.72,
      map: pTex,
      vertexColors: true,
      transparent: true,
      opacity: 0.90,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true,
    });
    this.points = new THREE.Points(geo, mat);
    this.points.frustumCulled = false;
    this.scene.add(this.points);
  }

  _buildAtmosphericLids() {
    const { wx_min_km, wx_max_km, wy_min_km, wy_max_km } = this.dem;
    const W = wx_max_km - wx_min_km;
    const H = wy_max_km - wy_min_km;
    const cx = (wx_min_km + wx_max_km) / 2;
    const cz = -(wy_min_km + wy_max_km) / 2;
    // Dynamic Morning PBL Ceiling Plane (translucent emerald green!)
    const pblGeo = new THREE.PlaneGeometry(W, H);
    pblGeo.rotateX(-Math.PI / 2);
    this.pblMesh = new THREE.Mesh(
      pblGeo,
      new THREE.MeshBasicMaterial({ color: 0x00ff88, transparent: true, opacity: 0.08, side: THREE.DoubleSide, depthWrite: false })
    );
    this.pblMesh.position.set(cx, 0.285 * this.vertExag, cz);
    this.lidGroup.add(this.pblMesh);

    const pblEdges = new THREE.LineSegments(
      new THREE.EdgesGeometry(pblGeo),
      new THREE.LineBasicMaterial({ color: 0x00ff88, transparent: true, opacity: 0.45 })
    );
    this.pblMesh.add(pblEdges);

    // Fixed Subsidence Inversion Cap (1,075 m AGL, crimson translucent lid!)
    const invGeo = new THREE.PlaneGeometry(W, H);
    invGeo.rotateX(-Math.PI / 2);
    this.invMesh = new THREE.Mesh(
      invGeo,
      new THREE.MeshBasicMaterial({ color: 0xff4455, transparent: true, opacity: 0.07, side: THREE.DoubleSide, depthWrite: false })
    );
    this.invMesh.position.set(cx, 1.075 * this.vertExag, cz);
    this.lidGroup.add(this.invMesh);

    const invEdges = new THREE.LineSegments(
      new THREE.EdgesGeometry(invGeo),
      new THREE.LineBasicMaterial({ color: 0xff4455, transparent: true, opacity: 0.5 })
    );
    this.invMesh.add(invEdges);

    this.invLbl = this._makeSpriteLabel('PROJECTED LID: 1,075 m Inversion Cap (12Z)', '#ff7788', 'rgba(20,8,12,0.85)', 20);
    this.invLbl.position.set(-22, 1.075 * this.vertExag + 0.55, -10);
    this.lidGroup.add(this.invLbl);

    this.pblLbl = this._makeSpriteLabel('MODELED PBL Ceiling', '#fcd34d', 'rgba(24,18,6,0.85)', 20);
    this.pblLbl.position.set(-22, 0.285 * this.vertExag + 0.45, 28);
    this.lidGroup.add(this.pblLbl);
  }

  updateLiveTelemetry(liveData) {
    this.liveData = liveData;
    // Clear existing dynamic sensors, wind arrows & USGS gauges
    while (this.sensorGroup.children.length) this.sensorGroup.remove(this.sensorGroup.children[0]);
    while (this.windGroup.children.length) this.windGroup.remove(this.windGroup.children[0]);
    while (this.usgsGroup.children.length) this.usgsGroup.remove(this.usgsGroup.children[0]);
    // Keep only static/hydro pickables & labels (prevent memory leaks on refresh / exag change)
    this.pickables = this.pickables.filter((m) => ['origin', 'camera', 'tower', 'ballistic_halo', 'water_intake', 'runoff_zone'].includes(m.userData.type));
    this.allLabels = this.allLabels.filter((sp) => sp.userData.groupTag !== 'live');
    this.xactVisuals = {};
    this.windVisuals = [];
    this.shearMasts = [];

    // Rebuild live-assimilated 3D particle trajectories from latest ASOS + RWIS wind assimilation!
    const wa = liveData.wind_assimilation;
    if (wa && wa.u_assim) {
      this.windObsCutoffSec = (wa.obs_cutoff_min ?? 0) * 60;
      this.assimilatedPlume = this._buildAssimilatedPlume(wa);
      if (this.plumeMode === 'assimilated' && this.assimilatedPlume) {
        this.plume = this.assimilatedPlume;
      }
    }

    const xactMap = liveData.epa_xact || {};
    const { wx_min_km, wx_max_km, wy_min_km, wy_max_km } = this.dem;

    // Determine latest live EPA observation timestamp across all Xact stations
    let maxObs = 0;
    for (const st of Object.values(xactMap)) {
      const ser = st.series || [];
      if (ser.length) {
        maxObs = Math.max(maxObs, (ser[ser.length - 1].min_post_blast ?? 0) * 60);
      }
    }
    this.latestXactObsSec = maxObs || 6600;

    // 1. Plot all EPA Air Monitoring Stations (highlighting live Xact AS01..AS04 with dynamic time-synced towers!)
    for (const mon of (liveData.epa_monitors || [])) {
      const { x, z } = this.ll2xz(mon.lat, mon.lon);
      if (x < wx_min_km || x > wx_max_km || -z < wy_min_km || -z > wy_max_km) continue;
      const key = String(mon.station_id || '').replace('-', '');
      const xact = xactMap[key];
      const baseY = this.terrainY(x, z);

      if (xact) {
        const pillarMat = new THREE.MeshStandardMaterial({
          color: 0x00ff88, emissive: 0x00ff88, emissiveIntensity: 0.72, transparent: true, opacity: 0.96,
        });
        const pillar = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.28, 1.4, 14), pillarMat);
        pillar.position.set(x, baseY + 0.72, z);

        // Ground pulse halo ring (lights up amber when live Xact records >0 trace at playback time T!)
        const ringGeo = new THREE.RingGeometry(0.45, 0.78, 28);
        ringGeo.rotateX(-Math.PI / 2);
        const ringMat = new THREE.MeshBasicMaterial({
          color: 0x00ff88, side: THREE.DoubleSide, transparent: true, opacity: 0.55, depthWrite: false,
        });
        const ring = new THREE.Mesh(ringGeo, ringMat);
        ring.position.set(x, baseY + 0.14, z);
        this.sensorGroup.add(ring);

        pillar.userData = {
          type: 'xact',
          sid: key,
          title: `EPA Live Xact ${mon.station_id}`,
          getBody: (curSec) => {
            const snap = this._lookupXactAtSec(xact, curSec);
            const atTRow = snap.status === 'future'
              ? `<span style="color:#fcd34d;font-weight:700">No obs yet (playback past ${String(xact.latest_ts_utc || '').slice(11, 16)}Z)</span>`
              : `<span style="color:${snap.status === 'trace' ? '#fbbf24' : '#00ff88'};font-weight:700">${snap.value.toFixed(4)} ${xact.units}</span> (bin T+${Math.round(snap.obsMin)}m · ${String(snap.obsTs || '').slice(11, 16)}Z)`;
            return `<div style="margin-bottom:5px"><span class="tag-live">● Live Observed Sensor</span></div>` +
              `<b>Reading @ 3D Clock:</b> ${atTRow}<br>` +
              `<b>Latest Published (${String(xact.latest_ts_utc || '').slice(11, 16)}Z):</b> ${(xact.latest_value ?? 0).toFixed(4)} ${xact.units}<br>` +
              `<b>Post-Blast Max:</b> ${xact.post_blast_max.toFixed(4)} µg/m³ | <b>Pre-Blast Max:</b> ${xact.pre_blast_max.toFixed(4)} µg/m³<br>` +
              `<b>Distance:</b> ${xact.dist_km} km @ ${xact.bearing_deg}° | <b>Obs Count:</b> ${xact.obs_count}<br>` +
              `<div style="margin-top:5px;padding-top:4px;border-top:1px solid rgba(255,255,255,0.12)"><span class="tag-proj">◆ Modeled Projection</span> Hit prob: ${xact.hit_prob_pct ?? 'N/A'}% (peak ~T+${xact.modeled_peak_min ?? '?'}m)` +
              (xact.f_max_pct ? `<br><span class="tag-hybrid">Inverse Bound</span> f_Pb ≲ ${xact.f_max_pct.toFixed(4)}% (≤${xact.q_max_kg} kg)` : '') + `</div>`;
          },
        };
        this.sensorGroup.add(pillar);
        this.pickables.push(pillar);

        const dynLabel = this._makeDynamicSprite(`● ${key}: ${(xact.latest_value ?? 0).toFixed(4)} µg/m³`, '#6ee7b7', 'rgba(6,22,16,0.92)', { sizeMul: 1.05, groupTag: 'live' });
        dynLabel.sprite.position.set(x, baseY + 1.95, z);
        this.sensorGroup.add(dynLabel.sprite);

        this.xactVisuals[key] = { sid: key, pillar, ring, dynLabel, baseY, xact, x, z, lastKey: null, status: 'zero' };
      } else {
        const cone = new THREE.Mesh(
          new THREE.ConeGeometry(0.24, 0.55, 6),
          new THREE.MeshStandardMaterial({ color: 0x10b981, emissive: 0x059669, emissiveIntensity: 0.35 })
        );
        cone.position.set(x, baseY + 0.3, z);
        cone.userData = {
          type: 'epa',
          title: `EPA Station ${mon.station_id}`,
          body: `<div style="margin-bottom:5px"><span class="tag-live">● Live Deployed Site</span></div>` +
                `<b>Instruments:</b> ${mon.instrument_type}<br><b>Distance:</b> ${mon.dist_km} km @ ${mon.bearing_deg}°<br><b>Coords:</b> ${mon.lat.toFixed(4)}°N, ${mon.lon.toFixed(4)}°W`
        };
        this.sensorGroup.add(cone);
        this.pickables.push(cone);
      }
    }

    // 2. Plot Time-Synced Live ASOS & Iowa DOT RWIS 3D Wind Arrows!
    const addWindArrow = (sid, net, lat, lon, baseColor, series, fallbackRec, labelTitle) => {
      const { x, z } = this.ll2xz(lat, lon);
      if (x < wx_min_km - 5 || x > wx_max_km + 5 || -z < wy_min_km - 5 || -z > wy_max_km + 5) return;
      const baseY = this.terrainY(x, z) + 0.55;
      const validSer = (series || []).filter((r) => r.wind_to_deg != null && r.wind_ms != null);
      const initTo = fallbackRec?.wind_to_deg ?? validSer[validSer.length - 1]?.wind_to_deg;
      const initSpd = fallbackRec?.wind_ms ?? validSer[validSer.length - 1]?.wind_ms;
      if (initTo == null || initSpd == null) return;

      const rad = (initTo * Math.PI) / 180;
      const dir = new THREE.Vector3(Math.sin(rad), 0, -Math.cos(rad)).normalize();
      const len = Math.max(1.8, initSpd * 0.9);
      const arrow = new THREE.ArrowHelper(dir, new THREE.Vector3(x, baseY, z), len, baseColor, 0.9, 0.55);
      this.windGroup.add(arrow);

      const orbMat = new THREE.MeshBasicMaterial({ color: baseColor });
      const orb = new THREE.Mesh(new THREE.SphereGeometry(0.28, 12, 12), orbMat);
      orb.position.set(x, baseY, z);
      const lastObsSec = validSer.length ? Math.max(0, validSer[validSer.length - 1].min_post_blast * 60) : 0;

      const vis = { sid, net, arrow, orb, baseColor, series: validSer, fallbackRec, lastObsSec };
      orb.userData = {
        type: 'wind',
        title: labelTitle,
        getBody: (curSec) => {
          const w = this._lookupWindAtSec(vis, curSec);
          const atTRow = w.status === 'future'
            ? `<span style="color:#fcd34d">Holding last obs (${String(w.tsUtc || '').slice(11, 16)}Z)</span>`
            : `<span style="color:#6ee7b7;font-weight:700">${Math.round(w.fromDeg)}° → ${Math.round(w.toDeg)}° @ ${w.spdMs} m/s</span> (${String(w.tsUtc || '').slice(11, 16)}Z)`;
          const latestSpd = fallbackRec?.wind_ms ?? w.spdMs;
          const latestFrom = fallbackRec?.wind_from_deg ?? w.fromDeg;
          const latestTo = fallbackRec?.wind_to_deg ?? w.toDeg;
          const latestTs = fallbackRec?.valid_utc || w.tsUtc || '';
          return `<div style="margin-bottom:5px"><span class="tag-live">● Live Time-Synced ${net}</span></div>` +
            `<b>Wind @ 3D Clock:</b> ${atTRow}<br>` +
            `<b>Latest (${String(latestTs).slice(11, 16)}Z):</b> FROM ${Math.round(latestFrom)}° → TO ${Math.round(latestTo)}° @ ${latestSpd} m/s (${(latestSpd * 2.237).toFixed(1)} mph)<br>` +
            `<b>24h Time-Series Stored:</b> ${validSer.length} wind observations`;
        },
      };
      this.windGroup.add(orb);
      this.pickables.push(orb);
      this.windVisuals.push(vis);
    };

    for (const [k, st] of Object.entries(liveData.asos || {})) {
      addWindArrow(k, 'ASOS', st.lat, st.lon, 0xffdd44, st.series_24h, st.latest, `ASOS Weather ${k}`);
    }
    for (const [k, rw] of Object.entries(liveData.rwis || {})) {
      addWindArrow(k, 'IA RWIS', rw.lat, rw.lon, 0x38bdf8, rw.series_24h, rw, `Iowa DOT RWIS ${k} (${rw.name})`);
    }

    // 3. Plot Live USGS NWIS Stream Gauges (receiving rivers & watershed dilution anchors!)
    for (const g of (liveData.usgs_gauges || [])) {
      const { x, z } = this.ll2xz(g.lat, g.lon);
      if (x < wx_min_km || x > wx_max_km || -z < wy_min_km || -z > wy_max_km) continue;
      const baseY = this.terrainY(x, z);
      const isDirect = g.site_id === '05474000';
      const beacon = new THREE.Mesh(
        new THREE.OctahedronGeometry(isDirect ? 0.36 : 0.26, 0),
        new THREE.MeshStandardMaterial({
          color: isDirect ? 0x22d3ee : 0x38bdf8,
          emissive: isDirect ? 0x06b6d4 : 0x0284c7,
          emissiveIntensity: 0.72,
        })
      );
      beacon.position.set(x, baseY + 0.55, z);
      const flowStr = g.discharge_cfs != null ? `${g.discharge_cfs.toLocaleString()} cfs (${g.discharge_m3s} m³/s)` : 'Stage/WQ sensor';
      beacon.userData = {
        type: 'usgs',
        title: `USGS Gauge ${g.site_id} — ${g.name}`,
        body: `<div style="margin-bottom:5px"><span class="tag-live">● Live USGS Stream Gauge</span></div>` +
              `<b>Watershed Role:</b> ${g.role}<br>` +
              `<b>Live Flow:</b> <strong style="color:#22d3ee">${flowStr}</strong>` +
              (g.gage_height_ft != null ? ` · Stage: ${g.gage_height_ft} ft` : '') + '<br>' +
              (g.water_temp_c != null ? `<b>Water Temp:</b> ${g.water_temp_c}°C` : '') +
              (g.turbidity_fnu != null ? ` · <b>Turbidity:</b> ${g.turbidity_fnu} FNU<br>` : (g.water_temp_c != null ? '<br>' : '')) +
              `<b>Distance from Blast:</b> ${g.dist_km} km @ ${g.bearing_deg}°` +
              (isDirect ? `<br><span style="color:#86efac">Historical WQP Lead Baseline (40 samples): median 0.15 µg/L, max 0.36 µg/L</span>` : ''),
      };
      this.usgsGroup.add(beacon);
      this.pickables.push(beacon);
      if (isDirect || g.site_id === '05490500' || g.site_id === '05474500') {
        const shortFlow = g.discharge_cfs != null ? ` (${Math.round(g.discharge_cfs).toLocaleString()} cfs)` : (g.turbidity_fnu != null ? ` (${g.turbidity_fnu} FNU)` : '');
        const lbl = this._makeSpriteLabel(`USGS ${g.name.split(' at ')[0]}${shortFlow}`, '#67e8f9', 'rgba(4,20,30,0.88)', 19, { sizeMul: 0.80, groupTag: 'live' });
        lbl.position.set(x, baseY + 1.05, z);
        this.usgsGroup.add(lbl);
      }
    }

    // 4. 3D Vertical Wind-Shear Masts (10m -> 880m AGL) at IAAAP & Kahoka!
    this._buildShearMasts(wa);

    this._updateXactVisualsAtTime(this.simSec);
    this._updateWindVisualsAtTime(this.simSec);
    this._updateShearMastsAtTime(this.simSec);
  }

  _lookupWindAtSec(vis, sec) {
    const ser = vis.series;
    if (!ser || !ser.length) {
      const f = vis.fallbackRec || {};
      return { status: 'live', fromDeg: f.wind_from_deg ?? 0, toDeg: f.wind_to_deg ?? 180, spdMs: f.wind_ms ?? 0, tsUtc: f.valid_utc };
    }
    const secMin = sec / 60.0;
    const lastRec = ser[ser.length - 1];
    if (secMin > lastRec.min_post_blast + 20.0) {
      return {
        status: 'future',
        fromDeg: lastRec.wind_from_deg,
        toDeg: lastRec.wind_to_deg,
        spdMs: lastRec.wind_ms,
        tsUtc: lastRec.ts_utc,
      };
    }
    // Nearest valid observation in time (prefers <= sec + 5m)
    let best = ser[0], bestDt = 1e9;
    for (let i = 0; i < ser.length; i++) {
      const dt = Math.abs(ser[i].min_post_blast - secMin);
      if (dt < bestDt) {
        bestDt = dt;
        best = ser[i];
      }
    }
    return {
      status: 'live',
      fromDeg: best.wind_from_deg,
      toDeg: best.wind_to_deg,
      spdMs: best.wind_ms,
      tsUtc: best.ts_utc,
    };
  }

  _updateWindVisualsAtTime(sec) {
    for (const vis of this.windVisuals) {
      const w = this._lookupWindAtSec(vis, sec);
      if (w.toDeg == null || w.spdMs == null) continue;
      const rad = (w.toDeg * Math.PI) / 180;
      const dir = new THREE.Vector3(Math.sin(rad), 0, -Math.cos(rad)).normalize();
      vis.arrow.setDirection(dir);
      vis.arrow.setLength(Math.max(1.6, w.spdMs * 0.9), 0.85, 0.52);
      const col = w.status === 'future' ? 0x64748b : vis.baseColor;
      vis.arrow.setColor(col);
      vis.orb.material.color.setHex(col);
    }
  }

  _buildShearMasts(wa) {
    if (!wa || !wa.z_grid_m || !wa.times_min || !wa.u_assim) return;
    const zGrid = wa.z_grid_m;
    const tierSpecs = [
      { idx: 0, zM: 10,  color: 0xffdd44, hex: '#ffdd44', note: 'Surface' },
      { idx: 2, zM: 120, color: 0x34d399, hex: '#34d399', note: 'White cap' },
      { idx: 4, zM: 250, color: 0x22d3ee, hex: '#22d3ee', note: 'Lower PBL' },
      { idx: 5, zM: 450, color: 0x38bdf8, hex: '#38bdf8', note: 'Mid PBL' },
      { idx: 7, zM: 880, color: 0xa855f7, hex: '#a855f7', note: 'Upper shear' },
    ].filter((t) => t.idx < zGrid.length);

    const nodes = wa.spatial_nodes || [];
    const iaaapNode = nodes.find((n) => n.id === 'IAAAP') || { id: 'IAAAP', x_km: 0, y_km: 0, u_assim: wa.u_assim, v_assim: wa.v_assim };
    const kahokaNode = nodes.find((n) => n.id === 'Kahoka');
    const mastDefs = [
      { id: 'IAAAP', label: 'IAAAP Shear Mast (10–880m)', xKm: 1.15, zKm: 0.0, node: iaaapNode, isPrimary: true },
      ...(kahokaNode ? [{ id: 'Kahoka', label: 'Kahoka Shear (10–880m)', xKm: kahokaNode.x_km, zKm: -kahokaNode.y_km, node: kahokaNode, isPrimary: false }] : []),
    ];

    for (const def of mastDefs) {
      const baseY = this.terrainY(def.xKm, def.zKm);
      const topZKm = (tierSpecs[tierSpecs.length - 1]?.zM || 880) / 1000.0;
      const poleH = Math.max(0.6, topZKm * this.vertExag);
      const pole = new THREE.Mesh(
        new THREE.CylinderGeometry(0.055, 0.085, poleH, 10),
        new THREE.MeshStandardMaterial({ color: 0x94a3b8, emissive: 0x38bdf8, emissiveIntensity: 0.35, transparent: true, opacity: 0.78 })
      );
      pole.position.set(def.xKm, baseY + poleH / 2, def.zKm);

      const baseRingGeo = new THREE.RingGeometry(0.28, 0.48, 24);
      baseRingGeo.rotateX(-Math.PI / 2);
      const baseRing = new THREE.Mesh(
        baseRingGeo,
        new THREE.MeshBasicMaterial({ color: 0xa855f7, side: THREE.DoubleSide, transparent: true, opacity: 0.65, depthWrite: false })
      );
      baseRing.position.set(def.xKm, baseY + 0.14, def.zKm);
      this.windGroup.add(baseRing);

      const arrows = [];
      for (const tier of tierSpecs) {
        const yAlt = baseY + (zGrid[tier.idx] / 1000.0) * this.vertExag;
        const arr = new THREE.ArrowHelper(new THREE.Vector3(-0.5, 0, 0.866), new THREE.Vector3(def.xKm, yAlt, def.zKm), 2.5, tier.color, 0.68, 0.42);
        this.windGroup.add(arr);
        const bead = new THREE.Mesh(new THREE.SphereGeometry(0.13, 10, 10), new THREE.MeshBasicMaterial({ color: tier.color }));
        bead.position.set(def.xKm, yAlt, def.zKm);
        this.windGroup.add(bead);
        arrows.push({ tier, arrow: arr, zM: zGrid[tier.idx] });
      }

      const mastObj = { id: def.id, node: def.node, wa, arrows, isPrimary: def.isPrimary };
      pole.userData = {
        type: 'shear_mast',
        title: `3D Vertical Wind-Shear Profile — ${def.id}`,
        getBody: (curSec) => {
          const useFcst = this.plumeMode === 'forecast' && wa.u_fcst && wa.v_fcst;
          const rows = [...arrows].reverse().map(({ tier, zM }) => {
            const { u, v } = this._sampleMastWind(mastObj, tier.idx, curSec, useFcst);
            const spd = Math.hypot(u, v);
            const toDeg = (Math.atan2(u, v) * 180 / Math.PI + 360) % 360;
            const fromDeg = (toDeg + 180) % 360;
            return `<div style="display:flex;justify-content:space-between;gap:8px;font-family:ui-monospace,monospace;font-size:0.75rem">` +
              `<span style="color:${tier.hex};font-weight:700">${Math.round(zM)} m (${tier.note})</span>` +
              `<span>${Math.round(fromDeg)}°→${Math.round(toDeg)}° · ${spd.toFixed(1)} m/s (${(spd * 2.237).toFixed(0)} mph)</span></div>`;
          }).join('');
          const modeTag = useFcst
            ? '<span class="tag-proj">◆ 15Z Forecast Profile</span>'
            : '<span class="tag-hybrid">● Live-Assimilated HRRR + ASOS/RWIS</span>';
          return `<div style="margin-bottom:6px">${modeTag}</div>` +
            `<div style="margin-bottom:5px;color:#cbd5e1;font-size:0.74rem">Vertical wind speed &amp; directional shear from ground to near the 1,075 m inversion cap at 3D clock:</div>` +
            rows;
        },
      };
      this.windGroup.add(pole);
      this.pickables.push(pole);

      const lbl = this._makeSpriteLabel(def.label, '#d8b4fe', 'rgba(18,10,32,0.88)', 19, { sizeMul: 0.78, groupTag: 'live' });
      lbl.position.set(def.xKm, baseY + poleH + 0.45, def.zKm);
      this.windGroup.add(lbl);

      this.shearMasts.push(mastObj);
    }
  }

  _sampleMastWind(mast, zIdx, sec, useFcst) {
    const wa = mast.wa;
    const tMinArr = wa.times_min;
    const nT = tMinArr.length;
    const tMin = THREE.MathUtils.clamp(sec / 60.0, tMinArr[0], tMinArr[nT - 1]);
    const idxLo = Math.min(Math.floor(tMin / 10.0), nT - 2);
    const tf = THREE.MathUtils.clamp((tMin - tMinArr[idxLo]) / Math.max(1e-3, tMinArr[idxLo + 1] - tMinArr[idxLo]), 0, 1);
    const uArr = (useFcst && wa.u_fcst) ? wa.u_fcst : (mast.node.u_assim || wa.u_assim);
    const vArr = (useFcst && wa.v_fcst) ? wa.v_fcst : (mast.node.v_assim || wa.v_assim);
    const u = uArr[idxLo][zIdx] * (1 - tf) + uArr[idxLo + 1][zIdx] * tf;
    const v = vArr[idxLo][zIdx] * (1 - tf) + vArr[idxLo + 1][zIdx] * tf;
    return { u, v };
  }

  _updateShearMastsAtTime(sec) {
    if (!this.shearMasts.length) return;
    const useFcst = this.plumeMode === 'forecast';
    for (const mast of this.shearMasts) {
      for (const { tier, arrow } of mast.arrows) {
        const { u, v } = this._sampleMastWind(mast, tier.idx, sec, useFcst);
        const spd = Math.hypot(u, v);
        if (spd < 0.05) continue;
        this._tmpDir.set(u, 0, -v).normalize();
        arrow.setDirection(this._tmpDir);
        arrow.setLength(Math.max(1.3, spd * 0.72), 0.65, 0.40);
      }
    }
  }

  _updateXactVisualsAtTime(sec) {
    const snapshots = {};
    for (const [sid, vis] of Object.entries(this.xactVisuals)) {
      const snap = this._lookupXactAtSec(vis.xact, sec);
      snapshots[sid] = snap;
      vis.status = snap.status;

      let key, text, fg, bg, dashed = false;
      if (snap.status === 'future') {
        const hhmm = String(vis.xact.latest_ts_utc || '').slice(11, 16) || 'latest';
        key = `future:${hhmm}`;
        text = `${sid} · >${hhmm}Z (no obs yet)`;
        fg = '#94a3b8';
        bg = 'rgba(15,23,42,0.88)';
        dashed = true;
        vis.pillar.scale.set(1, 0.72, 1);
        vis.pillar.position.y = vis.baseY + 0.50;
        vis.pillar.material.color.setHex(0x64748b);
        vis.pillar.material.emissive.setHex(0x334155);
        vis.pillar.material.emissiveIntensity = 0.35;
        vis.pillar.material.opacity = 0.58;
        vis.ring.visible = false;
      } else if (snap.status === 'trace') {
        const valStr = snap.value.toFixed(4);
        key = `trace:${valStr}`;
        text = `● ${sid} LIVE: ${valStr} µg/m³`;
        fg = '#fcd34d';
        bg = 'rgba(42,26,4,0.95)';
        const hScale = 1.65 + Math.min(1.4, Math.log10(1 + snap.value * 2000) * 0.45);
        vis.pillar.scale.set(1.22, hScale, 1.22);
        vis.pillar.position.y = vis.baseY + 0.70 * hScale;
        vis.pillar.material.color.setHex(0xfbbf24);
        vis.pillar.material.emissive.setHex(0xf59e0b);
        vis.pillar.material.emissiveIntensity = 1.05;
        vis.pillar.material.opacity = 1.0;
        vis.ring.visible = true;
        vis.ring.material.color.setHex(0xfbbf24);
        vis.ring.material.opacity = 0.85;
      } else {
        key = `zero`;
        text = `● ${sid} LIVE: 0.0000 µg/m³`;
        fg = '#6ee7b7';
        bg = 'rgba(6,22,16,0.92)';
        vis.pillar.scale.set(1, 1, 1);
        vis.pillar.position.y = vis.baseY + 0.72;
        vis.pillar.material.color.setHex(0x00ff88);
        vis.pillar.material.emissive.setHex(0x00ff88);
        vis.pillar.material.emissiveIntensity = 0.65;
        vis.pillar.material.opacity = 0.95;
        vis.ring.visible = true;
        vis.ring.scale.set(1, 1, 1);
        vis.ring.material.color.setHex(0x00ff88);
        vis.ring.material.opacity = 0.38;
      }

      if (vis.lastKey !== key) {
        vis.lastKey = key;
        vis.dynLabel.draw(text, fg, bg, dashed);
      }
    }
    return snapshots;
  }

  inspectXactStation(sid) {
    const vis = this.xactVisuals[sid];
    if (!vis || !this.cb.onSelect) return;
    const ud = vis.pillar.userData;
    this.cb.onSelect({ ...ud, body: ud.getBody(this.simSec) });
  }

  applyVerticalExaggeration(exag) {
    this.vertExag = exag;
    // Update terrain & drape vertices from real DEM!
    const { elev_m, ref_elev_m } = this.dem;
    for (const geo of [this.terrainGeo, this.drapeGeo]) {
      const pos = geo.attributes.position;
      for (let i = 0; i < pos.count; i++) {
        pos.setY(i, ((elev_m[i] - ref_elev_m) / 1000.0) * exag);
      }
      pos.needsUpdate = true;
      geo.computeVertexNormals();
    }
    // Scale Tower T3 (71.6 m = 0.0716 km) & Cam 3 Wilson Drone altitude (100.9 m = 0.1009 km)
    if (this.ballisticHaloDisc && this.ballisticHaloRing) {
      const y0 = this.terrainY(0, 0);
      this.ballisticHaloDisc.position.y = y0 + 0.13;
      this.ballisticHaloRing.position.y = y0 + 0.15;
    }
    if (this.towerT3Mesh) {
      const hT3 = 0.0716 * exag;
      const baseT3 = this.terrainY(this.staticData.tower_t3.x_km, -this.staticData.tower_t3.y_km);
      this.towerT3Mesh.scale.set(1, hT3, 1);
      this.towerT3Mesh.position.y = baseT3 + hT3 / 2;
      this.towerT3Label.position.y = baseT3 + hT3 + 0.40;
    }
    if (this.cam3Mesh && this.staticData.cam3_wilson) {
      const c3 = this.staticData.cam3_wilson;
      const hC3 = (c3.alt_agl_m / 1000.0) * exag;
      const baseC3 = this.terrainY(c3.x_km, -c3.y_km);
      const yTop = baseC3 + hC3;
      this.cam3Mesh.position.y = yTop;
      this.cam3Stem.scale.set(1, Math.max(0.05, hC3), 1);
      this.cam3Stem.position.y = baseC3 + hC3 / 2;
      this.cam3Label.position.y = yTop + 0.55;
      for (const ray of [this.cam3RayN, this.cam3RayS]) {
        if (!ray) continue;
        const pos = ray.geometry.attributes.position;
        pos.setY(0, yTop);
        pos.needsUpdate = true;
      }
    }
    // Update inversion cap height (1.075 km)
    if (this.invMesh) {
      const yInv = 1.075 * exag;
      this.invMesh.position.y = yInv;
      this.invLbl.position.y = yInv + 0.35;
    }
    this._rebuildHydroOverlays();
    if (this.liveData) this.updateLiveTelemetry(this.liveData);
    this.updateParticlesAtTime(this.simSec);
  }

  updateParticlesAtTime(sec) {
    const ts = this.plume.times_sec;
    const clamped = THREE.MathUtils.clamp(sec, 0, this.maxSec);
    // Find bracketing keyframes
    let idx = 0;
    while (idx < ts.length - 2 && ts[idx + 1] < clamped) idx++;
    const t0 = ts[idx], t1 = ts[idx + 1];
    const alpha = t1 > t0 ? (clamped - t0) / (t1 - t0) : 0;

    const f0 = this.plume.frames[idx];
    const f1 = this.plume.frames[idx + 1];
    const N = this.plume.n_particles;
    const regs = this.plume.regimes;
    const pos = this.partPositions;
    const col = this.partColors;
    const exag = this.vertExag;
    let airborneCount = 0;

    for (let i = 0; i < N; i++) {
      const j = i * 3;
      const xKm = (f0[j] + (f1[j] - f0[j]) * alpha) * 0.01;       // 10m units -> km
      const yNorth = (f0[j + 1] + (f1[j + 1] - f0[j + 1]) * alpha) * 0.01;
      const zM = f0[j + 2] + (f1[j + 2] - f0[j + 2]) * alpha;      // meters AGL
      const zKm = -yNorth;
      const gndY = this.terrainY(xKm, zKm);

      pos[j] = xKm;
      pos[j + 1] = gndY + (zM / 1000.0) * exag + 0.04;
      pos[j + 2] = zKm;
      if (zM > 2.0) airborneCount++;

      if (this.colorMode === 'regime') {
        const r = regs[i];
        if (r === 0) { col[j] = 1.0; col[j + 1] = 0.62; col[j + 2] = 0.14; }      // Coarse: bright amber-orange
        else if (r === 1) { col[j] = 0.05; col[j + 1] = 0.88; col[j + 2] = 1.0; } // PM10: electric cyan
        else { col[j] = 0.96; col[j + 1] = 0.32; col[j + 2] = 0.88; }             // PM2.5: vibrant magenta
      } else {
        // Altitude ramp (blue 0m -> green 250m -> yellow 550m -> red 900m+)
        const hNorm = Math.min(1, zM / 900.0);
        col[j] = Math.min(1, hNorm * 1.6);
        col[j + 1] = 1.0 - Math.abs(hNorm - 0.5) * 1.4;
        col[j + 2] = Math.max(0.1, 1.0 - hNorm * 1.5);
      }
      // Slightly dim deposited surface particles
      if (zM <= 1.0) { col[j] *= 0.45; col[j + 1] *= 0.45; col[j + 2] *= 0.45; }
    }
    this.points.geometry.attributes.position.needsUpdate = true;
    this.points.geometry.attributes.color.needsUpdate = true;

    // Update dynamic PBL ceiling height!
    const pblM = this.plume.pbl_m[idx] + (this.plume.pbl_m[idx + 1] - this.plume.pbl_m[idx]) * alpha;
    if (this.pblMesh) {
      this.pblMesh.position.y = (pblM / 1000.0) * exag;
      this.pblLbl.position.y = (pblM / 1000.0) * exag + 0.45;
    }

    // Time-sync 3D live EPA Xact towers, ASOS/RWIS arrows & vertical wind-shear masts with the playback clock!
    const xactSnap = this._updateXactVisualsAtTime(clamped);
    this._updateWindVisualsAtTime(clamped);
    this._updateShearMastsAtTime(clamped);

    if (this.cb.onTick) {
      this.cb.onTick(clamped, pblM, Math.round((airborneCount / N) * 100), xactSnap, this.latestXactObsSec, this.windObsCutoffSec, this.plumeMode);
    }
  }

  setBaseMap(mode) {
    this.terrainMat.map = mode === 'sat' ? this.textures.sat : this.textures.dark;
    this.terrainMat.needsUpdate = true;
  }

  setDrape(mode) {
    if (mode === 'none') {
      this.drapeMesh.visible = false;
    } else {
      this.drapeMesh.visible = true;
      this.drapeMat.map = this.textures[mode];
      this.drapeMat.needsUpdate = true;
    }
  }

  setColorMode(mode) {
    this.colorMode = mode;
    this.updateParticlesAtTime(this.simSec);
  }

  setLayerVisible(name, vis) {
    if (name === 'particles' && this.points) this.points.visible = vis;
    if (name === 'sightlines') this.sightlineGroup.visible = vis;
    if (name === 'sensors') this.sensorGroup.visible = vis;
    if (name === 'winds') this.windGroup.visible = vis;
    if (name === 'hydro') { this.hydroGroup.visible = vis; this.usgsGroup.visible = vis; }
    if (name === 'lids') this.lidGroup.visible = vis;
    if (name === 'labels') this.labelGroup.visible = vis;
  }

  flyToPreset(preset) {
    const s = this.staticData;
    if (preset === 'overview') {
      this.cameraPosGoal = new THREE.Vector3(22, 22, 31);
      this.cameraTargetGoal = new THREE.Vector3(-15, 1.5, 11);
    } else if (preset === 'kwqc') {
      // Exact KWQC Cam 1 viewpoint on 190th Ave looking SSE across FAA Tower T3!
      const c1 = s.cam1_kwqc;
      this.cameraPosGoal = new THREE.Vector3(c1.x_km, this.terrainY(c1.x_km, -c1.y_km) + 0.35, -c1.y_km);
      this.cameraTargetGoal = new THREE.Vector3(0, 0.12 * this.vertExag, 0);
      this.simSec = 25; this.updateParticlesAtTime(this.simSec);
    } else if (preset === 'kcrg') {
      // Exact KCRG/AP Cam 2 viewpoint at 9856 202nd Ave looking ESE!
      const c2 = s.cam2_kcrg;
      this.cameraPosGoal = new THREE.Vector3(c2.x_km, this.terrainY(c2.x_km, -c2.y_km) + 0.35, -c2.y_km);
      this.cameraTargetGoal = new THREE.Vector3(0, 0.14 * this.vertExag, 0);
      this.simSec = 35; this.updateParticlesAtTime(this.simSec);
    } else if (preset === 'wilson' && s.cam3_wilson) {
      // Exact James Wilson Cam 3 Aerial Drone viewpoint (101 m AGL on 80th St, looking due East into both bunkers!)
      const c3 = s.cam3_wilson;
      const yDrone = this.terrainY(c3.x_km, -c3.y_km) + (c3.alt_agl_m / 1000.0) * this.vertExag;
      this.cameraPosGoal = new THREE.Vector3(c3.x_km, Math.max(0.45, yDrone), -c3.y_km);
      this.cameraTargetGoal = new THREE.Vector3(0, 0.085 * this.vertExag, 0);
      this.simSec = 5; this.updateParticlesAtTime(this.simSec);
    } else if (preset === 'shear') {
      // Dramatic side cross-section showing vertical wind shear masts & 1,075m inversion cap!
      this.cameraPosGoal = new THREE.Vector3(-8, 6.5, 44);
      this.cameraTargetGoal = new THREE.Vector3(-22, 4.5, 12);
    } else if (preset === 'watershed') {
      // Frame the IAAAP on-site containment zone -> Long Creek -> Skunk River @ Augusta -> Mississippi + Burlington Upstream intake!
      this.cameraPosGoal = new THREE.Vector3(17.5, 13.5, 24.5);
      this.cameraTargetGoal = new THREE.Vector3(3.5, 0.5, 5.5);
    } else if (preset === 'top') {
      this.cameraPosGoal = new THREE.Vector3(-56, 175, 42);
      this.cameraTargetGoal = new THREE.Vector3(-56, 0, 41.5);
    }
  }

  _animate() {
    requestAnimationFrame(() => this._animate());
    const dt = Math.min(this.clock.getDelta(), 0.1);

    if (this.playing) {
      // Adaptive time pace: slower during dramatic first 2 minutes so users see dual-bunker ascent, faster over 4 hours!
      const pace = this.simSec < 120 ? this.speedMul * 0.25 : this.speedMul * 4.5;
      this.simSec += dt * pace;
      if (this.simSec > this.maxSec) this.simSec = 0;
      this.updateParticlesAtTime(this.simSec);
    }

    // Gentle pulse animation on any live EPA Xact station experiencing >0 trace at current playback time
    const nowSec = performance.now() * 0.001;
    for (const vis of Object.values(this.xactVisuals)) {
      if (vis.status === 'trace' && vis.ring.visible) {
        const s = 1.0 + 0.45 * (0.5 + 0.5 * Math.sin(nowSec * 5.2));
        vis.ring.scale.set(s, s, 1);
      }
    }

    // Animate structural downstream water-flow direction chevrons along EVERY stitched USGS NHD river & creek!
    if (this.hydroGroup.visible && this.flowPulses.length) {
      for (let i = 0; i < this.flowPulses.length; i++) {
        const fp = this.flowPulses[i];
        const tr = fp.track;
        const dist = ((fp.phase + (nowSec * tr.speedKmS) / tr.totalLen) % 1.0) * tr.totalLen;
        this._sampleTrackAtDist(tr.pts3d, tr.cumLen, dist, fp.mesh.position, this._tmpDir);
        if (this._tmpDir.lengthSq() > 1e-8) {
          fp.mesh.quaternion.setFromUnitVectors(this._upVec, this._tmpDir);
        }
      }
    }

    // Animate opt-in Hypothetical Storm-Wash Lead Fate packets (silt settling in Long Creek -> 65x Skunk dilution -> 1000x Mississippi fade)
    if (this.runoffGroup.visible && this.runoffTrack && this.runoffParticles.length) {
      const tr = this.runoffTrack;
      const dLc = tr.dLongCreekEnd;
      const dSk = tr.dSkunkEnd;
      // Scale intensity gently with chosen wash-off scenario (0.01% -> 0.72x, 0.1% -> 1.0x, 1.0% -> 1.32x)
      const pctGain = THREE.MathUtils.clamp(1.0 + Math.log10(Math.max(0.005, this.runoffPct) / 0.1) * 0.30, 0.65, 1.45);
      for (let i = 0; i < this.runoffParticles.length; i++) {
        const rp = this.runoffParticles[i];
        const frac = (rp.phase + (nowSec * rp.speedKmS) / Math.max(4.0, rp.maxDist)) % 1.0;
        const dist = frac * rp.maxDist;
        this._sampleTrackAtDist(tr.pts3d, tr.cumLen, dist, this._tmpVec, this._tmpDir);

        // Compute perpendicular horizontal spread (tight in Long Creek, widens gently as diluted in Skunk/Mississippi)
        const spreadMul = dist < dLc ? 0.45 : (dist < dSk ? 1.15 : 1.75);
        const px = -this._tmpDir.z * rp.lateralOffset * spreadMul;
        const pz = this._tmpDir.x * rp.lateralOffset * spreadMul;
        // Settling silt sinks slightly downward onto creek bed near end of its run
        const sinkY = rp.settlesInCreek ? -0.14 * Math.pow(frac, 2.0) : 0.05;
        rp.mesh.position.set(this._tmpVec.x + px, this._tmpVec.y + sinkY, this._tmpVec.z + pz);

        if (rp.isDissolved) {
          // Violet dissolved Pb (~10%): fades smoothly across 65x Skunk and 1,000x Mississippi dilution
          const stageOp = dist < dLc ? 0.92 : (dist < dSk ? 0.48 : 0.22);
          const stageSc = dist < dLc ? 0.82 : (dist < dSk ? 0.52 : 0.30);
          rp.mat.color.setHex(dist < dSk ? 0xc084fc : 0x818cf8);
          rp.mat.opacity = THREE.MathUtils.clamp(stageOp * pctGain, 0.12, 0.96);
          rp.mesh.scale.setScalar(stageSc * Math.sqrt(pctGain));
        } else if (rp.settlesInCreek) {
          // Heavy silt-bound Pb settling onto Long Creek streambed
          const fadeOut = frac > 0.78 ? (1.0 - (frac - 0.78) / 0.22) : 1.0;
          rp.mat.color.setHex(frac > 0.55 ? 0xd97706 : 0xfbbf24);
          rp.mat.opacity = THREE.MathUtils.clamp(0.92 * fadeOut * pctGain, 0.05, 0.96);
          rp.mesh.scale.setScalar((1.05 - 0.35 * frac) * Math.sqrt(pctGain));
        } else {
          // Suspended silt-bound Pb entering Skunk (65x dilution) -> Mississippi (1000x dilution)
          if (dist < dLc) {
            rp.mat.color.setHex(0xf59e0b);
            rp.mat.opacity = THREE.MathUtils.clamp(0.92 * pctGain, 0.2, 0.96);
            rp.mesh.scale.setScalar(1.0 * Math.sqrt(pctGain));
          } else if (dist < dSk) {
            const tSk = (dist - dLc) / Math.max(1.0, dSk - dLc);
            this._tmpCol.setHex(0xf59e0b).lerp(this._colDilute, 0.45 + 0.40 * tSk);
            rp.mat.color.copy(this._tmpCol);
            rp.mat.opacity = THREE.MathUtils.clamp((0.55 - 0.22 * tSk) * pctGain, 0.15, 0.85);
            rp.mesh.scale.setScalar((0.62 - 0.15 * tSk) * Math.sqrt(pctGain));
          } else {
            const tMs = Math.min(1.0, (dist - dSk) / 22.0);
            rp.mat.color.setHex(0x60a5fa);
            rp.mat.opacity = THREE.MathUtils.clamp((0.28 - 0.16 * tMs) * pctGain, 0.08, 0.45);
            rp.mesh.scale.setScalar(0.32 * Math.sqrt(pctGain));
          }
        }
      }
    }

    if (this.cameraPosGoal && this.cameraTargetGoal) {
      this.camera.position.lerp(this.cameraPosGoal, 0.08);
      this.controls.target.lerp(this.cameraTargetGoal, 0.08);
      if (this.camera.position.distanceTo(this.cameraPosGoal) < 0.08) {
        this.cameraPosGoal = null;
        this.cameraTargetGoal = null;
      }
    }

    this.controls.update();

    // Distance-adaptive zoom scaling for all 3D title/name popups (compact on close zoom, readable from afar)
    for (let i = 0; i < this.allLabels.length; i++) {
      const sp = this.allLabels[i];
      if (!sp.visible || (sp.parent && !sp.parent.visible)) continue;
      sp.getWorldPosition(this._tmpVec);
      const dist = this.camera.position.distanceTo(this._tmpVec);
      const h = THREE.MathUtils.clamp(dist * 0.022, 0.08, 2.15) * (sp.userData.labelSizeMul || 1.0);
      sp.scale.set(h * sp.userData.labelAspect, h, 1);
    }

    this.renderer.render(this.scene, this.camera);
  }
}
