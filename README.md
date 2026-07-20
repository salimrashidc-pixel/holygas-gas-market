# HOLYGAS · Gas Market Terminal

Self-updating Colombian natural gas market dashboard. Static site (GitHub Pages)
fed by scheduled GitHub Actions; zero servers, USD 0/month.

## Go live in ~20 minutes

1. **Create the repo** (private is fine for Actions; Pages on a free plan
   requires the repo to be public — data here is public-source):
   ```bash
   cd holygas-gas-market
   git init -b main && git add -A && git commit -m "v1: HOLYGAS gas market terminal"
   git remote add origin https://github.com/<YOUR_USER>/holygas-gas-market.git
   git push -u origin main
   ```
2. **Enable Pages**: repo → Settings → Pages → Source: *Deploy from a branch* →
   `main` / `/ (root)`. Your URL: `https://<YOUR_USER>.github.io/holygas-gas-market/`
   — open it on your phone and "Add to Home Screen".
3. **Allow the bot to commit**: Settings → Actions → General →
   *Workflow permissions* → **Read and write**.
4. **First sync**: Actions tab → *Update market data* → Run workflow.
   Then run *Backfill XM history* once (pulls 2020-01-01 → today; ~30–60 min).

## Data flow

| Tab | Source | Mode |
|---|---|---|
| Balance / Supply·Fields | Gestor BEC `bi-gas/oferta` export | Upload to `inbox/` (auto-probe pending endpoint) |
| Declared PP overlay | DPNG workbook (MinEnergía) | Upload new resolution to `inbox/` |
| Supply·Transporters | Promigas / Promioriente BEO, TGI CGA | Auto-probe each run + upload fallback |
| TGI Nominations | CGA public reports (Excel exports) | Upload `c_jlinero_*.xlsx` to `inbox/` |
| Thermal & Power | API XM (`servapibi.xm.com.co`, keyless) | **Fully automatic** |
| Weekly outlook | XM CP deck (PDF) | Upload to `inbox/` (archived; summary manual for now) |

**The `inbox/` contract**: drop any recognized file (phone browser →
"Add file · Upload"); `process_uploads.py` sniffs the layout, routes it to the
right parser, refreshes `data/`, and archives the file to `inbox/processed/`.

## Schedules (Bogotá time)
04:00 D-1 supply · 11:30 post-predespacho · 14:30 post-despacho · 18:30 sweep.

## Tuning knobs
- `data/settings.json` — PC factors (KPC→MBTU), display defaults.
- `data/xm/metric_map.json` — auto-resolved XM metric IDs; edit to override.
- `data/xm/heat_rates.json` — derived 30-d median implied HR per plant; editable.

## Roadmap (v2)
- Confirm TGI CGA / Promigas export endpoints from a browser network trace →
  flip those sources to fully automatic.
- Parse XM weekly deck PDF (pdfplumber) into the maintenance card.
- Auth via Cloudflare Pages + Access if the obscure-URL posture changes.
