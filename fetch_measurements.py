#!/usr/bin/env python3
"""
Auto-updating live measurement fetcher & inverse-bound recomputer for the
Nov 25, 2026 IAAAP Controlled Detonation OSINT Analysis.

Pulls from all surrounding open measurement networks:
  1. EPA Region 7 Live Xact Continuous Lead Telemetry (AS01-AS04+)
  2. EPA Region 7 Air Sampling & Monitoring Station Registry (43+ stations)
  3. Regional NWS / IEM ASOS Airport Weather Stations (10 stations)
  4. Iowa DOT RWIS Road Weather Stations along/near plume corridor (6 stations)
  5. Open-Meteo Vertical Wind/PBL Profile + Regional Air Quality (PM2.5/PM10/AQI)
  6. KDVN Upper-Air Radiosonde Soundings (12Z / 00Z)

Uses pure Python 3 standard library so it runs identically in local systemd/cron
daemons and GitHub Actions without external dependencies.
"""
import argparse
import csv
import datetime
import gzip
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SITE_DATA_DIR = BASE_DIR / "site_data"
LATEST_FILE = DATA_DIR / "latest_measurements.json"
LIVE_BUNDLE_FILE = SITE_DATA_DIR / "live_telemetry.json"
LOG_FILE = DATA_DIR / "updater.log"

ORIGIN_LAT = 40.8099
ORIGIN_LON = -91.2668
TOTAL_PB_KG = 3050.0
USER_AGENT = "IAAAP-OSINT-Plume-Monitor/1.0 (https://github.com/mike10010100/iaaap-plume-analysis)"

ASOS_STATIONS = ["BRL", "MPZ", "FSW", "EOK", "FFL", "AWG", "MQB", "GBG", "DVN", "IRK"]
RWIS_STATIONS = {"RAGI4", "RARI4", "RBUI4", "RCLI4", "RMPI4", "ROUI4"}
AQ_POINTS = [
    ("IAAAP (Middletown)", 40.810, -91.267),
    ("Denmark, IA",        40.737, -91.334),
    ("West Point, IA",     40.717, -91.454),
    ("Donnellson, IA",     40.641, -91.560),
    ("Kahoka, MO",         40.422, -91.722),
]


def http_get_json(url: str, timeout: int = 25):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def http_get_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def dist_brg_km(lat1, lon1, lat2, lon2):
    dN = (lat2 - lat1) * 111.132
    dE = (lon2 - lon1) * 111.320 * math.cos(math.radians(0.5 * (lat1 + lat2)))
    return round(math.hypot(dE, dN), 2), round((math.degrees(math.atan2(dE, dN)) + 360.0) % 360.0, 1)


def fetch_epa_xact():
    url = (
        "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
        "IAAAP_Xact_Data_Public_Viewer/FeatureServer/0/query?"
        "where=1%3D1&orderByFields=Mon_Date_Time+DESC&outFields=*&returnGeometry=true"
        "&outSR=4326&resultRecordCount=1000&f=json"
    )
    data = http_get_json(url)
    by_stn = {}
    for ft in data.get("features", []):
        a = ft.get("attributes") or {}
        sid = str(a.get("Air_Station_ID") or "UNK").strip()
        ts_ms = a.get("Mon_Date_Time")
        if ts_ms is None:
            continue
        dt_utc = datetime.datetime.fromtimestamp(ts_ms / 1000.0, datetime.timezone.utc)
        val = float(a.get("Mon_Results") or 0.0)
        lat = float(a.get("Latitude") or (ft.get("geometry") or {}).get("y") or 0.0)
        lon = float(a.get("Longitude") or (ft.get("geometry") or {}).get("x") or 0.0)
        by_stn.setdefault(sid, {"station_id": sid, "lat": lat, "lon": lon, "units": a.get("Mon_Units") or "µg/m3", "records": []})
        by_stn[sid]["records"].append({
            "ts_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "min_post_blast": round((dt_utc - datetime.datetime(2026, 11, 25, 15, 0, 0, tzinfo=datetime.timezone.utc)).total_seconds() / 60.0, 1),
            "value": val,
        })

    # Load modeled receptor peaks (if present) to dynamically refresh inverse bounds!
    model_peaks = {}
    si_path = DATA_DIR / "sensitivity_and_inverse.json"
    if si_path.exists():
        try:
            si = json.loads(si_path.read_text(encoding="utf-8"))
            for k, v in (si.get("receptors") or {}).items():
                model_peaks[k] = v
        except Exception:
            pass

    out = {}
    for sid, info in sorted(by_stn.items()):
        # Deduplicate by timestamp & sort ascending
        dedup = {r["ts_utc"]: r for r in info["records"]}
        recs = sorted(dedup.values(), key=lambda r: r["ts_utc"])
        pre = [r for r in recs if r["min_post_blast"] < 0]
        post = [r for r in recs if r["min_post_blast"] >= 0]
        pre_max = max((r["value"] for r in pre), default=0.0)
        post_max = max((r["value"] for r in post), default=0.0)
        latest = recs[-1] if recs else None
        dkm, brg = dist_brg_km(ORIGIN_LAT, ORIGIN_LON, info["lat"], info["lon"])
        mp = model_peaks.get(sid, {})
        med_peak = mp.get("peak_C_over_Q_med") or 0.0
        c_ceil = max(post_max, 0.005)
        q_max_kg = round(c_ceil / (med_peak * 1e9), 4) if med_peak > 0 else None
        f_max_pct = round(q_max_kg / TOTAL_PB_KG * 100.0, 5) if q_max_kg is not None else None

        out[sid] = {
            "station_id": sid,
            "lat": round(info["lat"], 6),
            "lon": round(info["lon"], 6),
            "dist_km": dkm,
            "bearing_deg": brg,
            "units": info["units"],
            "obs_count": len(recs),
            "latest_ts_utc": latest["ts_utc"] if latest else None,
            "latest_value": latest["value"] if latest else None,
            "pre_blast_max": pre_max,
            "post_blast_max": post_max,
            "nonzero_post": [r for r in post if r["value"] > 0],
            "hit_prob_pct": mp.get("hit_prob_pct"),
            "modeled_peak_min": mp.get("peak_time_min"),
            "q_max_kg": q_max_kg,
            "f_max_pct": f_max_pct,
            "series": recs,
        }
    return out


