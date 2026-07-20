#!/usr/bin/env python3
"""Parse TGI CGA public-report Excel exports (c_jlinero_*.xlsx style).
Auto-detects one of three known layouts:
  A) 'PROGRAMA DE TRANSPORTE' detail blocks: date -> remitente/point/segment ->
     rows Nomination / Authorisation / Confirmed / Renomination x 24 hours
  B) 'PROGRAMA DE TRANSPORTE POR PUNTO DE SALIDA': exit points x 24h (+Total)
  C) Route-level nominations: contract, remitente, entry->exit, market/sector,
     Energía Nominada / Volumen Nominado / Desvío, fecha+hora nominación
Merges into data/tgi_nominations.json (keyed stores, idempotent per gas day).
Usage : python scripts/parse_tgi.py <file.xlsx> [more.xlsx ...]
"""
import sys, json, re, warnings
from pathlib import Path
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "tgi_nominations.json"

def load_store() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text())
    return {"detail": {}, "program_by_point": {}, "routes": {}, "files": []}

def norm_date(v) -> str | None:
    s = str(v).strip()
    m = re.match(r"^(\d{2})-(\d{2})-(\d{2})$", s)          # 01-09-25 (DD-MM-YY)
    if m:
        return f"20{m.group(3)}-{m.group(2)}-{m.group(1)}"
    try:
        ts = pd.to_datetime(s, dayfirst=True, errors="raise")
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return None

def parse_detail(df: pd.DataFrame, store: dict) -> int:
    labels = {"Nomination", "Authorisation", "Confirmed", "Renomination"}
    gas_day, ctx, n = None, [], 0
    for _, row in df.iterrows():
        c0 = row.iloc[0]
        s0 = str(c0).strip() if pd.notna(c0) else ""
        d = norm_date(s0) if re.match(r"^\d{2}-\d{2}-\d{2}$", s0) else None
        if d:
            gas_day, ctx = d, []
            continue
        if s0 in labels:
            vals = [float(x) if pd.notna(x) and str(x).strip() != "" else 0.0
                    for x in row.iloc[1:25]]
            rem = ctx[0] if len(ctx) > 0 else "?"
            pnt = ctx[1] if len(ctx) > 1 else "?"
            seg = ctx[2] if len(ctx) > 2 else ""
            key = f"{gas_day}|{rem}|{pnt}|{seg}"
            store["detail"].setdefault(key, {})[s0.lower()] = {
                "total": round(sum(vals), 1), "hours": vals}
            n += 1
        elif s0 and "PROGRAMA" not in s0.upper():
            if s0 not in labels:
                if len(ctx) >= 3 or (gas_day and not ctx):
                    if any(str(x).strip() for x in [s0]):
                        pass
                ctx.append(s0)
                ctx = ctx[-3:]
    return n

def parse_by_point(df: pd.DataFrame, store: dict, fname: str) -> int:
    # header row = the one whose col1 is 00:00:00
    hdr = None
    for i in range(min(10, len(df))):
        if str(df.iloc[i, 1]).strip().startswith("00:00"):
            hdr = i
            break
    if hdr is None:
        return 0
    gas_day = None
    for i in range(min(10, len(df))):
        for v in df.iloc[i].tolist():
            d = norm_date(v)
            if d and pd.notna(v) and not isinstance(v, float):
                gas_day = d
                break
        if gas_day:
            break
    gas_day = gas_day or f"file:{fname}"
    n = 0
    for _, row in df.iloc[hdr + 1:].iterrows():
        name = str(row.iloc[0]).strip()
        if not name or name.lower() == "nan":
            continue
        vals = [float(x) if pd.notna(x) and str(x).strip() != "" else 0.0
                for x in row.iloc[1:25]]
        total = row.iloc[25] if df.shape[1] > 25 and pd.notna(row.iloc[25]) \
            else sum(vals)
        store["program_by_point"].setdefault(gas_day, {})[name] = {
            "total": round(float(total), 1), "hours": vals}
        n += 1
    return n

def parse_routes(df: pd.DataFrame, store: dict) -> int:
    hdr = None
    for i in range(min(12, len(df))):
        vals = [str(v).strip() for v in df.iloc[i].tolist()]
        if "Remitente" in vals and any("Nominada" in v for v in vals):
            hdr = i
            break
    if hdr is None:
        return 0
    cols = [str(c).strip() for c in df.iloc[hdr]]
    body = df.iloc[hdr + 1:].copy()
    body.columns = cols + [f"x{i}" for i in range(len(body.columns) - len(cols))] \
        if len(body.columns) > len(cols) else cols[:len(body.columns)]
    n = 0
    for _, r in body.iterrows():
        rem = str(r.get("Remitente", "")).strip()
        if not rem or rem.lower() == "nan":
            continue
        d = norm_date(r.get("Fecha Nominación", ""))
        if not d:
            continue
        rec = {
            "contract": str(r.get("Nombre Contacto", "")).strip(),
            "remitente": rem,
            "entry": str(r.get("Punto Entrada", "")).strip(),
            "exit": str(r.get("Punto Salida", "")).strip(),
            "market": str(r.get("Mercado", "")).strip(),
            "sector": str(r.get("Sector", "")).strip(),
            "mbtu": float(pd.to_numeric(r.get("Energía Nominada"), errors="coerce") or 0),
            "kpc": float(pd.to_numeric(r.get("Volumen Nominado"), errors="coerce") or 0),
            "desvio": str(r.get("Desvio", "")).strip(),
            "hora": str(r.get("Hora Nominación", "")).strip(),
        }
        store["routes"].setdefault(d, []).append(rec)
        n += 1
    return n

def detect_and_parse(path: str, store: dict) -> str:
    df = pd.read_excel(path, sheet_name=0, header=None)
    head_txt = " ".join(str(v) for v in df.iloc[:8].values.ravel()
                        if pd.notna(v)).upper()
    labels = df[0].astype(str).str.strip()
    if labels.isin(["Nomination", "Confirmed"]).any():
        n = parse_detail(df, store)
        return f"detail blocks: {n} series"
    if "PUNTO DE SALIDA" in head_txt:
        n = parse_by_point(df, store, Path(path).name)
        return f"program by exit point: {n} points"
    n = parse_routes(df, store)
    if n:
        return f"route nominations: {n} rows"
    return "UNRECOGNIZED layout - file archived, no data ingested"

if __name__ == "__main__":
    store = load_store()
    for p in sys.argv[1:]:
        msg = detect_and_parse(p, store)
        store["files"].append({"file": Path(p).name, "result": msg,
                               "at": pd.Timestamp.utcnow()
                               .strftime("%Y-%m-%dT%H:%MZ")})
        print(f"{Path(p).name}: {msg}")
    # routes: dedupe identical records per day
    for d, rows in store["routes"].items():
        seen, out = set(), []
        for r in rows:
            k = json.dumps(r, sort_keys=True, ensure_ascii=False)
            if k not in seen:
                seen.add(k)
                out.append(r)
        store["routes"][d] = out
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")))
