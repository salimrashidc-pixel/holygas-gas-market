#!/usr/bin/env python3
"""Route files dropped in inbox/ to the right parser by content sniffing,
then archive them to inbox/processed/. Runs in CI on every push and on cron.

Recognized:
  - Gestor export (sheet 'Export' with 'Campo producción')      -> parse_gestor
  - Gestor field map (sheet 'Export' with 'Productor')          -> field_map.json
  - DPNG declaration ('DPGN' sheet or DPNG in filename)         -> parse_dpng
  - TGI CGA exports (c_jlinero_* style, three layouts)          -> parse_tgi
  - XM weekly maintenance deck (pdf)                            -> stored + noted
"""
import json, shutil, subprocess, sys, warnings
from pathlib import Path
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
INBOX, DONE = ROOT / "inbox", ROOT / "inbox" / "processed"
DATA = ROOT / "data"

def sniff(p: Path) -> str:
    if p.suffix.lower() == ".pdf":
        return "xm_pdf"
    if p.suffix.lower() not in (".xlsx", ".xlsm", ".xls"):
        return "skip"
    try:
        xl = pd.ExcelFile(p)
    except Exception:
        return "skip"
    if any("DPGN" in s or "DPNG" in s for s in xl.sheet_names) or \
            "DPNG" in p.name.upper():
        return "dpng"
    head = pd.read_excel(p, sheet_name=0, header=None, nrows=12)
    txt = " ".join(str(v) for v in head.values.ravel() if pd.notna(v))
    if "Campo producción" in txt and "Energía" in txt:
        return "gestor"
    if "Productor" in txt and "Campo de producción" in txt:
        return "fieldmap"
    if "PROGRAMA DE TRANSPORTE" in txt.upper() or "Remitente" in txt:
        return "tgi"
    return "tgi"  # default guess for unknown xlsx: TGI parser self-reports

def handle(p: Path) -> str:
    kind = sniff(p)
    if kind == "skip":
        return f"{p.name}: unsupported type, left in inbox"
    if kind == "gestor":
        subprocess.run([sys.executable, ROOT / "scripts" / "parse_gestor.py",
                        str(p)], check=True)
    elif kind == "dpng":
        subprocess.run([sys.executable, ROOT / "scripts" / "parse_dpng.py",
                        str(p)], check=True)
    elif kind == "tgi":
        subprocess.run([sys.executable, ROOT / "scripts" / "parse_tgi.py",
                        str(p)], check=True)
    elif kind == "fieldmap":
        df = pd.read_excel(p, sheet_name=0, header=0)
        df.columns = [c.strip() for c in df.columns]
        m: dict = {}
        for _, r in df.iterrows():
            campo = str(r.get("Campo de producción", "")).strip()
            if not campo or campo.lower() == "nan":
                continue
            e = m.setdefault(campo, {"producers": [], "region": None})
            prod = str(r.get("Productor", "")).strip()
            if prod and prod not in e["producers"]:
                e["producers"].append(prod)
            reg = r.get("Región")
            if pd.notna(reg):
                e["region"] = str(reg).strip()
        (DATA / "field_map.json").write_text(
            json.dumps(m, ensure_ascii=False, separators=(",", ":")))
    elif kind == "xm_pdf":
        dest = DATA / "xm" / "maintenance_decks"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest / p.name)
    DONE.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), DONE / p.name)
    return f"{p.name}: processed as {kind}"

if __name__ == "__main__":
    msgs = [handle(p) for p in sorted(INBOX.iterdir())
            if p.is_file() and p.parent == INBOX]
    print("\n".join(msgs) if msgs else "inbox empty")