def fetch_epa_monitors():
    url = (
        "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
        "Air_Sampling_and_Monitoring_Location/FeatureServer/0/query?"
        "where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=json"
    )
    data = http_get_json(url)
    stns = []
    for ft in data.get("features", []):
        a = ft.get("attributes") or {}
        g = ft.get("geometry") or {}
        if g.get("y") is None or g.get("x") is None:
            continue
        dkm, brg = dist_brg_km(ORIGIN_LAT, ORIGIN_LON, g["y"], g["x"])
        stns.append({
            "station_id": a.get("Station_ID"),
            "instrument_type": a.get("Instrument_Type"),
            "lat": round(float(g["y"]), 5),
            "lon": round(float(g["x"]), 5),
            "dist_km": dkm,
            "bearing_deg": brg,
        })
    stns.sort(key=lambda s: (s["dist_km"], str(s["station_id"])))
    return stns


def fetch_asos_latest():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    start_dt = datetime.datetime(2026, 11, 25, 13, 0, 0, tzinfo=datetime.timezone.utc)
    # Cap query to today + tomorrow if clock advances past Nov 25
    stn_params = "&".join(f"station={s}" for s in ASOS_STATIONS)
    url = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?{stn_params}"
        "&data=tmpc&data=dwpc&data=drct&data=sknt&data=gust&data=alti&data=skyc1&data=metar"
        f"&year1={start_dt.year}&month1={start_dt.month}&day1={start_dt.day}"
        f"&year2={(now_utc + datetime.timedelta(days=1)).year}&month2={(now_utc + datetime.timedelta(days=1)).month}&day2={(now_utc + datetime.timedelta(days=1)).day}"
        "&tz=Etc%2FUTC&format=onlycomma&latlon=yes"
    )
    txt = http_get_text(url, timeout=30)
    by_stn = {}
    for row in csv.DictReader(io.StringIO(txt)):
        s = row.get("station")
        if not s:
            continue
        valid = row.get("valid", "")
        if valid < "2026-11-25 14:30":
            continue
        def fnum(val):
            try:
                return float(val) if val not in (None, "", "M") else None
            except ValueError:
                return None
        drct = fnum(row.get("drct"))
        sknt = fnum(row.get("sknt"))
        rec = {
            "valid_utc": valid.replace(" ", "T") + ":00Z",
            "temp_c": fnum(row.get("tmpc")),
            "dewpoint_c": fnum(row.get("dwpc")),
            "wind_from_deg": drct,
            "wind_to_deg": round((drct + 180.0) % 360.0, 1) if drct is not None else None,
            "wind_kt": sknt,
            "wind_ms": round(sknt * 0.514444, 2) if sknt is not None else None,
            "gust_kt": fnum(row.get("gust")),
            "sky": row.get("skyc1") if row.get("skyc1") != "M" else None,
            "metar": row.get("metar"),
        }
        entry = by_stn.setdefault(s, {
            "station": f"K{s}",
            "lat": round(float(row["lat"]), 4),
            "lon": round(float(row["lon"]), 4),
            "history": [],
        })
        entry["history"].append(rec)

    out = {}
    for s, info in sorted(by_stn.items()):
        # Keep latest valid wind obs + recent 24 observations
        hist = info["history"]
        latest_wind = next((r for r in reversed(hist) if r["wind_from_deg"] is not None and r["wind_ms"] is not None), hist[-1] if hist else None)
        dkm, brg = dist_brg_km(ORIGIN_LAT, ORIGIN_LON, info["lat"], info["lon"])
        out[f"K{s}"] = {
            "station": f"K{s}",
            "lat": info["lat"],
            "lon": info["lon"],
            "dist_km": dkm,
            "bearing_deg": brg,
            "latest": latest_wind,
            "recent_obs_count": len(hist),
            "recent": hist[-16:],
        }
    return out


