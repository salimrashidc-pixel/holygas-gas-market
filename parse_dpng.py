#!/usr/bin/env python3
"""Parse Declaración de Producción (DPNG) support workbook (MinEnergía / Gestor).
Header found dynamically (row containing 'CAMPO' + 'PTDV'). Units: MBTUD.
Output: data/dpng.json
  fields: { CAMPO: { operator, pc_btu_pc, monthly: {"2026-01": {pp, ptdv, cidv}},
                     annual_pp: {"2026": avg, ...} } }
Usage : python scripts/parse_dpng.py <input.xlsx>
"""
import sys, json, warnings
from pathlib import Path
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

def run(path: str) -> None:
    raw = pd.read_excel(path, sheet_name=0, header=None)
    hdr = None
    for i in range(min(40, len(raw))):
        vals = [str(v).strip().upper() for v in raw.iloc[i].tolist()]
        if "CAMPO" in vals and any("PTDV" in v for v in vals):
            hdr = i
            break
    if hdr is None:
        raise SystemExit("DPNG: header row not found")
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
    df = df.dropna(subset=["CAMPO", "MES"])
    df["MES"] = pd.to_datetime(df["MES"], errors="coerce")
    df = df.dropna(subset=["MES"])
    for c in df.columns:
        if c not in ("CAMPO", "CONTRATO", "RAZÓN SOCIAL AGENTE",
                     "PARTICIPACIÓN (Operador/Asociado/Estado)", "MES"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    part_col = "PARTICIPACIÓN (Operador/Asociado/Estado)"
    pp_col = next(c for c in df.columns if c.startswith("PP "))
    pc_col = next(c for c in df.columns if c.startswith("PODER"))
    df["m"] = df["MES"].dt.strftime("%Y-%m")
    df["y"] = df["MES"].dt.strftime("%Y")

    fields: dict = {}
    for campo, sub in df.groupby("CAMPO"):
        op_rows = sub[sub[part_col].astype(str).str.contains("Operador", na=False)]
        operator = ""
        if len(op_rows):
            operator = str(op_rows["RAZÓN SOCIAL AGENTE"].mode().iloc[0])
        pc_vals = op_rows[pc_col].dropna()
        pc = round(float(pc_vals.median()), 1) if len(pc_vals) else None

        monthly = {}
        for m, mm in sub.groupby("m"):
            ptdv = float(mm["PTDV"].sum(skipna=True))
            cidv = float(mm["CIDV"].sum(skipna=True))
            pp = float(mm.loc[mm[part_col].astype(str)
                              .str.contains("Operador", na=False), pp_col]
                       .sum(skipna=True))
            monthly[m] = {"pp": round(pp, 1), "ptdv": round(ptdv, 1),
                          "cidv": round(cidv, 1)}
        annual_pp = {}
        for y, yy in sub[sub[part_col].astype(str)
                         .str.contains("Operador", na=False)].groupby("y"):
            per_m = yy.groupby("m")[pp_col].sum(skipna=True)
            if len(per_m):
                annual_pp[y] = round(float(per_m.mean()), 1)
        fields[str(campo)] = {"operator": operator, "pc_btu_pc": pc,
                              "monthly": monthly, "annual_pp": annual_pp}

    DATA.mkdir(exist_ok=True)
    (DATA / "dpng.json").write_text(json.dumps({
        "source": "DPNG 2026-2035 - Res. 01205 (2026-07-07)",
        "unit": "MBTUD", "fields": fields,
        "updated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
    }, ensure_ascii=False, separators=(",", ":")))
    print(f"dpng: {len(fields)} fields, months "
          f"{df['m'].min()} -> {df['m'].max()}")

if __name__ == "__main__":
    run(sys.argv[1])
