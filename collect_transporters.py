#!/usr/bin/env python3
"""Attempt automated pulls from transporter public portals. Each source is an
independent attempt; any failure degrades to 'upload mode' (drop the file in
inbox/), never breaks the pipeline.

Status (tune from Action logs):
  TGI CGA   : public report viewer at cga.tgi.com.co:8081 (?public=124
              'Consolidado De Recibos'). Likely a JS app with an Excel export
              endpoint; we probe common export routes and save the response to
              inbox/ for parse routing. If the probe fails -> manual export.
  Promigas  : /Beo/Paginas/VolumenEntregadoProductor.aspx (ASP.NET page).
              We fetch and try to read embedded grid tables via pandas.
  Promioriente: same layout as Promigas.
Outputs: data/transporters.json  { source: {date: {point: kpcd}} } + meta
"""
from __future__ import annotations
import io, json, re, sys
from pathlib import Path
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "transporters.json"
UA = {"User-Agent": "Mozilla/5.0 (HOLYGAS market dashboard; contact: internal)"}

SOURCES = {
    "PROMIGAS": "https://www.promigas.com/Beo/Paginas/VolumenEntregadoProductor.aspx",
    "PROMIORIENTE": "https://www.promioriente.com/Beo/Paginas/VolumenEntregadoProductor.aspx",
}
TGI_CGA = "https://cga.tgi.com.co:8081/?public=124.%20Consolidado%20De%20Recibos"

def load() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text())
    return {"sources": {}, "meta": {}}

def try_html_tables(name: str, url: str, store: dict) -> str:
    r = requests.get(url, headers=UA, timeout=45, verify=True)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    best = max(tables, key=len) if tables else None
    if best is None or len(best) < 3:
        return f"{name}: page fetched ({len(r.text)//1024} KB) but no data grid "\
               f"found - likely JS-rendered; keep manual upload for now"
    # Heuristic ingest: first col = point, remaining numeric cols = values
    day = pd.Timestamp.today().strftime("%Y-%m-%d")
    rows = {}
    for _, row in best.iterrows():
        point = str(row.iloc[0]).strip()
        vals = pd.to_numeric(row.iloc[1:], errors="coerce").dropna()
        if point and len(vals):
            rows[point] = round(float(vals.iloc[-1]), 1)
    if rows:
        store["sources"].setdefault(name, {})[day] = rows
        return f"{name}: grid ingested, {len(rows)} points"
    return f"{name}: grid parsed but empty - manual upload"

def try_tgi(store: dict) -> str:
    r = requests.get(TGI_CGA, headers=UA, timeout=45, verify=False)
    kb = len(r.content) // 1024
    ct = r.headers.get("content-type", "")
    if "spreadsheet" in ct or "excel" in ct:
        p = ROOT / "inbox" / f"tgi_auto_{pd.Timestamp.today():%Y%m%d}.xlsx"
        p.write_bytes(r.content)
        return f"TGI: export captured to inbox ({kb} KB) - routed to parser"
    return (f"TGI CGA: reachable (HTTP {r.status_code}, {kb} KB, {ct}) but "
            f"viewer is app-rendered; export endpoint TBD from browser "
            f"network trace - manual export meanwhile")

if __name__ == "__main__":
    store = load()
    logs = []
    for name, url in SOURCES.items():
        try:
            logs.append(try_html_tables(name, url, store))
        except Exception as e:
            logs.append(f"{name}: FETCH FAILED ({type(e).__name__}: {e}) - "
                        f"manual upload mode")
    try:
        logs.append(try_tgi(store))
    except Exception as e:
        logs.append(f"TGI: FETCH FAILED ({type(e).__name__}) - manual mode")
    store["meta"] = {"updated_utc":
                     pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
                     "log": logs}
    OUT.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")))
    print("\n".join(logs))
