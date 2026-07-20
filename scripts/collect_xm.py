#!/usr/bin/env python3
"""Collect XM data via API XM (keyless). Robust version: hardwired documented
metric IDs, tolerant to schema drift, never hard-fails - logs and continues."""
import json, sys, time, traceback
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
XMD = ROOT / "data" / "xm"
XMD.mkdir(parents=True, exist_ok=True)

METRICS = {
    "gene":      {"metric": "Gene",                "entity": "Recurso"},
    "fuel_mbtu": {"metric": "ConsCombustibleMBTU", "entity": "RecursoComb"},
    "demand":    {"metric": "DemaCome",            "entity": "Sistema"},
}

def log(*a): print("[collect_xm]", *a, flush=True)

def metric_map():
    p = XMD / "metric_map.json"
    try:
        m = json.loads(p.read_text())
        if all(k in m for k in METRICS):
            return m
    except Exception:
        pass
    p.write_text(json.dumps(METRICS, ensure_ascii=False, indent=1))
    return dict(METRICS)

def find_col(df, *frags):
    for c in df.columns:
        cl = c.lower()
        if any(f in cl for f in frags):
            return c
    return None

def daily_sum(df, want_gas=False):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=["date", "id", "val"])
    d = df.copy()
    fuel = find_col(d, "comb", "fuel")
    if want_gas and fuel:
        gas = d[d[fuel].astype(str).str.upper().str.contains("GAS", na=False)]
        if len(gas):
            d = gas
    hcols = [c for c in d.columns if "hour" in c.lower()]
    if hcols:
        d["val"] = d[hcols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    else:
        vc = find_col(d, "value")
        if not vc:
            return pd.DataFrame(columns=["date", "id", "val"])
        d["val"] = pd.to_numeric(d[vc], errors="coerce")
    dc = find_col(d, "date", "fecha")
    d["date"] = pd.to_datetime(d[dc]).dt.strftime("%Y-%m-%d")
    ic = find_col(d, "name") or find_col(d, "code")
    d["id"] = d[ic].astype(str).str.upper().str.strip() if ic else "SYSTEM"
    return d.groupby(["date", "id"], as_index=False)["val"].sum()

def fetch_range(api, spec, s, e):
    out = []
    cur = s
    while cur <= e:
        nxt = min(date(cur.year + 1, 1, 1) - timedelta(days=1), e)
        try:
            part = api.request_data(spec["metric"], spec["entity"], cur, nxt)
            n = len(part) if isinstance(part, pd.DataFrame) else 0
            log(spec["metric"], cur, "->", nxt, ":", n, "rows",
                list(part.columns)[:8] if n else "")
            if n:
                out.append(part)
        except Exception as ex:
            log("WARN", spec["metric"], cur, "->", nxt, ":", repr(ex))
        cur = nxt + timedelta(days=1)
        time.sleep(1)
    return pd.concat(out) if out else pd.DataFrame()

def main():
    from pydataxm.pydataxm import ReadDB
    api = ReadDB()
    mm = metric_map()
    end = date.today()
    start = date(2020, 1, 1) if "--backfill" in sys.argv \
        else end - timedelta(days=35)
    g = daily_sum(fetch_range(api, mm["gene"], start, end))
    f = daily_sum(fetch_range(api, mm["fuel_mbtu"], start, end), want_gas=True)
    s = daily_sum(fetch_range(api, mm["demand"], start, end))
    log("shapes gene/fuel/demand:", len(g), len(f), len(s))

    tpath = XMD / "thermal_daily.json"
    try:
        thermal = json.loads(tpath.read_text())
        if not isinstance(thermal, dict):
            thermal = {}
    except Exception:
        thermal = {}
    gmap = {(r.date, r.id): r.val for r in g.itertuples()}
    for r in f.itertuples():
        gwh = float(gmap.get((r.date, r.id), 0)) / 1e6
        mbtu = float(r.val)
        hr = round(mbtu / (gwh * 1000), 3) if gwh > 0.005 else None
        thermal.setdefault(r.date, {})[r.id] = {
            "gwh": round(gwh, 3), "mbtu": round(mbtu, 0), "hr": hr}
    tpath.write_text(json.dumps(thermal, ensure_ascii=False,
                                separators=(",", ":")))

    spath = XMD / "system_daily.json"
    try:
        system = json.loads(spath.read_text())
        if not isinstance(system, dict):
            system = {}
    except Exception:
        system = {}
    dem = {r.date: float(r.val) / 1e6 for r in s.itertuples()}
    for dte, plants in thermal.items():
        system.setdefault(dte, {})["gas_burn_mbtud"] = \
            round(sum(p["mbtu"] for p in plants.values()), 0)
    for dte, v in dem.items():
        system.setdefault(dte, {})["demand_gwh"] = round(v, 2)
    spath.write_text(json.dumps(system, ensure_ascii=False,
                                separators=(",", ":")))

    days = sorted(thermal.keys())[-30:]
    acc = {}
    for dte in days:
        for p, v in thermal[dte].items():
            if v.get("hr") and 4 < v["hr"] < 20:
                acc.setdefault(p, []).append(v["hr"])
    (XMD / "heat_rates.json").write_text(json.dumps(
        {"unit": "MBTU/MWh", "method": "median implied HR, trailing 30d",
         "plants": {p: round(pd.Series(v).median(), 2)
                    for p, v in sorted(acc.items())}},
        ensure_ascii=False, indent=1))
    (XMD / "meta.json").write_text(json.dumps(
        {"metrics": mm, "last_thermal_date": days[-1] if days else None,
         "updated_utc": pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%MZ")},
        ensure_ascii=False, indent=1))
    log("done: thermal days", len(thermal), "| system days", len(system),
        "| plants(30d)", len(acc))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("FAILED WITH ERROR - partial data kept:")
        traceback.print_exc()
    sys.exit(0)
