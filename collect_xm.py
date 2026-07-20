#!/usr/bin/env python3
"""Collect XM data via API XM (servapibi.xm.com.co, no key required).
Resolves MetricIds dynamically from the live catalog by name matching, caches
the mapping to data/xm/metric_map.json (editable override).

Outputs (data/xm/):
  thermal_daily.json  : per gas plant per day -> GWh generated, MBTU burned,
                        implied heat rate (MBTU/MWh)
  system_daily.json   : SIN demand (GWh-day), total thermal gas burn (MBTUD)
  heat_rates.json     : trailing-30d median implied HR per plant (editable)
  meta.json           : freshness stamps

Modes:
  python scripts/collect_xm.py               # incremental (last 35 days)
  python scripts/collect_xm.py --backfill    # from 2020-01-01 (run once)
"""
from __future__ import annotations
import json, sys, time, unicodedata
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from pydataxm.pydataxm import ReadDB

ROOT = Path(__file__).resolve().parents[1]
XMD = ROOT / "data" / "xm"
XMD.mkdir(parents=True, exist_ok=True)

WANTED = {
    # key: (name fragments to match in catalog, entity)
    "gene":      (["generacion real", "recurso"], "Recurso"),
    "fuel_mbtu": (["consumo comb", "mbtu"], "RecursoComb"),
    "demand":    (["demanda comercial"], "Sistema"),
}

def canon(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.lower()

def resolve_metrics(api: ReadDB) -> dict:
    cache = XMD / "metric_map.json"
    if cache.exists():
        m = json.loads(cache.read_text())
        if all(k in m for k in WANTED):
            return m
    inv = api.get_collections()  # DataFrame: MetricId, MetricName, Entity, ...
    inv["c"] = inv["MetricName"].map(canon)
    out = {}
    for key, (frags, ent_hint) in WANTED.items():
        cand = inv[inv["c"].apply(lambda x: all(f in x for f in map(canon, frags)))]
        if ent_hint:
            pref = cand[cand["Entity"].str.contains(ent_hint, case=False, na=False)]
            cand = pref if len(pref) else cand
        if not len(cand):
            raise SystemExit(f"XM catalog: no metric matches {frags}. "
                             f"Edit data/xm/metric_map.json manually.")
        row = cand.iloc[0]
        out[key] = {"metric": row["MetricId"], "entity": row["Entity"],
                    "name": row["MetricName"]}
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out

def fetch(api: ReadDB, m: dict, start: date, end: date) -> pd.DataFrame:
    df = api.request_data(m["metric"], m["entity"], start, end)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

def daily_sum(df: pd.DataFrame, id_col: str) -> pd.DataFrame:
    """Hourly frames have Values_Hour01..24; daily frames have Value."""
    if df.empty:
        return df
    hour_cols = [c for c in df.columns if c.startswith("Values_Hour")]
    d = df.copy()
    if hour_cols:
        d["val"] = d[hour_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    else:
        vc = "Value" if "Value" in d.columns else next(
            c for c in d.columns if "value" in c.lower())
        d["val"] = pd.to_numeric(d[vc], errors="coerce")
    d["date"] = pd.to_datetime(d["Date"]).dt.strftime("%Y-%m-%d")
    idc = next((c for c in d.columns if id_col.lower() in c.lower()), None)
    keys = ["date"] + ([idc] if idc else [])
    return d.groupby(keys, as_index=False)["val"].sum()

def merge_store(path: Path, new: dict) -> dict:
    old = json.loads(path.read_text()) if path.exists() else {}
    old.update(new)
    path.write_text(json.dumps(old, ensure_ascii=False, separators=(",", ":")))
    return old

def run(backfill: bool) -> None:
    api = ReadDB()
    mm = resolve_metrics(api)
    end = date.today()
    start = date(2020, 1, 1) if backfill else end - timedelta(days=35)

    gene = fuel = dem = pd.DataFrame()
    # chunk by ~6 months to be gentle on the API when backfilling
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=180), end)
        for name, tgt in (("gene", "gene"), ("fuel_mbtu", "fuel"),
                          ("demand", "dem")):
            try:
                part = fetch(api, mm[name], cur, nxt)
            except Exception as e:  # keep going; freshness badge will show gap
                print(f"WARN {name} {cur}->{nxt}: {e}")
                part = pd.DataFrame()
            if name == "gene":
                gene = pd.concat([gene, part])
            elif name == "fuel_mbtu":
                fuel = pd.concat([fuel, part])
            else:
                dem = pd.concat([dem, part])
        cur = nxt + timedelta(days=1)
        time.sleep(1)

    g = daily_sum(gene, "Name")     # kWh per resource per day
    f = daily_sum(fuel, "Name")     # MBTU per resource per day
    s = daily_sum(dem, "Name")      # kWh system demand

    thermal: dict = json.loads((XMD / "thermal_daily.json").read_text()) \
        if (XMD / "thermal_daily.json").exists() else {}
    name_col_g = [c for c in g.columns if c not in ("date", "val")]
    name_col_f = [c for c in f.columns if c not in ("date", "val")]
    gmap = {(r["date"], str(r[name_col_g[0]]).upper()): r["val"]
            for _, r in g.iterrows()} if len(g) and name_col_g else {}
    for _, r in f.iterrows() if len(f) else []:
        plant = str(r[name_col_f[0]]).upper() if name_col_f else "?"
        dte = r["date"]
        mbtu = float(r["val"])
        gwh = float(gmap.get((dte, plant), 0)) / 1e6  # kWh -> GWh
        hr = round(mbtu / (gwh * 1000), 3) if gwh > 0.005 else None
        thermal.setdefault(dte, {})[plant] = {
            "gwh": round(gwh, 3), "mbtu": round(mbtu, 0), "hr": hr}
    (XMD / "thermal_daily.json").write_text(
        json.dumps(thermal, ensure_ascii=False, separators=(",", ":")))

    system = {}
    dem_by_date = {r["date"]: float(r["val"]) / 1e6 for _, r in s.iterrows()} \
        if len(s) else {}
    for dte, plants in thermal.items():
        system[dte] = {
            "demand_gwh": round(dem_by_date.get(dte, 0), 2) or None,
            "gas_burn_mbtud": round(sum(p["mbtu"] for p in plants.values()), 0),
        }
    for dte, v in dem_by_date.items():
        system.setdefault(dte, {"gas_burn_mbtud": None})["demand_gwh"] = round(v, 2)
    merge_store(XMD / "system_daily.json", system)

    # trailing 30d implied HR medians (editable output)
    dates = sorted(thermal.keys())[-30:]
    hr_acc: dict = {}
    for dte in dates:
        for plant, v in thermal[dte].items():
            if v["hr"] and 4 < v["hr"] < 20:
                hr_acc.setdefault(plant, []).append(v["hr"])
    hr_out = {"unit": "MBTU/MWh", "method": "median implied HR, trailing 30d, "
              "bounds 4-20", "plants": {p: round(pd.Series(v).median(), 2)
                                        for p, v in sorted(hr_acc.items())}}
    (XMD / "heat_rates.json").write_text(
        json.dumps(hr_out, ensure_ascii=False, indent=1))

    (XMD / "meta.json").write_text(json.dumps({
        "metrics": mm,
        "last_thermal_date": dates[-1] if dates else None,
        "updated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
    }, ensure_ascii=False, indent=1))
    print(f"xm: thermal days={len(thermal)}, plants(30d)={len(hr_acc)}, "
          f"system days={len(system)}")

if __name__ == "__main__":
    run("--backfill" in sys.argv)
