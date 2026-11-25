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
    this.maxSec = 14400.0;
    this.colorMode = 'regime'; // 'regime' | 'altitude'
    this.plumeMode = 'assimilated'; // 'assimilated' | 'forecast'
    this.pickables = [];
    this.xactVisuals = {};
    this.windVisuals = [];
    this.latestXactObsSec = 6600.0;
    this.windObsCutoffSec = 6600.0;
    this.assimShiftInfo = null;
    this.sensorGroup = new THREE.Group();
    this.windGroup = new THREE.Group();
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
    this.scene.fog = new THREE.FogExp2(0x060a10, 0.0042);

    const w = this.canvas.clientWidth || 1200;
    const h = this.canvas.clientHeight || 700;
    this.camera = new THREE.PerspectiveCamera(46, w / h, 0.05, 400);
    this.camera.position.set(22, 22, 31);

    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, powerPreference: 'high-performance' });
    this.renderer.setSize(w, h, false);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;
    this.controls.maxPolarAngle = Math.PI * 0.492;
    this.controls.minDistance = 0.8;
    this.controls.maxDistance = 135;
    this.controls.target.set(-14, 1.5, 11);
    this.controls.update();

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.clock = new THREE.Clock();

    this.scene.add(this.sensorGroup, this.windGroup, this.sightlineGroup, this.lidGroup, this.labelGroup);
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
    const vs = new Float32Array(N);
    const pStick = new Float32Array(N);
    const x = new Float32Array(N);
    const y = new Float32Array(N);
    const z = new Float32Array(N);
    const wBuoy = new Float32Array(N);
    const cdArr = new Float32Array(N);
    const sdArr = new Float32Array(N);
    const spdArr = new Float32Array(N);
    const active = new Uint8Array(N);

    const bSin = Math.sin((248.0 * Math.PI) / 180.0);
    const bCos = Math.cos((248.0 * Math.PI) / 180.0);
    for (let i = 0; i < N; i++) {
      active[i] = 1;
      const r = i < N_C ? 0 : (i < N_C + N_10 ? 1 : 2);
      regimes[i] = r;
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
      const bunker = rand() < 0.35 ? -120.0 : 120.0;
      x[i] = bunker * bSin + randn() * 45.0;
      y[i] = bunker * bCos + randn() * 45.0;
      z[i] = 5.0 + rand() * 30.0;
      let wb = bunker < 0 ? (0.6 + rand() * 1.4) : (1.5 + rand() * 2.7);
      if (r === 0) wb *= 0.65;
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
    const uProfT = new Float32Array(nZ);
    const vProfT = new Float32Array(nZ);

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
    while (tCur < 14400) {
      const dt = tCur < 120 ? 5.0 : 30.0;
      tCur += dt;
      const tMin = tCur / 60.0;
      const idxLo = Math.min(Math.floor(tMin / 10.0), nT - 2);
      const tf = Math.min(1.0, (tMin - tMinArr[idxLo]) / 10.0);
      for (let k = 0; k < nZ; k++) {
        uProfT[k] = uAss[idxLo][k] * (1.0 - tf) + uAss[idxLo + 1][k] * tf;
        vProfT[k] = vAss[idxLo][k] * (1.0 - tf) + vAss[idxLo + 1][k] * tf;
      }
      const pblC = Math.min(1075.0, Math.max(285.0, pblArr[idxLo] * (1.0 - tf) + pblArr[idxLo + 1] * tf));
      const conv = Math.min(1.0, (tCur / 3600.0) / 2.0);
      const sigH = Math.sqrt(2.0 * (16.0 + 36.0 * conv) * dt);
      const kzBase = 3.2 + 14.0 * conv;
      const buoyDecay = tCur <= 180 ? Math.exp(-tCur / 45.0) : 0.0;

      for (let i = 0; i < N; i++) {
        if (!active[i]) continue;
        const za = z[i];
        const um = interpZ(za, uProfT);
        const vm = interpZ(za, vProfT);
        const ur = spdArr[i] * (um * cdArr[i] + vm * sdArr[i]);
        const vr = spdArr[i] * (-um * sdArr[i] + vm * cdArr[i]);
        const kz = kzBase * Math.max(0.15, Math.min(1.0, 1.0 - 0.7 * (za / 1075.0) ** 2));
        x[i] += ur * dt + randn() * sigH;
        y[i] += vr * dt + randn() * sigH;
        let zn = za + (wBuoy[i] * buoyDecay - vs[i]) * dt + randn() * Math.sqrt(2.0 * kz * dt);
        if (zn > pblC) zn = Math.max(-50.0, Math.min(1075.0, pblC - (zn - pblC) * 0.85));
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
      if (kfSet.has(tCur)) snap(pblC);
    }

    // Measure airborne fine-plume centroid shift vs. initial 15Z forecast at T+120m (frame index for 7200s)
    const idx2h = kfSec.indexOf(7200);
    if (idx2h >= 0 && this.forecastPlume.frames[idx2h]) {
      const fA = frames[idx2h], fF = this.forecastPlume.frames[idx2h];
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

    // Subtle metric coordinate grid around ground
    const grid = new THREE.GridHelper(110, 22, 0x1e2d42, 0x121c2a);
    grid.position.set(cx, -0.8, cz);
    this.scene.add(grid);
  }

  _makeSpriteLabel(text, color = '#ffffff', bg = 'rgba(8,12,18,0.82)', fontSize = 26) {
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
    sp.scale.set(1.22 * aspect, 1.22, 1);
    sp.renderOrder = 10;
    return sp;
  }

  _makeDynamicSprite(initialText, color = '#6ee7b7', bg = 'rgba(6,22,16,0.92)') {
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
    sp.scale.set(1.32 * aspect, 1.32, 1);
    sp.renderOrder = 14;
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

    // 3. Stereo Origin Beacon + Dual-Camera 95% Error Ellipse (340m x 220m)
    const originSphere = new THREE.Mesh(
      new THREE.SphereGeometry(0.28, 20, 20),
      new THREE.MeshBasicMaterial({ color: 0xff2244 })
    );
    originSphere.position.set(0, 0.25, 0);
    originSphere.userData = {
      type: 'origin',
      title: 'Stereo-Triangulated Detonation Zone',
      body: `<div style="margin-bottom:5px"><span class="tag-live">Video-Measured</span></div>` +
            `<b>Coordinates:</b> ${s.origin.lat.toFixed(4)}°N, ${s.origin.lon.toFixed(4)}°W (±250 m)<br>` +
            `<b>Stereo Baseline:</b> 46.3° (KWQC + KCRG/AP)<br>` +
            `<b>Dual-Bunker Separation:</b> ~239 m (176–320 m)<br>` +
            `<b>Event Time:</b> 2026-11-25 15:00 UTC (9:00 AM CST)<br>` +
            `<b>Inventory:</b> ~9,500 lb lead azide (~3,050 kg elemental Pb)`
    };
    this.sightlineGroup.add(originSphere);
    this.pickables.push(originSphere);

    const ellPts = [];
    const rot = (135 * Math.PI) / 180;
    for (let i = 0; i <= 64; i++) {
      const th = (i / 64) * Math.PI * 2;
      const dN = 0.34 * Math.cos(th) * Math.cos(rot) - 0.22 * Math.sin(th) * Math.sin(rot);
      const dE = 0.34 * Math.cos(th) * Math.sin(rot) + 0.22 * Math.sin(th) * Math.cos(rot);
      ellPts.push(new THREE.Vector3(dE, 0.14, -dN));
    }
    this.sightlineGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(ellPts), new THREE.LineBasicMaterial({ color: 0xffff00 })));

    // 4. Camera 1 (KWQC), FAA Tower T3, Camera 2 (KCRG/AP) + 3D Sightlines!
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

      const lbl = this._makeSpriteLabel(name, '#' + color.toString(16).padStart(6, '0'), 'rgba(8,12,18,0.88)', 22);
      lbl.position.set(x, 1.35, z);
      this.sightlineGroup.add(lbl);
    };

    addCam(s.cam1_kwqc, 0x00e5ff, 'Cam 1: KWQC (190th Ave)',
      `<b>Site:</b> ${s.cam1_kwqc.site}<br><b>Bearing:</b> ${s.cam1_kwqc.bearing_deg}° SSE across FAA Tower T3<br>` +
      `<b>Acoustic Range:</b> 6.23 ± 0.5 km (boom @ Δt=18.8s)<br><b>Tracked Plumes:</b> P1 4.9 m/s, P2 7.2 m/s (R²=0.999)`);

    addCam(s.cam2_kcrg, 0xff8833, 'Cam 2: KCRG/AP (9856 202nd)',
      `<b>Site:</b> ${s.cam2_kcrg.site}<br><b>Bearing:</b> ${s.cam2_kcrg.bearing_deg}° ESE | Range: ${s.cam2_kcrg.dist_km} km<br>` +
      `<b>Tracked Upper Puff:</b> ${s.cam2_kcrg.v_true_ms} m/s (R²=${s.cam2_kcrg.r2}), rising ${s.cam2_kcrg.h_top_t35_m}→${s.cam2_kcrg.h_top_t51_m} m AGL`);

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
    const t3Lbl = this._makeSpriteLabel('Tower T3 (235 ft)', '#ff88ff', 'rgba(8,12,18,0.85)', 20);
    t3Lbl.position.set(t3x, 1.4, t3z);
    this.sightlineGroup.add(t3Lbl);
    this.towerT3Label = t3Lbl;

    // 5. Key Towns 3D Labels
    const showTowns = new Set(['Middletown, IA', 'Danville, IA', 'Burlington, IA', 'Augusta, IA', 'Denmark, IA',
                               'West Point, IA', 'Fort Madison, IA', 'Donnellson, IA', 'Farmington, IA', 'Kahoka, MO', 'Keokuk, IA']);
    for (const t of s.towns) {
      if (!showTowns.has(t.town)) continue;
      const short = t.town.replace(', IA', '').replace(', MO', ' (MO)');
      const sp = this._makeSpriteLabel(short, '#e2e8f0', 'rgba(10,15,24,0.72)', 20);
      sp.position.set(t.x_km, 0.9, -t.y_km);
      sp.scale.multiplyScalar(0.82);
      this.labelGroup.add(sp);
    }
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
    const W = 76, H = 60, cx = -26, cz = 16;
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
    // Clear existing dynamic sensors & wind arrows
    while (this.sensorGroup.children.length) this.sensorGroup.remove(this.sensorGroup.children[0]);
    while (this.windGroup.children.length) this.windGroup.remove(this.windGroup.children[0]);
    // Keep only static pickables
    this.pickables = this.pickables.filter((m) => ['origin', 'camera', 'tower'].includes(m.userData.type));
    this.xactVisuals = {};
    this.windVisuals = [];

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

        const dynLabel = this._makeDynamicSprite(`● ${key}: ${(xact.latest_value ?? 0).toFixed(4)} µg/m³`);
        dynLabel.sprite.position.set(x, baseY + 2.30, z);
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

    this._updateXactVisualsAtTime(this.simSec);
    this._updateWindVisualsAtTime(this.simSec);
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
    // Scale Tower T3 (71.6 m = 0.0716 km)
    if (this.towerT3Mesh) {
      const hT3 = 0.0716 * exag;
      const baseT3 = this.terrainY(this.staticData.tower_t3.x_km, -this.staticData.tower_t3.y_km);
      this.towerT3Mesh.scale.set(1, hT3, 1);
      this.towerT3Mesh.position.y = baseT3 + hT3 / 2;
      this.towerT3Label.position.y = baseT3 + hT3 + 0.65;
    }
    // Update inversion cap height (1.075 km)
    if (this.invMesh) {
      const yInv = 1.075 * exag;
      this.invMesh.position.y = yInv;
      this.invLbl.position.y = yInv + 0.6;
    }
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

    // Time-sync 3D live EPA Xact towers & ASOS/RWIS wind arrows with the playback clock!
    const xactSnap = this._updateXactVisualsAtTime(clamped);
    this._updateWindVisualsAtTime(clamped);

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
      this.cameraTargetGoal = new THREE.Vector3(0, 0.22 * this.vertExag, 0);
      this.simSec = 45; this.updateParticlesAtTime(this.simSec);
    } else if (preset === 'shear') {
      // Dramatic side cross-section showing vertical wind shear & inversion cap!
      this.cameraPosGoal = new THREE.Vector3(-8, 6.5, 44);
      this.cameraTargetGoal = new THREE.Vector3(-22, 4.5, 12);
    } else if (preset === 'top') {
      this.cameraPosGoal = new THREE.Vector3(-22, 74, 15.5);
      this.cameraTargetGoal = new THREE.Vector3(-22, 0, 15);
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

    if (this.cameraPosGoal && this.cameraTargetGoal) {
      this.camera.position.lerp(this.cameraPosGoal, 0.08);
      this.controls.target.lerp(this.cameraTargetGoal, 0.08);
      if (this.camera.position.distanceTo(this.cameraPosGoal) < 0.08) {
        this.cameraPosGoal = null;
        this.cameraTargetGoal = null;
      }
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
