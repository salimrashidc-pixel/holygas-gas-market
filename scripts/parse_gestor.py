#!/usr/bin/env python3
"""Parse Gestor del Mercado (BEC bi-gas/oferta) supply export.
Input : xlsx with sheet 'Export' -> Fecha | Tipo de Producción | Proceso | Campo producción | Energía (MBTU)
Output: data/gestor_daily.json   (row-level, last N days)
        data/gestor_monthly.json (per field avg MBTUD per month, full history)
        data/gestor_meta.json    (freshness + coverage)
Usage : python scripts/parse_gestor.py <input.xlsx> [--days 180]
"""
import sys, json, warnings
from pathlib import Path
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

def run(path: str, keep_days: int = 180) -> None:
    df = pd.read_excel(path, sheet_name="Export", header=0)
    df.columns = ["fecha", "tipo", "proceso", "campo", "mbtu"]
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha", "campo"]).copy()
    df["mbtu"] = pd.to_numeric(df["mbtu"], errors="coerce").fillna(0.0)
    df["d"] = df["fecha"].dt.strftime("%Y-%m-%d")
    df["m"] = df["fecha"].dt.strftime("%Y-%m")

    last = df["fecha"].max()
    cut = (last - pd.Timedelta(days=keep_days)).strftime("%Y-%m-%d")

    daily = (
        df[df["d"] >= cut]
        .groupby(["d", "tipo", "proceso", "campo"], as_index=False)["mbtu"].sum()
    )
    daily_rows = [
        [r.d, r.tipo, r.proceso, r.campo, round(float(r.mbtu), 1)]
        for r in daily.itertuples()
    ]

    # Monthly avg MBTUD per field (sum MBTU / days observed in month)
    g = df.groupby(["campo", "m"]).agg(mbtu=("mbtu", "sum"), days=("d", "nunique"))
    monthly: dict = {}
    for (campo, m), row in g.iterrows():
        monthly.setdefault(campo, {})[m] = round(row.mbtu / max(row.days, 1), 1)

    proc_m = df.groupby(["proceso", "m"]).agg(mbtu=("mbtu", "sum"), days=("d", "nunique"))
    proc_monthly: dict = {}
    for (proc, m), row in proc_m.iterrows():
        proc_monthly.setdefault(proc, {})[m] = round(row.mbtu / max(row.days, 1), 1)

    DATA.mkdir(exist_ok=True)
    (DATA / "gestor_daily.json").write_text(json.dumps(
        {"cols": ["date", "tipo", "proceso", "campo", "mbtu"], "rows": daily_rows},
        ensure_ascii=False, separators=(",", ":")))
    (DATA / "gestor_monthly.json").write_text(json.dumps(
        {"unit": "MBTUD (monthly avg)", "fields": monthly, "procesos": proc_monthly},
        ensure_ascii=False, separators=(",", ":")))
    (DATA / "gestor_meta.json").write_text(json.dumps({
        "source": "Gestor del Mercado (BEC) bi-gas/oferta",
        "unit": "MBTU/day",
        "first_date": df["d"].min(), "last_date": df["d"].max(),
        "n_fields": int(df["campo"].nunique()),
        "updated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
    }, ensure_ascii=False))
    print(f"gestor: {len(df)} rows, {df['d'].min()} -> {df['d'].max()}, "
          f"{df['campo'].nunique()} fields; daily rows kept: {len(daily_rows)}")

if __name__ == "__main__":
    src = sys.argv[1]
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 180
    run(src, days)
