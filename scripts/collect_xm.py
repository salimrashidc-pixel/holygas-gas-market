#!/usr/bin/env python3
"""XM collector - raw API XM (servapibi.xm.com.co), no third-party client.
Outputs (data/xm/):
  thermal_daily.json : {date: {plant: {gwh, mbtu, hr, fuel}}}   gas-fired burn only (GAS + GAS NI)
  system_daily.json  : {date: {demand_gwh, gas_burn_mbtud, gas_gen_gwh}}
  heat_rates.json    : trailing-30d median implied HR per plant (MBTU/MWh), bounds 4-20
  resources.json     : plant code -> name/type (cached catalog)
  meta.json          : freshness + run stats
Modes: incremental (last 35 d) | --backfill (2020-01-01 ->) | --since YYYY-MM-DD
Never exits non-zero: logs and keeps partial data.
"""
import json, os, sys, time, traceback
from datetime import date, timedelta
from pathlib import Path
import requests

ROOT = Path(os.environ.get("HG_ROOT", Path(__file__).resolve().parents[1]))
XMD = ROOT / "data" / "xm"; XMD.mkdir(parents=True, exist_ok=True)
BASE = "https://servapibi.xm.com.co"
STATS = {"chunks_ok": 0, "chunks_fail": 0}

def log(*a): print("[collect_xm]", *a, flush=True)

def post(path, body, tries=4, timeout=60):
    for i in range(tries):
        try:
            r = requests.post(f"{BASE}/{path}", json=body, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            log(f"HTTP {r.status_code} {path} {body.get('MetricId')} attempt {i+1}")
        except Exception as ex:
            log(f"{type(ex).__name__} {path} {body.get('MetricId')} attempt {i+1}")
        time.sleep(5 * (i + 1))
    return None

def hourly(mid, ent, s, e):
    out, cur = [], s
    while cur <= e:
        nxt = min(cur + timedelta(days=30), e)
        j = post("hourly", {"MetricId": mid, "StartDate": cur.isoformat(),
                            "EndDate": nxt.isoformat(), "Entity": ent})
        if j is None:
            STATS["chunks_fail"] += 1
        else:
            STATS["chunks_ok"] += 1
            for it in j.get("Items", []):
                d = str(it.get("Date", ""))[:10]
                for he in it.get("HourlyEntities", []):
                    v = he.get("Values", {}); tot = 0.0
                    for h in range(1, 25):
                        x = v.get(f"Hour{h:02d}")
                        if x not in (None, ""):
                            try: tot += float(x)
                            except ValueError: pass
                    out.append((d, str(v.get("code", "")).strip(), str(v.get("Name", "")).strip(), tot))
        cur = nxt + timedelta(days=1); time.sleep(0.4)
    log(f"{mid}/{ent}: rows={len(out)}")
    return out

def load(p, default):
    try:
        v = json.loads(p.read_text()); return v if isinstance(v, dict) else default
    except Exception:
        return default

def dump(p, obj, pretty=False):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1 if pretty else None,
                            separators=None if pretty else (",", ":")))

def resources():
    p = XMD / "resources.json"; cur = load(p, {})
    if cur: return cur
    j = post("Lists", {"MetricId": "ListadoRecursos"}); m = {}
    for it in (j or {}).get("Items", []):
        for le in it.get("ListEntities", []):
            v = le.get("Values", {}); c = str(v.get("Code", "")).strip()
            if c: m[c] = {"name": str(v.get("Name", "")).strip(), "type": v.get("Type", ""), "src": v.get("EnerSource", "")}
    if m: dump(p, m)
    return m

def main():
    end = date.today()
    if "--backfill" in sys.argv: start = date(2020, 1, 1)
    elif "--since" in sys.argv: start = date.fromisoformat(sys.argv[sys.argv.index("--since") + 1])
    else: start = end - timedelta(days=35)
    log(f"window {start} -> {end}")
    res = resources(); log(f"resources: {len(res)}")
    fuel = hourly("ConsCombustibleMBTU", "Recurso", start, end)
    gene = hourly("Gene", "Recurso", start, end)
    dem = hourly("DemaCome", "Sistema", start, end)

    gmap = {}
    for d, code, _, v in gene: gmap[(d, code)] = gmap.get((d, code), 0.0) + v
    acc = {}
    for d, code, fname, mbtu in fuel:
        if "GAS" not in fname.upper() or mbtu <= 0: continue
        name = res.get(code, {}).get("name") or code
        a = acc.setdefault((d, name), {"mbtu": 0.0, "gwh": gmap.get((d, code), 0.0) / 1e6, "fuel": set()})
        a["mbtu"] += mbtu; a["fuel"].add(fname.upper())
    thermal = load(XMD / "thermal_daily.json", {})
    for d in {k[0] for k in acc}: thermal[d] = {}
    for (d, name), a in acc.items():
        hr = round(a["mbtu"] / (a["gwh"] * 1000), 3) if a["gwh"] > 0.005 else None
        thermal[d][name] = {"gwh": round(a["gwh"], 3), "mbtu": round(a["mbtu"], 0), "hr": hr,
                            "fuel": "+".join(sorted(a["fuel"]))}
    dump(XMD / "thermal_daily.json", thermal)
    dump(XMD / "thermal_latest.json", {d: thermal[d] for d in sorted(thermal)[-10:]})

    system = load(XMD / "system_daily.json", {})
    for d, plants in thermal.items():
        s = system.setdefault(d, {})
        s["gas_burn_mbtud"] = round(sum(p["mbtu"] for p in plants.values()), 0)
        s["gas_gen_gwh"] = round(sum(p["gwh"] for p in plants.values()), 2)
    for d, _, _, v in dem:
        system.setdefault(d, {})["demand_gwh"] = round(v / 1e6, 2)
    cut = (end - timedelta(days=5)).isoformat()   # XM settles demand/fuel over ~5 days
    for d in system: system[d]["prelim"] = d > cut
    dump(XMD / "system_daily.json", system)

    days = sorted(thermal.keys())[-30:]; hr_acc = {}
    for d in days:
        for p, v in thermal[d].items():
            if v.get("hr") and 4 < v["hr"] < 20: hr_acc.setdefault(p, []).append(v["hr"])
    med = lambda xs: sorted(xs)[len(xs) // 2]
    dump(XMD / "heat_rates.json", {"unit": "MBTU/MWh", "method": "median implied HR, trailing 30d, bounds 4-20",
                                    "plants": {p: round(med(v), 2) for p, v in sorted(hr_acc.items())}}, pretty=True)
    dump(XMD / "meta.json", {"source": "API XM servapibi.xm.com.co (Gene/Recurso, ConsCombustibleMBTU/Recurso, DemaCome/Sistema)",
                              "window": [start.isoformat(), end.isoformat()],
                              "last_thermal_date": days[-1] if days else None,
                              "last_demand_date": max((d for d in system if "demand_gwh" in system[d]), default=None),
                              "chunks": STATS, "updated_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}, pretty=True)
    log(f"done: thermal days={len(thermal)} system days={len(system)} plants(30d)={len(hr_acc)} chunks={STATS}")

if __name__ == "__main__":
    try: main()
    except Exception:
        log("FAILED WITH ERROR - partial data kept:"); traceback.print_exc()
    sys.exit(0)
