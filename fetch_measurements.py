#!/usr/bin/env python3
"""
Auto-updating live measurement fetcher, 24h+ multi-detector time-series archiver,
live wind-assimilation engine, and inverse-bound recomputer for the
Nov 25, 2026 IAAAP Controlled Detonation OSINT Analysis.

Pulls & archives full rolling 24h+ time series from all surrounding open networks:
  1. EPA Region 7 Live Xact Continuous Lead Telemetry (AS01-AS04+, paginated)
  2. EPA Region 7 Air Sampling & Monitoring Station Registry (43+ stations)
  3. Regional NWS / IEM ASOS Airport Weather Stations (10 stations, 24h+ METAR series)
  4. Iowa DOT RWIS Road Weather Stations along/near plume corridor (6 stations, 24h+ 10-min series)
  5. Open-Meteo Vertical Wind/PBL Profile + Regional Air Quality (PM2.5/PM10/AQI, 24h+ hourly)

Also computes a live inverse-distance-weighted surface-to-PBL wind assimilation table
(`wind_assimilation`) blending observed ASOS + RWIS winds up to the latest real-world
observation (`observed_reanalysis`) with forecast winds beyond (`forecast_projection`) so
the 3D WebGL observatory dynamically shifts as new measurements flow in without bloating git.

Pure Python 3 standard library — zero external dependencies.
"""
import argparse
import csv
import datetime
import fcntl
import gzip
import io
import json
import math
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SITE_DATA_DIR = BASE_DIR / "site_data"
LATEST_FILE = DATA_DIR / "latest_measurements.json"
ARCHIVE_FILE = DATA_DIR / "timeseries_archive.json.gz"
LIVE_BUNDLE_FILE = SITE_DATA_DIR / "live_telemetry.json"
LOG_FILE = DATA_DIR / "updater.log"

EVENT_DT = datetime.datetime(2026, 11, 25, 15, 0, 0, tzinfo=datetime.timezone.utc)
ORIGIN_LAT = 40.8090
ORIGIN_LON = -91.2657
ELEV_M = 215.0
TOTAL_PB_KG = 3050.0
USER_AGENT = "IAAAP-OSINT-Plume-Monitor/2.0 (https://github.com/mike10010100/iaaap-plume-analysis)"

ASOS_STATIONS = ["BRL", "MPZ", "FSW", "EOK", "FFL", "AWG", "MQB", "GBG", "DVN", "IRK"]
RWIS_STATIONS = {"RAGI4", "RARI4", "RBUI4", "RCLI4", "RMPI4", "ROUI4"}
AQ_POINTS = [
    ("IAAAP (Middletown)", 40.809, -91.266),
    ("Denmark, IA",        40.737, -91.334),
    ("West Point, IA",     40.717, -91.454),
    ("Donnellson, IA",     40.641, -91.560),
    ("Kahoka, MO",         40.422, -91.722),
]
SPATIAL_HRRR_NODES = [
    ("IAAAP",      40.8090, -91.2657, 215.0),
    ("Kahoka",     40.4220, -91.7218, 212.0),
    ("Kirksville", 40.1948, -92.5832, 295.0),
    ("Ottumwa",    41.0200, -92.4113, 258.0),
]
Z_GRID_M = [10.0, 80.0, 120.0, 180.0, 250.0, 450.0, 665.0, 880.0, 1330.0]


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


def parse_iso_utc(ts: str) -> datetime.datetime:
    return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def min_post_blast(dt_utc: datetime.datetime) -> float:
    return round((dt_utc - EVENT_DT).total_seconds() / 60.0, 1)


def wind_from_to_uv(from_deg: float, spd_ms: float):
    rad = math.radians(from_deg)
    return -spd_ms * math.sin(rad), -spd_ms * math.cos(rad)


def uv_to_from_spd(u: float, v: float):
    spd = math.hypot(u, v)
    from_deg = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0 if spd > 1e-6 else 0.0
    return round(from_deg, 1), round(spd, 2)


def lin_interp(x: float, xs: list, ys: list) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            dx = xs[i + 1] - xs[i]
            t = (x - xs[i]) / dx if dx > 0 else 0.0
            return ys[i] * (1.0 - t) + ys[i + 1] * t
    return ys[-1]


def load_archive() -> dict:
    if ARCHIVE_FILE.exists():
        try:
            with gzip.open(ARCHIVE_FILE, "rt", encoding="utf-8") as gz:
                return json.load(gz)
        except Exception:
            pass
    return {"epa_xact": {}, "asos": {}, "rwis": {}, "openmeteo_aq": {}, "openmeteo_met": {}}


