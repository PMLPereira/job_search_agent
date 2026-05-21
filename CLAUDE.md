# SP Job Tracker — Claude Code Context

## What this is
Automated daily job scanner for Pedro's São Paulo finance job search.  
- Scrapes 13 target company career pages every day via GitHub Actions  
- Scores each role against Pedro's CV using Claude API  
- Publishes a dashboard at https://pmlpereira.github.io/job_search_agent/  
- Data lives in `docs/data.json`; dashboard in `docs/index.html`

## Architecture
```
scripts/scan.py          ← main scraper + scorer + HTML generator
docs/data.json           ← scraped + scored jobs (committed to repo = GitHub Pages data)
docs/index.html          ← dashboard HTML (auto-generated)
.github/workflows/daily-scan.yml  ← runs scan.py daily at 07:00 UTC, commits output
requirements.txt         ← anthropic, requests, beautifulsoup4, lxml, python-dateutil
```

## How to run locally
```bash
pip install -r requirements.txt
# Set ANTHROPIC_API_KEY env var (needed for scoring only; scraping works without it)
python scripts/scan.py
```
Output: updated `docs/data.json` and `docs/index.html`

---

## Scraper status — WHAT WORKS vs WHAT'S BROKEN

### ✅ CONFIRMED WORKING
| Company | Type | Config |
|---|---|---|
| Nubank | Greenhouse | `boards.greenhouse.io/nubank` |
| Stone / StoneCo | Greenhouse | `boards.greenhouse.io/stone` |
| Oliver Wyman SP | Greenhouse | `boards.greenhouse.io/oliverwyman` |
| Itaú BBA | Gupy SSR | slug `vemproitau` → `vemproitau.gupy.io` |
| Vinci Partners | Gupy SSR | slug `vincipartners` |
| Kinea Investimentos | Gupy SSR | slug `kinea` |
| Pátria Investimentos | Gupy SSR | slug `patriainvestimentos` |
| XP Inc. | Greenhouse | `boards.greenhouse.io/xpinc` (8 jobs confirmed) |
| Warren Investimentos | Gupy SSR | slug `warrenbrasil` |
| Avenue Securities | Gupy SSR | slug `avenue` |

### ❌ STILL BROKEN — needs fixing
| Company | Problem | Known info |
|---|---|---|
| **BTG Pactual** | Custom portal — not Gupy or Greenhouse | Career site: `carreiras.btgpactual.com/vagas` (Angular/SPA). No known public API. May need Apify or Playwright. |
| **Bradesco BBI** | Cornerstone OnDemand (CSOD) ATS | Portal: `bradesco.csod.com/ux/ats/careersite/1/home`. CSOD API requires auth. |
| **Santander Brasil** | Workday ATS | Portal: `santander.wd3.myworkdayjobs.com/pt-BR/SantanderCareers`. Workday has a known public JSON API (POST). |

---

## Key technical context

### Gupy platform migration (why old scraper broke)
Gupy migrated from API-based to Next.js SSR. Old approach (`portal.api.gupy.io?companySlug=X`) no longer works.  
New approach: fetch `https://{slug}.gupy.io/`, parse the `<script id="__NEXT_DATA__">` tag, jobs are in `props.pageProps.jobs[]`.

### Workday public API (for Santander)
Workday career pages have a public JSON endpoint:
```
POST https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs
Body: {"limit":20,"offset":0,"searchText":"","appliedFacets":{}}
Returns: {"total":N,"jobPostings":[{"title":"...","locationsText":"...","externalPath":"/..."}]}
```
For Santander: `POST https://santander.wd3.myworkdayjobs.com/wday/cxs/santander/SantanderCareers/jobs`

### BTG Pactual options
1. Apify actor `fantastic-jobs/career-site-job-listing-api` — covers 54 ATS platforms, might have BTG
2. Apify actor `zen-studio/gupy-jobs-scraper` — specifically for Gupy (not BTG though)
3. Playwright/browser-based scraper (heavier, needs browser in GitHub Actions)
4. Manual check: look at what XHR calls `carreiras.btgpactual.com` makes in browser devtools

### Bradesco BBI options
1. Apify `fantastic-jobs/career-site-job-listing-api` — covers CSOD/Cornerstone
2. Scrape `banco.bradesco/trabalheconosco/` HTML (the main Bradesco portal, simpler)
3. Accept that BBI-specific roles are posted on the main Bradesco CSOD portal

---

## Tasks to complete (in priority order)

1. **Add Workday scraper** for Santander Brasil
   - Add `scrape_workday(company)` function to `scripts/scan.py`
   - Update Santander entry in COMPANIES: `"scrape_type": "workday"`, add `"workday_tenant": "santander"`, `"workday_instance": "wd3"`, `"workday_board": "SantanderCareers"`
   - Update dispatch logic in `main()` line ~766

2. **Fix BTG Pactual** — pick one approach:
   - Option A: Use Apify `fantastic-jobs/career-site-job-listing-api` (needs APIFY_API_KEY secret)
   - Option B: Write a scraper that fetches the HTML and parses visible job cards
   - Option C: Use `requests_html` or Playwright for JS rendering (complex for GitHub Actions)

3. **Fix Bradesco BBI** — pick one approach:
   - Option A: Use Apify (same as BTG)
   - Option B: Scrape `banco.bradesco/trabalheconosco/` (main Bradesco page, CSOD-powered)
   - Option C: Drop from list (BBI is a small team, few open roles)

4. **Test run** — run `python scripts/scan.py` locally after fixes and check results

5. **Commit + push** — GitHub Actions will pick it up for the next daily run

---

## dispatch logic (scan.py ~line 766)
```python
# Current (only handles gupy and greenhouse):
fn = scrape_gupy if company.get("scrape_type")=="gupy" else scrape_greenhouse

# Should become:
SCRAPERS = {
    "gupy":       scrape_gupy,
    "greenhouse": scrape_greenhouse,
    "workday":    scrape_workday,  # add this
    "btg":        scrape_btg,      # add this
}
fn = SCRAPERS.get(company.get("scrape_type", "greenhouse"), scrape_greenhouse)
```

---

## GitHub Actions
- Workflow: `.github/workflows/daily-scan.yml`
- Runs: daily at 07:00 UTC + manual trigger (`workflow_dispatch`)
- Secrets needed: `ANTHROPIC_API_KEY` (set in repo Settings → Secrets)
- If adding Apify: also add `APIFY_API_KEY` secret

## Dashboard URL
https://pmlpereira.github.io/job_search_agent/