def fetch_rwis_latest():
    url = "https://mesonet.agron.iastate.edu/api/1/currents.json?network=IA_RWIS"
    data = http_get_json(url)
    out = {}
    for row in data.get("data", []):
        sid = row.get("station")
        if sid not in RWIS_STATIONS:
            continue
        lat, lon = float(row.get("lat") or 0), float(row.get("lon") or 0)
        dkm, brg = dist_brg_km(ORIGIN_LAT, ORIGIN_LON, lat, lon)
        sknt = row.get("sknt")
        drct = row.get("drct")
        out[sid] = {
            "station": sid,
            "name": row.get("name"),
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "dist_km": dkm,
            "bearing_deg": brg,
            "valid_utc": row.get("utc_valid"),
            "temp_f": row.get("tmpf"),
            "wind_from_deg": drct,
            "wind_to_deg": round((float(drct) + 180.0) % 360.0, 1) if drct is not None else None,
            "wind_ms": round(float(sknt) * 0.514444, 2) if sknt is not None else None,
            "gust_kt": row.get("gust"),
        }
    return out


def fetch_openmeteo_current():
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    start_d = "2026-11-25"
    url_wx = (
        f"https://api.open-meteo.com/v1/forecast?latitude={ORIGIN_LAT}&longitude={ORIGIN_LON}"
        "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
        "wind_speed_80m,wind_direction_80m,wind_speed_120m,wind_direction_120m,wind_speed_180m,wind_direction_180m,"
        "boundary_layer_height,wind_speed_950hPa,wind_direction_950hPa,wind_speed_925hPa,wind_direction_925hPa"
        f"&wind_speed_unit=ms&timezone=UTC&start_date={start_d}&end_date={max(start_d, today)}"
    )
    wx = http_get_json(url_wx)
    # Also fetch Open-Meteo regional Air Quality (PM2.5, PM10, US AQI) at 5 corridor points
    lats = ",".join(str(p[1]) for p in AQ_POINTS)
    lons = ",".join(str(p[2]) for p in AQ_POINTS)
    url_aq = (
        f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lats}&longitude={lons}"
        f"&hourly=pm10,pm2_5,us_aqi&timezone=UTC&start_date={start_d}&end_date={max(start_d, today)}"
    )
    aq_raw = http_get_json(url_aq)
    if isinstance(aq_raw, dict):
        aq_raw = [aq_raw]
    now_hr = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:00")
    aq_out = []
    for (name, lat, lon), blk in zip(AQ_POINTS, aq_raw):
        h = blk.get("hourly") or {}
        times = h.get("time") or []
        idx = times.index(now_hr) if now_hr in times else max(0, len(times) - 1)
        aq_out.append({
            "location": name, "lat": lat, "lon": lon,
            "hour_utc": times[idx] if times else None,
            "pm2_5": (h.get("pm2_5") or [None])[idx],
            "pm10": (h.get("pm10") or [None])[idx],
            "us_aqi": (h.get("us_aqi") or [None])[idx],
        })
    return {"iaaap_hourly": wx.get("hourly"), "corridor_aq": aq_out}