def save_archive(archive: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".tmp_arch_", suffix=".json.gz")
    os.close(fd)
    with gzip.open(tmp, "wt", encoding="utf-8") as gz:
        json.dump(archive, gz, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, ARCHIVE_FILE)


def fetch_epa_xact(archive: dict):
    """Paginated ArcGIS fetch so multi-day EPA Xact series never truncates at 1,000 rows."""
    base_url = (
        "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
        "IAAAP_Xact_Data_Public_Viewer/FeatureServer/0/query?"
        "where=1%3D1&orderByFields=Mon_Date_Time+ASC&outFields=*&returnGeometry=true"
        "&outSR=4326&f=json"
    )
    by_stn = {}
    # Seed with any archived records so historical points are never lost if EPA prunes viewer
    for sid, prev in (archive.get("epa_xact") or {}).items():
        by_stn[sid] = {
            "station_id": sid,
            "lat": prev.get("lat", 0.0),
            "lon": prev.get("lon", 0.0),
            "units": prev.get("units", "µg/m3"),
            "records": list(prev.get("series") or []),
        }

    offset = 0
    page_size = 1000
    for _ in range(25):  # up to 25,000 records
        data = http_get_json(f"{base_url}&resultOffset={offset}&resultRecordCount={page_size}")
        feats = data.get("features") or []
        for ft in feats:
            a = ft.get("attributes") or {}
            sid = str(a.get("Air_Station_ID") or "UNK").strip()
            ts_ms = a.get("Mon_Date_Time")
            if ts_ms is None:
                continue
            dt_utc = datetime.datetime.fromtimestamp(ts_ms / 1000.0, datetime.timezone.utc)
            val = float(a.get("Mon_Results") or 0.0)
            lat = float(a.get("Latitude") or (ft.get("geometry") or {}).get("y") or 0.0)
            lon = float(a.get("Longitude") or (ft.get("geometry") or {}).get("x") or 0.0)
            entry = by_stn.setdefault(
                sid,
                {"station_id": sid, "lat": lat, "lon": lon, "units": a.get("Mon_Units") or "µg/m3", "records": []},
            )
            if lat:
                entry["lat"] = lat
            if lon:
                entry["lon"] = lon
            entry["records"].append({
                "ts_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "min_post_blast": min_post_blast(dt_utc),
                "value": val,
            })
        if not data.get("exceededTransferLimit") and len(feats) < page_size:
            break
        offset += len(feats)
        if not feats:
            break

    # Load modeled receptor peaks (if present) to dynamically refresh inverse bounds
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
            "earliest_ts_utc": recs[0]["ts_utc"] if recs else None,
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
        archive.setdefault("epa_xact", {})[sid] = {
            "lat": info["lat"], "lon": info["lon"], "units": info["units"], "series": recs
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


def fetch_asos_24h(archive: dict, now_utc: datetime.datetime):
    """Fetch full rolling 24h+ ASOS METAR/SPECI series (including 24h pre-blast baseline)."""
    start_dt = min(EVENT_DT - datetime.timedelta(hours=24), now_utc - datetime.timedelta(hours=26))
    end_dt = now_utc + datetime.timedelta(days=1)
    stn_params = "&".join(f"station={s}" for s in ASOS_STATIONS)
    url = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?{stn_params}"
        "&data=tmpc&data=dwpc&data=drct&data=sknt&data=gust&data=alti&data=skyc1"
        f"&year1={start_dt.year}&month1={start_dt.month}&day1={start_dt.day}"
        f"&year2={end_dt.year}&month2={end_dt.month}&day2={end_dt.day}"
        "&tz=Etc%2FUTC&format=onlycomma&latlon=yes"
    )
    txt = http_get_text(url, timeout=35)
    cutoff_24h = min(EVENT_DT - datetime.timedelta(hours=24), now_utc - datetime.timedelta(hours=24))
    by_stn = {}

    def fnum(val):
        try:
            return float(val) if val not in (None, "", "M") else None
        except ValueError:
            return None

    for row in csv.DictReader(io.StringIO(txt)):
        s = row.get("station")
        valid = row.get("valid", "")
        if not s or len(valid) < 16:
            continue
        try:
            dt_utc = datetime.datetime.strptime(valid[:16], "%Y-%m-%d %H:%M").replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        if dt_utc < cutoff_24h:
            continue
        drct = fnum(row.get("drct"))
        sknt = fnum(row.get("sknt"))
        rec = {
            "valid_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "min_post_blast": min_post_blast(dt_utc),
            "temp_c": fnum(row.get("tmpc")),
            "dewpoint_c": fnum(row.get("dwpc")),
            "wind_from_deg": drct,
            "wind_to_deg": round((drct + 180.0) % 360.0, 1) if drct is not None else None,
            "wind_ms": round(sknt * 0.514444, 2) if sknt is not None else None,
            "gust_kt": fnum(row.get("gust")),
            "sky": row.get("skyc1") if row.get("skyc1") not in (None, "", "M") else None,
        }
        entry = by_stn.setdefault(s, {
            "station": f"K{s}",
            "lat": round(float(row["lat"]), 4),
            "lon": round(float(row["lon"]), 4),
            "history": {},
        })
        entry["history"][rec["valid_utc"]] = rec

    out = {}
    for s, info in sorted(by_stn.items()):
        ksid = f"K{s}"
        # Merge into persistent local archive
        arch_map = {r["valid_utc"]: r for r in (archive.get("asos", {}).get(ksid) or [])}
        arch_map.update(info["history"])
        full_hist = sorted(arch_map.values(), key=lambda r: r["valid_utc"])
        archive.setdefault("asos", {})[ksid] = full_hist

        # For live_telemetry.json, keep full 24h+ window starting from min(T-24h, now-24h)
        win_start = (now_utc - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        blast_pre = (EVENT_DT - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        keep_from = min(win_start, blast_pre)
        # Keep observations with valid wind or temp, compactly packed for web
        hist_24h = [r for r in full_hist if r["valid_utc"] >= keep_from]
        latest_wind = next(
            (r for r in reversed(hist_24h) if r["wind_from_deg"] is not None and r["wind_ms"] is not None),
            hist_24h[-1] if hist_24h else None,
        )
        dkm, brg = dist_brg_km(ORIGIN_LAT, ORIGIN_LON, info["lat"], info["lon"])
        # Compact series_24h keeps JSON crisp while retaining every METAR/SPECI
        series_compact = [
            {
                "ts_utc": r["valid_utc"],
                "min_post_blast": r["min_post_blast"],
                "wind_from_deg": r["wind_from_deg"],
                "wind_to_deg": r["wind_to_deg"],
                "wind_ms": r["wind_ms"],
                "temp_c": r["temp_c"],
            }
            for r in hist_24h
            if r["wind_from_deg"] is not None or r["temp_c"] is not None
        ]
        out[ksid] = {
            "station": ksid,
            "lat": info["lat"],
            "lon": info["lon"],
            "dist_km": dkm,
            "bearing_deg": brg,
            "latest": latest_wind,
            "obs_count_24h": len(series_compact),
            "series_24h": series_compact,
        }
    return out


def fetch_rwis_24h(archive: dict, now_utc: datetime.datetime):
    """Fetch both latest metadata/currents AND full 24h+ 10-minute historical series for Iowa DOT RWIS."""
    url_cur = "https://mesonet.agron.iastate.edu/api/1/currents.json?network=IA_RWIS"
    data = http_get_json(url_cur)
    meta = {}
    for row in data.get("data", []):
        sid = row.get("station")
        if sid not in RWIS_STATIONS:
            continue
        lat, lon = float(row.get("lat") or 0), float(row.get("lon") or 0)
        dkm, brg = dist_brg_km(ORIGIN_LAT, ORIGIN_LON, lat, lon)
        sknt = row.get("sknt")
        drct = row.get("drct")
        meta[sid] = {
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

    # Fetch 24h+ 10-minute history from IEM rwis.py
    start_dt = min(EVENT_DT - datetime.timedelta(hours=24), now_utc - datetime.timedelta(hours=24))
    end_dt = now_utc + datetime.timedelta(minutes=30)
    stns_csv = ",".join(sorted(RWIS_STATIONS))
    url_hist = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/rwis.py?"
        f"stations={stns_csv}&vars=tmpf,drct,sknt,gust"
        f"&sts={start_dt.strftime('%Y-%m-%dT%H:%MZ')}&ets={end_dt.strftime('%Y-%m-%dT%H:%MZ')}&format=comma"
    )
    hist_by_stn = {}
    try:
        txt = http_get_text(url_hist, timeout=30)

        def fnum(val):
            try:
                return float(val) if val not in (None, "", "M", "None") else None
            except ValueError:
                return None

        for row in csv.DictReader(io.StringIO(txt)):
            sid = row.get("station")
            ob = row.get("obtime", "")
            if sid not in RWIS_STATIONS or len(ob) < 16:
                continue
            try:
                dt_utc = datetime.datetime.strptime(ob[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                continue
            drct = fnum(row.get("drct"))
            sknt = fnum(row.get("sknt"))
            tmpf = fnum(row.get("tmpf"))
            if drct is None and tmpf is None:
                continue
            ts_utc = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            hist_by_stn.setdefault(sid, {})[ts_utc] = {
                "ts_utc": ts_utc,
                "min_post_blast": min_post_blast(dt_utc),
                "wind_from_deg": drct,
                "wind_to_deg": round((drct + 180.0) % 360.0, 1) if drct is not None else None,
                "wind_ms": round(sknt * 0.514444, 2) if sknt is not None else None,
                "temp_f": tmpf,
            }
    except Exception:
        pass

    out = {}
    for sid, m in sorted(meta.items()):
        arch_map = {r["ts_utc"]: r for r in (archive.get("rwis", {}).get(sid) or [])}
        arch_map.update(hist_by_stn.get(sid, {}))
        full_ser = sorted(arch_map.values(), key=lambda r: r["ts_utc"])
        archive.setdefault("rwis", {})[sid] = full_ser

        # Downsample very dense (>1/10min) pre-blast points gently if huge; keep all 10-min bins
        m["obs_count_24h"] = len(full_ser)
        m["series_24h"] = full_ser
        out[sid] = m
    return out


def fetch_openmeteo_24h(archive: dict, now_utc: datetime.datetime):
    """Fetch 24h+ hourly vertical meteorology at IAAAP + 24h+ hourly PM2.5/PM10/AQI across 5 corridor towns."""
    start_d = min(EVENT_DT - datetime.timedelta(hours=24), now_utc - datetime.timedelta(hours=24)).strftime("%Y-%m-%d")
    end_d = max(EVENT_DT + datetime.timedelta(hours=6), now_utc + datetime.timedelta(hours=6)).strftime("%Y-%m-%d")

    url_wx = (
        f"https://api.open-meteo.com/v1/forecast?latitude={ORIGIN_LAT}&longitude={ORIGIN_LON}"
        "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
        "wind_speed_80m,wind_direction_80m,wind_speed_120m,wind_direction_120m,wind_speed_180m,wind_direction_180m,"
        "boundary_layer_height,wind_speed_975hPa,wind_direction_975hPa,geopotential_height_975hPa,"
        "wind_speed_950hPa,wind_direction_950hPa,geopotential_height_950hPa,"
        "wind_speed_925hPa,wind_direction_925hPa,geopotential_height_925hPa,"
        "wind_speed_900hPa,wind_direction_900hPa,geopotential_height_900hPa,"
        "wind_speed_850hPa,wind_direction_850hPa,geopotential_height_850hPa"
        f"&wind_speed_unit=ms&timezone=UTC&start_date={start_d}&end_date={end_d}"
    )
    wx = http_get_json(url_wx)
    wx_h = wx.get("hourly") or {}

    # Compact 24h+ hourly surface/PBL summary at IAAAP
    times_w = wx_h.get("time") or []
    met_24h = []
    win_lo = min(EVENT_DT - datetime.timedelta(hours=24), now_utc - datetime.timedelta(hours=24))
    win_hi = max(EVENT_DT + datetime.timedelta(hours=5), now_utc + datetime.timedelta(hours=4))
    for i, tstr in enumerate(times_w):
        dt = datetime.datetime.strptime(tstr, "%Y-%m-%dT%H:%M").replace(tzinfo=datetime.timezone.utc)
        if not (win_lo <= dt <= win_hi):
            continue
        met_24h.append({
            "ts_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "min_post_blast": min_post_blast(dt),
            "temp_c": (wx_h.get("temperature_2m") or [None])[i],
            "wind_10m_ms": (wx_h.get("wind_speed_10m") or [None])[i],
            "wind_10m_deg": (wx_h.get("wind_direction_10m") or [None])[i],
            "wind_180m_ms": (wx_h.get("wind_speed_180m") or [None])[i],
            "wind_180m_deg": (wx_h.get("wind_direction_180m") or [None])[i],
            "pbl_m": (wx_h.get("boundary_layer_height") or [None])[i],
        })

    # Regional Air Quality across 5 corridor locations (full 24h+ hourly series)
    lats = ",".join(str(p[1]) for p in AQ_POINTS)
    lons = ",".join(str(p[2]) for p in AQ_POINTS)
    url_aq = (
        f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lats}&longitude={lons}"
        f"&hourly=pm10,pm2_5,us_aqi&timezone=UTC&start_date={start_d}&end_date={end_d}"
    )
    aq_raw = http_get_json(url_aq)
    if isinstance(aq_raw, dict):
        aq_raw = [aq_raw]
    now_hr = now_utc.strftime("%Y-%m-%dT%H:00")
    aq_current = []
    aq_series = {}
    for (name, lat, lon), blk in zip(AQ_POINTS, aq_raw):
        h = blk.get("hourly") or {}
        times = h.get("time") or []
        idx = times.index(now_hr) if now_hr in times else max(0, len(times) - 1)
        aq_current.append({
            "location": name, "lat": lat, "lon": lon,
            "hour_utc": times[idx] if times else None,
            "pm2_5": (h.get("pm2_5") or [None])[idx],
            "pm10": (h.get("pm10") or [None])[idx],
            "us_aqi": (h.get("us_aqi") or [None])[idx],
        })
        ser = []
        for i, tstr in enumerate(times):
            dt = datetime.datetime.strptime(tstr, "%Y-%m-%dT%H:%M").replace(tzinfo=datetime.timezone.utc)
            if not (win_lo <= dt <= now_utc + datetime.timedelta(hours=1)):
                continue
            ser.append({
                "ts_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "min_post_blast": min_post_blast(dt),
                "pm2_5": (h.get("pm2_5") or [None])[i],
                "pm10": (h.get("pm10") or [None])[i],
                "us_aqi": (h.get("us_aqi") or [None])[i],
            })
        aq_series[name] = ser

    # Fetch batched 4-node NOAA HRRR 3 km vertical wind & PBL profiles across the 192x156 km domain!
    hrrr_nodes_raw = {}
    try:
        n_lats = ",".join(str(p[1]) for p in SPATIAL_HRRR_NODES)
        n_lons = ",".join(str(p[2]) for p in SPATIAL_HRRR_NODES)
        url_hrrr = (
            f"https://api.open-meteo.com/v1/forecast?latitude={n_lats}&longitude={n_lons}"
            "&hourly=wind_speed_10m,wind_direction_10m,wind_speed_80m,wind_direction_80m,"
            "boundary_layer_height,wind_speed_975hPa,wind_direction_975hPa,geopotential_height_975hPa,"
            "wind_speed_950hPa,wind_direction_950hPa,geopotential_height_950hPa,"
            "wind_speed_925hPa,wind_direction_925hPa,geopotential_height_925hPa,"
            "wind_speed_900hPa,wind_direction_900hPa,geopotential_height_900hPa,"
            "wind_speed_850hPa,wind_direction_850hPa,geopotential_height_850hPa"
            "&models=ncep_hrrr_conus&wind_speed_unit=ms&timezone=UTC"
            "&start_date=2026-11-25&end_date=2026-11-25"
        )
        h_raw = http_get_json(url_hrrr, timeout=25)
        if isinstance(h_raw, dict):
            h_raw = [h_raw]
        for (nid, _, _, _), blk in zip(SPATIAL_HRRR_NODES, h_raw):
            hrrr_nodes_raw[nid] = blk.get("hourly") or {}
    except Exception:
        pass

    archive["openmeteo_met"] = met_24h
    archive["openmeteo_aq"] = aq_series
    return {
        "iaaap_hourly_raw": wx_h,
        "hrrr_nodes_raw": hrrr_nodes_raw,
        "met_24h": met_24h,
        "corridor_aq": aq_current,
        "corridor_aq_24h": aq_series,
    }


def _extract_hourly_profile(om: dict, elev_m: float, hours_utc: list):
    times_om = om.get("time") or []
    u_hr, v_hr, pbl_hr = [], [], []
    for hr in hours_utc:
        key = f"2026-11-25T{hr:02d}:00"
        idx = times_om.index(key) if key in times_om else 0
        def g(k):
            arr = om.get(k)
            return arr[idx] if (arr and idx < len(arr)) else None
        pbl_val = g("boundary_layer_height")
        pbl_hr.append(float(pbl_val if pbl_val is not None else 300.0))
        cand = [
            (10.0,  g("wind_speed_10m"),  g("wind_direction_10m")),
            (80.0,  g("wind_speed_80m"),  g("wind_direction_80m")),
            (120.0, g("wind_speed_120m"), g("wind_direction_120m")),
            (180.0, g("wind_speed_180m"), g("wind_direction_180m")),
        ]
        for p_lbl in ("975hPa", "950hPa", "925hPa", "900hPa", "850hPa"):
            gh = g(f"geopotential_height_{p_lbl}")
            ws = g(f"wind_speed_{p_lbl}")
            wd = g(f"wind_direction_{p_lbl}")
            if gh is not None and ws is not None and wd is not None:
                cand.append((max(15.0, float(gh) - elev_m), float(ws), float(wd)))
        lvls = [(float(z), float(ws), float(wd)) for (z, ws, wd) in cand if ws is not None and wd is not None]
        lvls.sort(key=lambda x: x[0])
        if not lvls:
            lvls = [(10.0, 4.0, 45.0), (1330.0, 8.0, 55.0)]
        zs = [x[0] for x in lvls]
        us = [wind_from_to_uv(x[2], x[1])[0] for x in lvls]
        vs = [wind_from_to_uv(x[2], x[1])[1] for x in lvls]
        u_hr.append([lin_interp(z, zs, us) for z in Z_GRID_M])
        v_hr.append([lin_interp(z, zs, vs) for z in Z_GRID_M])
    return u_hr, v_hr, pbl_hr


def build_wind_assimilation(asos: dict, rwis: dict, om_hourly_raw: dict, hrrr_nodes_raw: dict = None):
    """
    Build a 4-node spatially varying 10-minute vertical wind field (T=0 -> T=300m) combining
    NOAA HRRR 3 km (`ncep_hrrr_conus`) across IAAAP, Kahoka, Kirksville, and Ottumwa with
    local inverse-distance-weighted live ASOS + RWIS surface observations (`observed_reanalysis`
    up to obs_cutoff_min, smoothly relaxing into HRRR 3 km forecast beyond).
    """
    fallback_om = om_hourly_raw or {}
    if "geopotential_height_975hPa" not in fallback_om:
        base_om_path = DATA_DIR / "openmeteo_iaaap.json"
        if base_om_path.exists():
            fallback_om = json.loads(base_om_path.read_text(encoding="utf-8")).get("hourly") or {}

    hours_utc = [15, 16, 17, 18, 19, 20]
    times_min = list(range(0, 301, 10))
    m_per_lon = 111.320 * math.cos(math.radians(ORIGIN_LAT))

    # Collect ALL ASOS + RWIS stations with their lat/lon for per-node spatial IDW weighting!
    all_obs_stations = []
    for k, st in (asos or {}).items():
        all_obs_stations.append((k, st["lat"], st["lon"], 1.0, st.get("series_24h") or []))
    for k, rw in (rwis or {}).items():
        all_obs_stations.append((k, rw["lat"], rw["lon"], 0.65, rw.get("series_24h") or []))

    # Pre-index nearest observation per station per 10-min time bin for speed
    stn_bin_uv = []
    for k, slat, slon, net_w, ser in all_obs_stations:
        bins = []
        for tm in times_min:
            best, best_dt = None, 18.1
            for r in ser:
                if r.get("wind_from_deg") is None or r.get("wind_ms") is None:
                    continue
                dt = abs(r["min_post_blast"] - tm)
                if dt < best_dt:
                    best_dt = dt
                    best = r
            bins.append(wind_from_to_uv(best["wind_from_deg"], best["wind_ms"]) if best is not None else None)
        stn_bin_uv.append((k, slat, slon, net_w, bins))

    spatial_nodes_out = []
    iaaap_result = None

    for nid, nlat, nlon, nelev in SPATIAL_HRRR_NODES:
        om_node = (hrrr_nodes_raw or {}).get(nid) or fallback_om
        u_hr, v_hr, pbl_hr = _extract_hourly_profile(om_node, nelev, hours_utc)

        # Compute node-local IDW observed surface wind (within 110 km of node, p=2, L=15km)
        obs_uv, obs_counts = [], []
        node_cutoff = 0
        for i, tm in enumerate(times_min):
            wsum, usum, vsum, cnt = 0.0, 0.0, 0.0, 0
            for _, slat, slon, net_w, bins in stn_bin_uv:
                uv = bins[i]
                if uv is None:
                    continue
                dkm, _ = dist_brg_km(nlat, nlon, slat, slon)
                if dkm > 115.0:
                    continue
                w = net_w / ((dkm + 15.0) ** 2)
                usum += w * uv[0]
                vsum += w * uv[1]
                wsum += w
                cnt += 1
            if wsum > 0 and cnt >= 1:
                obs_uv.append((usum / wsum, vsum / wsum))
                obs_counts.append(cnt)
                if cnt >= 2 or (nid == "Kirksville" and cnt >= 1):
                    node_cutoff = tm
            else:
                obs_uv.append(None)
                obs_counts.append(0)

        u_fcst_all, v_fcst_all, u_assim_all, v_assim_all, pbl_all = [], [], [], [], []
        timeline_summary = []
        last_du, last_dv = 0.0, 0.0
        for i, tm in enumerate(times_min):
            t_hr = tm / 60.0
            h_lo = min(int(t_hr), len(hours_utc) - 2)
            hf = min(1.0, t_hr - h_lo)
            uf = [(1.0 - hf) * u_hr[h_lo][zi] + hf * u_hr[h_lo + 1][zi] for zi in range(len(Z_GRID_M))]
            vf = [(1.0 - hf) * v_hr[h_lo][zi] + hf * v_hr[h_lo + 1][zi] for zi in range(len(Z_GRID_M))]
            pbl_c = round(min(1075.0, max(285.0, (1.0 - hf) * pbl_hr[h_lo] + hf * pbl_hr[h_lo + 1])), 1)

            if obs_uv[i] is not None and tm <= node_cutoff:
                mode = "observed_reanalysis"
                uo, vo = obs_uv[i]
                du, dv = uo - uf[0], vo - vf[0]
                last_du, last_dv = du, dv
            else:
                mode = "forecast_projection"
                decay = math.exp(-max(0.0, tm - node_cutoff) / 60.0)
                du, dv = last_du * decay, last_dv * decay

            ua = [round(uf[zi] + du * math.exp(-Z_GRID_M[zi] / 320.0), 3) for zi in range(len(Z_GRID_M))]
            va = [round(vf[zi] + dv * math.exp(-Z_GRID_M[zi] / 320.0), 3) for zi in range(len(Z_GRID_M))]
            u_fcst_all.append([round(x, 3) for x in uf])
            v_fcst_all.append([round(x, 3) for x in vf])
            u_assim_all.append(ua)
            v_assim_all.append(va)
            pbl_all.append(pbl_c)

            if nid == "IAAAP":
                fc_deg, fc_spd = uv_to_from_spd(uf[0], vf[0])
                as_deg, as_spd = uv_to_from_spd(ua[0], va[0])
                d_deg = ((as_deg - fc_deg + 540.0) % 360.0) - 180.0
                ts_utc = (EVENT_DT + datetime.timedelta(minutes=tm)).strftime("%Y-%m-%dT%H:%M:%SZ")
                timeline_summary.append({
                    "t_min": tm,
                    "ts_utc": ts_utc,
                    "mode": mode,
                    "n_obs": obs_counts[i],
                    "assim_from_deg": as_deg,
                    "assim_ms": as_spd,
                    "fcst_from_deg": fc_deg,
                    "fcst_ms": fc_spd,
                    "delta_deg": round(d_deg, 1),
                    "delta_ms": round(as_spd - fc_spd, 2),
                })

        x_km = round((nlon - ORIGIN_LON) * m_per_lon, 2)
        y_km = round((nlat - ORIGIN_LAT) * 111.132, 2)
        node_obj = {
            "id": nid,
            "lat": nlat,
            "lon": nlon,
            "x_km": x_km,
            "y_km": y_km,
            "obs_cutoff_min": node_cutoff,
            "pbl_m": pbl_all,
            "u_assim": u_assim_all,
            "v_assim": v_assim_all,
        }
        spatial_nodes_out.append(node_obj)
        if nid == "IAAAP":
            iaaap_result = (node_cutoff, u_fcst_all, v_fcst_all, u_assim_all, v_assim_all, pbl_all, timeline_summary)

    obs_cutoff_min, u_fcst_all, v_fcst_all, u_assim_all, v_assim_all, pbl_all, timeline_summary = iaaap_result
    obs_cutoff_utc = (EVENT_DT + datetime.timedelta(minutes=obs_cutoff_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "model": "NOAA HRRR 3 km (ncep_hrrr_conus) + 4-node ASOS/RWIS IDW",
        "obs_cutoff_min": obs_cutoff_min,
        "obs_cutoff_utc": obs_cutoff_utc,
        "z_grid_m": Z_GRID_M,
        "times_min": times_min,
        "pbl_m": pbl_all,
        "u_fcst": u_fcst_all,
        "v_fcst": v_fcst_all,
        "u_assim": u_assim_all,
        "v_assim": v_assim_all,
        "timeline_summary": timeline_summary,
        "spatial_nodes": spatial_nodes_out,
    }


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

    # Prevent overlapping manual + systemd invocations from triggering upstream HTTP 429s
    lock_path = DATA_DIR / ".fetch.lock"
    lock_fp = open(lock_path, "w")
    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log_line("[skip] Another fetch_measurements.py run is already in progress.")
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    iso_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_slug = now_utc.strftime("%Y%m%d_%H%M%S")
    archive = load_archive()

    # Load previous good bundle so transient upstream errors/429s NEVER clobber existing live data!
    prev_bundle = {}
    if LIVE_BUNDLE_FILE.exists():
        try:
            prev_bundle = json.loads(LIVE_BUNDLE_FILE.read_text(encoding="utf-8"))
        except Exception:
            prev_bundle = {}

    errors = []
    def safe(name, fn, default):
        try:
            res = fn()
            if not res and default:
                errors.append(f"{name}: empty response (retained previous cached data)")
                return default
            return res
        except Exception as e:
            errors.append(f"{name}: {e}")
            return default

    xact = safe("epa_xact", lambda: fetch_epa_xact(archive), prev_bundle.get("epa_xact") or {})
    monitors = safe("epa_monitors", fetch_epa_monitors, prev_bundle.get("epa_monitors") or [])
    asos = safe("iem_asos", lambda: fetch_asos_24h(archive, now_utc), prev_bundle.get("asos") or {})
    rwis = safe("iem_rwis", lambda: fetch_rwis_24h(archive, now_utc), prev_bundle.get("rwis") or {})
    prev_om = {
        "corridor_aq": prev_bundle.get("openmeteo_aq") or [],
        "corridor_aq_24h": prev_bundle.get("openmeteo_aq_24h") or {},
        "met_24h": prev_bundle.get("openmeteo_met_24h") or [],
    }
    om = safe("openmeteo", lambda: fetch_openmeteo_24h(archive, now_utc), prev_om)
    wind_assim = safe(
        "wind_assimilation",
        lambda: build_wind_assimilation(asos, rwis, om.get("iaaap_hourly_raw"), om.get("hrrr_nodes_raw")),
        prev_bundle.get("wind_assimilation") or {},
    )

    save_archive(archive)

    xact_max_post = max((s["post_blast_max"] for s in xact.values()), default=0.0)
    xact_latest_ts = max((s["latest_ts_utc"] for s in xact.values() if s.get("latest_ts_utc")), default=None)
    kbrl = asos.get("KBRL", {}).get("latest") or {}
    as02_fmax = (xact.get("AS02") or {}).get("f_max_pct")
    as03_fmax = (xact.get("AS03") or {}).get("f_max_pct")

    total_24h_obs = (
        sum(s.get("obs_count", 0) for s in xact.values())
        + sum(s.get("obs_count_24h", 0) for s in asos.values())
        + sum(s.get("obs_count_24h", 0) for s in rwis.values())
    )

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
            "total_24h_obs": total_24h_obs,
            "wind_obs_cutoff_min": wind_assim.get("obs_cutoff_min", 0),
            "wind_obs_cutoff_utc": wind_assim.get("obs_cutoff_utc"),
        },
        "epa_xact": xact,
        "epa_monitors": monitors,
        "asos": asos,
        "rwis": rwis,
        "openmeteo_aq": om.get("corridor_aq", []),
        "openmeteo_aq_24h": om.get("corridor_aq_24h", {}),
        "openmeteo_met_24h": om.get("met_24h", []),
        "wind_assimilation": wind_assim,
        "source_errors": errors,
    }

    if push:
        try:
            env = dict(os.environ, PATH=f"/home/mike10010100/.gemini/antigravity-cli/bin:/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:{os.environ.get('PATH', '/usr/bin:/bin')}")
            subprocess.run(["git", "-C", str(BASE_DIR), "pull", "--ff-only", "origin", "main"], env=env, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    changed = True
    if LIVE_BUNDLE_FILE.exists():
        try:
            prev = json.loads(LIVE_BUNDLE_FILE.read_text(encoding="utf-8"))
            changed = strip_timestamps_for_diff(prev) != strip_timestamps_for_diff(bundle)
        except Exception:
            changed = True

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
        f"[{iso_utc}] Xact={len(xact)} (latest={xact_latest_ts}, max={xact_max_post:.4f} ug/m3) | "
        f"ASOS={len(asos)} RWIS={len(rwis)} (24h_obs={total_24h_obs}, wind_assim<=T+{wind_assim.get('obs_cutoff_min',0)}m) | "
        f"{snap_note}" + (f" | errors={errors}" if errors else "")
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
    parser = argparse.ArgumentParser(description="Fetch live + 24h IAAAP surrounding measurements")
    parser.add_argument("--push", action="store_true", help="Auto-commit & push site_data/live_telemetry.json if measurements changed")
    args = parser.parse_args()
    run_fetch(push=args.push)