def log_line(msg: str):
    print(msg, flush=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def strip_timestamps_for_diff(obj: dict) -> dict:
    c = json.loads(json.dumps(obj))
    c.pop("fetched_at_utc", None)
    c.pop("source_errors", None)
    return c


def run_fetch(push: bool = False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    iso_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_slug = now_utc.strftime("%Y%m%d_%H%M%S")

    errors = []
    def safe(name, fn, default):
        try:
            return fn()
        except Exception as e:
            errors.append(f"{name}: {e}")
            return default

    xact = safe("epa_xact", fetch_epa_xact, {})
    monitors = safe("epa_monitors", fetch_epa_monitors, [])
    asos = safe("iem_asos", fetch_asos_latest, {})
    rwis = safe("iem_rwis", fetch_rwis_latest, {})
    om = safe("openmeteo", fetch_openmeteo_current, {})

    # Compute high-level live summary for instant dashboard header KPIs
    xact_max_post = max((s["post_blast_max"] for s in xact.values()), default=0.0)
    xact_latest_ts = max((s["latest_ts_utc"] for s in xact.values() if s.get("latest_ts_utc")), default=None)
    kbrl = asos.get("KBRL", {}).get("latest") or {}
    # Conservative mixed-PBL inverse bound from AS02 (if available)
    as02_fmax = (xact.get("AS02") or {}).get("f_max_pct")
    as03_fmax = (xact.get("AS03") or {}).get("f_max_pct")

    bundle = {
        "fetched_at_utc": iso_utc,
        "event_time_utc": "2026-11-25T15:00:00Z",
        "origin": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
        "summary": {
            "xact_stations_active": len(xact),
            "epa_monitors_deployed": len(monitors),
            "xact_latest_obs_utc": xact_latest_ts,
            "xact_max_post_ugm3": xact_max_post,
            "as02_inverse_bound_pct": as02_fmax,
            "as03_inverse_bound_pct": as03_fmax,
            "kbrl_wind_from_deg": kbrl.get("wind_from_deg"),
            "kbrl_wind_to_deg": kbrl.get("wind_to_deg"),
            "kbrl_wind_ms": kbrl.get("wind_ms"),
            "kbrl_valid_utc": kbrl.get("valid_utc"),
            "asos_stations_live": len(asos),
            "rwis_stations_live": len(rwis),
        },
        "epa_xact": xact,
        "epa_monitors": monitors,
        "asos": asos,
        "rwis": rwis,
        "openmeteo_aq": om.get("corridor_aq", []),
        "source_errors": errors,
    }

    # Check whether actual measurements changed vs previous live_telemetry.json
    changed = True
    if LIVE_BUNDLE_FILE.exists():
        try:
            prev = json.loads(LIVE_BUNDLE_FILE.read_text(encoding="utf-8"))
            changed = strip_timestamps_for_diff(prev) != strip_timestamps_for_diff(bundle)
        except Exception:
            changed = True

    # Atomic write: always update local LATEST_FILE heartbeat; only overwrite git-tracked
    # site_data/live_telemetry.json when measurements actually changed (or file missing)
    # so neither local --push nor GitHub Actions spams timestamp-only commits.
    targets = [LATEST_FILE]
    if changed or not LIVE_BUNDLE_FILE.exists():
        targets.append(LIVE_BUNDLE_FILE)
    for target in targets:
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(bundle, f, separators=(",", ":"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)

    snap_note = "unchanged (skipped snapshot)"
    if changed:
        snap_path = SNAPSHOT_DIR / f"telemetry_{ts_slug}.json.gz"
        with gzip.open(snap_path, "wt", encoding="utf-8") as gz:
            json.dump(bundle, gz, separators=(",", ":"))
        snap_note = snap_path.name

    log_line(
        f"[{iso_utc}] Xact={len(xact)} stns (latest={xact_latest_ts}, max={xact_max_post:.4f} ug/m3) | "
        f"EPA={len(monitors)} | ASOS={len(asos)} (KBRL {kbrl.get('wind_from_deg')}deg@{kbrl.get('wind_ms')}m/s) | "
        f"RWIS={len(rwis)} | {snap_note}"
        + (f" | errors={errors}" if errors else "")
    )

    if push and changed:
        try:
            env = dict(os.environ, PATH=f"/home/mike10010100/.gemini/antigravity-cli/bin:/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:{os.environ.get('PATH', '/usr/bin:/bin')}")
            subprocess.run(["git", "-C", str(BASE_DIR), "add", "site_data/live_telemetry.json"], env=env, check=False)
            diff = subprocess.run(["git", "-C", str(BASE_DIR), "diff", "--cached", "--quiet"], env=env, check=False)
            if diff.returncode != 0:
                subprocess.run(
                    ["git", "-C", str(BASE_DIR), "commit", "-m", f"chore(data): auto-update live telemetry ({iso_utc})"],
                    env=env, check=False,
                )
                subprocess.run(["git", "-C", str(BASE_DIR), "pull", "--rebase", "--autostash", "origin", "main"], env=env, check=False)
                subprocess.run(["git", "-C", str(BASE_DIR), "push", "origin", "main"], env=env, check=False)
                log_line(f"[{iso_utc}] Pushed live_telemetry.json update to origin/main")
        except Exception as e:
            log_line(f"[{iso_utc}] Warning: git push skipped: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch live IAAAP surrounding measurements")
    parser.add_argument("--push", action="store_true", help="Auto-commit & push site_data/live_telemetry.json if measurements changed")
    args = parser.parse_args()
    run_fetch(push=args.push)
