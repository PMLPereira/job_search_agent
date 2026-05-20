"""
SP Job Tracker - Daily Scanner
Scrapes target company career pages, scores roles against Pedro's CV,
and writes docs/data.json + docs/index.html for GitHub Pages.
"""

import os
import json
import time
import hashlib
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import anthropic

# ── PEDRO'S PROFILE ────────────────────────────────────────────────────────────
PEDRO_PROFILE = """
Name: Pedro Miguel Lourenco Pereira
Current role: Technology Manager / Delivery Lead - Bank of America Global Markets, London
Previous: Principal Consultant - Capco Financial Services (7 years, London)

Key strengths:
- 12+ years senior program/technology delivery in global capital markets
- $20M regulatory compliance program delivery (BofA / Capco)
- $5M+ annual budget with full P&L accountability
- Cross-functional leadership: Product, Engineering, Risk, Operations, Compliance
- Regulatory expertise: Fed reporting, BACEN, risk management, data governance
- AI & automation: Python, SQL, LLMs, prompt engineering, no-code tools (Make, Zapier)
- Languages: Portuguese (native), English (fluent)
- Education: MSc Management (Robert Gordon UK), MSc Environmental Systems (NOVA Portugal)
- Certifications: Google Cloud AI, Data Science, Advanced ML (Cambridge Spark)

Target: Director / Head of Technology Delivery / Senior Program Manager in São Paulo
Target salary: R$35,000–50,000 take-home per month
Timeline: Available from mid-2027 (relocating from London)
"""

# ── TARGET COMPANIES & CAREER PAGE CONFIGS ────────────────────────────────────
COMPANIES = [
    {
        "name": "BTG Pactual",
        "tier": 1,
        "sector": "Investment Banking",
        "careers_url": "https://btgpactual.gupy.io/",
        "why": "Fastest growing IB in Brazil. Hire international profiles aggressively.",
        "scrape_type": "gupy",
        "gupy_company": "btgpactual",
    },
    {
        "name": "XP Investimentos",
        "tier": 1,
        "sector": "Financial Services",
        "careers_url": "https://xpi.gupy.io/",
        "why": "Tech-forward platform growing into institutional markets.",
        "scrape_type": "gupy",
        "gupy_company": "xpi",
    },
    {
        "name": "Nubank",
        "tier": 1,
        "sector": "Fintech",
        "careers_url": "https://boards.greenhouse.io/nubank",
        "why": "Building institutional/B2B products. Your capital markets background is rare here.",
        "scrape_type": "greenhouse",
    },
    {
        "name": "Itaú BBA",
        "tier": 1,
        "sector": "Investment Banking",
        "careers_url": "https://vempraItau.gupy.io/",
        "why": "Your BofA regulatory work maps directly to their transformation agenda.",
        "scrape_type": "gupy",
        "gupy_company": "vempraItau",
    },
    {
        "name": "Pátria Investimentos",
        "tier": 1,
        "sector": "Alternative Assets / PE",
        "careers_url": "https://patriainvestimentos.gupy.io/",
        "why": "Growing fast, technology and operational transformation investment underway.",
        "scrape_type": "gupy",
        "gupy_company": "patriainvestimentos",
    },
    {
        "name": "Vinci Partners",
        "tier": 1,
        "sector": "Asset Management",
        "careers_url": "https://vincipartners.gupy.io/",
        "why": "Listed on Nasdaq. Scaling technology as AUM grows. Strong fit.",
        "scrape_type": "gupy",
        "gupy_company": "vincipartners",
    },
    {
        "name": "Bradesco BBI",
        "tier": 2,
        "sector": "Investment Banking",
        "careers_url": "https://bradescobbi.gupy.io/",
        "why": "Traditional bank modernising. Capco consulting background is a strong fit.",
        "scrape_type": "gupy",
        "gupy_company": "bradescobbi",
    },
    {
        "name": "Santander Brasil",
        "tier": 2,
        "sector": "Banking",
        "careers_url": "https://santander.gupy.io/",
        "why": "Large operation, active technology transformation. Regulatory profile fits.",
        "scrape_type": "gupy",
        "gupy_company": "santander",
    },
    {
        "name": "Stone / StoneCo",
        "tier": 2,
        "sector": "Fintech / Payments",
        "careers_url": "https://boards.greenhouse.io/stone",
        "why": "Serious technology scale. International profile welcome.",
        "scrape_type": "greenhouse",
    },
    {
        "name": "Warren Investimentos",
        "tier": 2,
        "sector": "Fintech",
        "careers_url": "https://warren.gupy.io/",
        "why": "Tech-first investment platform scaling rapidly.",
        "scrape_type": "gupy",
        "gupy_company": "warren",
    },
    {
        "name": "Kinea Investimentos",
        "tier": 2,
        "sector": "Alternative Assets",
        "careers_url": "https://kineainvestimentos.gupy.io/",
        "why": "Itaú group alt asset manager. Significant tech investment underway.",
        "scrape_type": "gupy",
        "gupy_company": "kineainvestimentos",
    },
    {
        "name": "Avenue Securities",
        "tier": 2,
        "sector": "Brokerage",
        "careers_url": "https://avenuesecurities.gupy.io/",
        "why": "Brazilian-American brokerage. Your bilingual + capital markets profile is perfect.",
        "scrape_type": "gupy",
        "gupy_company": "avenuesecurities",
    },
]

# Keywords that make a role worth scoring (otherwise skip)
RELEVANT_KEYWORDS = [
    "technology", "tecnologia", "programa", "program", "delivery", "entrega",
    "gerente", "manager", "diretor", "director", "head", "lider", "líder",
    "transformação", "transformation", "operações", "operations", "produto",
    "product", "dados", "data", "regulatory", "regulatório", "fintech",
    "capital markets", "mercados", "estratégia", "strategy", "senior", "sênior",
    "principal", "agile", "scrum", "pmo", "portfolio",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobScanner/1.0; +https://github.com)",
    "Accept": "text/html,application/xhtml+xml,application/json",
}


# ── SCRAPERS ──────────────────────────────────────────────────────────────────

def scrape_gupy(company: dict) -> list[dict]:
    """Scrape Gupy ATS (used by most Brazilian companies)."""
    slug = company.get("gupy_company", "")
    api_url = f"https://portal.api.gupy.io/api/v1/jobs?companySlug={slug}&limit=50&offset=0"
    try:
        r = requests.get(api_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = data.get("data", [])
        results = []
        for j in jobs:
            title = j.get("name", "")
            location = j.get("city", "") or j.get("state", "") or "Brasil"
            url = j.get("jobUrl", company["careers_url"])
            desc = j.get("description", "") or j.get("responsibilities", "") or ""
            if not is_relevant(title + " " + desc):
                continue
            results.append({
                "id": hashlib.md5(f"{company['name']}{title}{url}".encode()).hexdigest()[:12],
                "company": company["name"],
                "title": title,
                "location": location,
                "url": url,
                "description": desc[:2000],
                "found_at": datetime.now(timezone.utc).isoformat(),
            })
        return results
    except Exception as e:
        print(f"  Gupy error for {company['name']}: {e}")
        return []


def scrape_greenhouse(company: dict) -> list[dict]:
    """Scrape Greenhouse ATS (used by Nubank, Stone etc)."""
    board = company["careers_url"].split("greenhouse.io/")[-1].strip("/")
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    try:
        r = requests.get(api_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = data.get("jobs", [])
        results = []
        for j in jobs:
            title = j.get("title", "")
            location = j.get("location", {}).get("name", "")
            url = j.get("absolute_url", company["careers_url"])
            desc = BeautifulSoup(j.get("content", ""), "lxml").get_text(separator=" ")[:2000]
            # Filter for SP / Brazil / Remote
            loc_lower = location.lower()
            if location and not any(x in loc_lower for x in ["são paulo", "sao paulo", "brazil", "brasil", "remote", "remoto"]):
                continue
            if not is_relevant(title + " " + desc):
                continue
            results.append({
                "id": hashlib.md5(f"{company['name']}{title}{url}".encode()).hexdigest()[:12],
                "company": company["name"],
                "title": title,
                "location": location,
                "url": url,
                "description": desc,
                "found_at": datetime.now(timezone.utc).isoformat(),
            })
        return results
    except Exception as e:
        print(f"  Greenhouse error for {company['name']}: {e}")
        return []


def is_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in RELEVANT_KEYWORDS)


# ── AI SCORING ────────────────────────────────────────────────────────────────

def score_role(client: anthropic.Anthropic, job: dict) -> dict:
    """Score a role against Pedro's profile using Claude."""
    prompt = f"""You are a senior executive recruiter specialising in financial services technology in Brazil.

Analyse how well this candidate profile matches the job posting. Respond ONLY with a JSON object, no markdown, no preamble:

{{
  "score": <0-100 integer>,
  "verdict": "<Strong Match|Good Match|Partial Match|Weak Match>",
  "topReasons": ["<reason 1>", "<reason 2>"],
  "gaps": ["<gap 1>"],
  "talkingPoints": ["<tailored talking point 1>", "<tailored talking point 2>"],
  "suggestedContact": "<title of person to find at this company>",
  "applyRecommendation": "<Yes|Yes with tweaks|No>"
}}

CANDIDATE:
{PEDRO_PROFILE}

JOB:
Company: {job['company']}
Title: {job['title']}
Location: {job['location']}
Description: {job['description'][:1500]}
"""
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text.strip().replace("```json", "").replace("```", "")
        return json.loads(text)
    except Exception as e:
        print(f"  Scoring error for {job['title']}: {e}")
        return {"score": 0, "verdict": "Error", "topReasons": [], "gaps": [], "talkingPoints": [], "suggestedContact": "", "applyRecommendation": "No"}


# ── LOAD / MERGE EXISTING DATA ────────────────────────────────────────────────

def load_existing(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"jobs": [], "pipeline": [], "last_updated": ""}


def merge_jobs(existing: list[dict], new_jobs: list[dict]) -> list[dict]:
    existing_ids = {j["id"] for j in existing}
    merged = list(existing)
    for j in new_jobs:
        if j["id"] not in existing_ids:
            j["is_new"] = True
            merged.append(j)
        else:
            # Mark as no longer new
            for e in merged:
                if e["id"] == j["id"]:
                    e["is_new"] = False
    return merged


# ── HTML GENERATOR ────────────────────────────────────────────────────────────

def generate_html(data: dict) -> str:
    jobs = sorted(data["jobs"], key=lambda j: j.get("score", {}).get("score", 0), reverse=True)
    pipeline = data.get("pipeline", [])
    last_updated = data.get("last_updated", "")
    companies_meta = {c["name"]: c for c in COMPANIES}

    jobs_json = json.dumps(jobs)
    pipeline_json = json.dumps(pipeline)
    companies_json = json.dumps(COMPANIES)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pedro · SP Job Tracker</title>
<style>
  :root {{
    --bg: #0c0c0c; --bg2: #111; --bg3: #161616;
    --border: #1e1e1e; --border2: #252525;
    --text: #eee; --muted: #888; --dim: #444;
    --green: #4ecba0; --gold: #c0a060; --blue: #60b4f0;
    --red: #ff6b6b; --purple: #c060f0; --orange: #f0a060;
    --font: 'Georgia', serif; --mono: 'Courier New', monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--font); min-height: 100vh; padding: 24px 16px 60px; }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  .header {{ text-align: center; margin-bottom: 28px; }}
  .header h1 {{ font-size: 26px; font-weight: 400; }}
  .header .sub {{ font-size: 10px; color: var(--dim); font-family: var(--mono); letter-spacing: 3px; margin-top: 6px; }}
  .header .updated {{ font-size: 10px; color: var(--dim); font-family: var(--mono); margin-top: 4px; }}
  .tabs {{ display: flex; gap: 6px; justify-content: center; margin-bottom: 24px; flex-wrap: wrap; }}
  .tab {{ padding: 6px 16px; background: none; border: 1px solid transparent; border-radius: 7px; color: var(--dim); cursor: pointer; font-size: 10px; font-family: var(--mono); letter-spacing: 1px; transition: all 0.2s; }}
  .tab.active {{ background: var(--bg2); border-color: var(--border2); color: var(--text); }}
  .section {{ display: none; }}
  .section.active {{ display: block; }}
  .stats {{ display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; margin-bottom: 20px; }}
  .stat {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 8px 16px; text-align: center; }}
  .stat .val {{ font-size: 22px; font-family: var(--mono); font-weight: 700; }}
  .stat .lbl {{ font-size: 9px; color: var(--dim); font-family: var(--mono); margin-top: 2px; }}
  .card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 10px; }}
  .card:hover {{ border-color: var(--border2); }}
  .score-ring {{ width: 48px; height: 48px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-family: var(--mono); font-size: 14px; font-weight: 700; flex-shrink: 0; }}
  .badge {{ display: inline-block; font-size: 9px; letter-spacing: 1px; padding: 2px 7px; border-radius: 20px; font-family: var(--mono); white-space: nowrap; }}
  .new-badge {{ background: rgba(192,160,96,0.15); border: 1px solid rgba(192,160,96,0.4); color: var(--gold); }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
  .grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
  .filter-bar {{ display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }}
  .filter-bar select, .filter-bar input {{
    background: var(--bg2); border: 1px solid var(--border2); border-radius: 6px;
    color: var(--muted); padding: 5px 10px; font-size: 11px; font-family: var(--mono);
  }}
  .detail-panel {{ background: var(--bg3); border: 1px solid var(--border2); border-radius: 8px; padding: 14px; margin-top: 10px; display: none; }}
  .detail-panel.open {{ display: block; }}
  .pipeline-table {{ width: 100%; border-collapse: collapse; }}
  .pipeline-table th {{ font-size: 9px; color: var(--dim); font-family: var(--mono); letter-spacing: 2px; text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  .pipeline-table td {{ font-size: 12px; padding: 10px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  select.status-sel {{ background: var(--bg3); border: 1px solid var(--border2); border-radius: 4px; color: var(--text); padding: 2px 5px; font-size: 10px; font-family: var(--mono); }}
  .company-card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  .progress-bar {{ background: var(--bg3); border-radius: 4px; height: 6px; margin-top: 4px; }}
  .progress-fill {{ height: 6px; border-radius: 4px; transition: width 0.3s; }}
  @media (max-width: 600px) {{ .grid2 {{ grid-template-columns: 1fr; }} .grid3 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="sub">SP CAREER INTELLIGENCE · PEDRO PEREIRA</div>
    <h1>São Paulo Job Tracker</h1>
    <div class="updated">Last scanned: {last_updated} · Auto-updates daily via GitHub Actions</div>
  </div>

  <div class="stats" id="stats-bar"></div>

  <div class="tabs">
    <button class="tab active" onclick="switchTab('jobs')">JOBS FOUND</button>
    <button class="tab" onclick="switchTab('pipeline')">MY PIPELINE</button>
    <button class="tab" onclick="switchTab('companies')">COMPANIES</button>
    <button class="tab" onclick="switchTab('alerts')">SETUP GUIDE</button>
  </div>

  <!-- JOBS -->
  <div id="tab-jobs" class="section active">
    <div class="filter-bar">
      <select id="filter-company" onchange="renderJobs()">
        <option value="">All companies</option>
      </select>
      <select id="filter-score" onchange="renderJobs()">
        <option value="0">All scores</option>
        <option value="70">Strong matches (70+)</option>
        <option value="50">Good matches (50+)</option>
      </select>
      <select id="filter-verdict" onchange="renderJobs()">
        <option value="">All verdicts</option>
        <option value="Strong Match">Strong Match</option>
        <option value="Good Match">Good Match</option>
        <option value="Partial Match">Partial Match</option>
      </select>
      <input type="text" id="filter-search" placeholder="Search title..." oninput="renderJobs()" style="flex:1; min-width:140px;">
    </div>
    <div id="jobs-list"></div>
  </div>

  <!-- PIPELINE -->
  <div id="tab-pipeline" class="section">
    <div style="display:flex; justify-content:flex-end; margin-bottom:12px;">
      <button onclick="addPipelineEntry()" style="background:rgba(78,203,160,0.1); border:1px solid rgba(78,203,160,0.3); color:var(--green); padding:6px 16px; border-radius:7px; cursor:pointer; font-size:10px; font-family:var(--mono);">+ ADD ROLE</button>
    </div>
    <div style="overflow-x:auto;">
      <table class="pipeline-table" id="pipeline-table">
        <thead><tr>
          <th>SCORE</th><th>COMPANY</th><th>ROLE</th><th>STATUS</th><th>CONTACT</th><th>NOTES</th><th>DATE</th><th></th>
        </tr></thead>
        <tbody id="pipeline-body"></tbody>
      </table>
    </div>
  </div>

  <!-- COMPANIES -->
  <div id="tab-companies" class="section">
    <div id="companies-grid" class="grid2"></div>
  </div>

  <!-- ALERTS / SETUP -->
  <div id="tab-alerts" class="section">
    <div class="card" style="margin-bottom:14px;">
      <div style="font-size:9px; color:var(--dim); letter-spacing:2px; font-family:var(--mono); margin-bottom:14px;">LINKEDIN JOB ALERTS TO SET UP NOW</div>
      <div id="alert-list"></div>
    </div>
    <div class="card">
      <div style="font-size:9px; color:var(--dim); letter-spacing:2px; font-family:var(--mono); margin-bottom:14px;">GOOGLE ALERTS</div>
      <div id="google-alert-list"></div>
    </div>
    <div class="card" style="margin-top:14px; background:rgba(13,21,32,0.8); border-color:rgba(96,144,192,0.2);">
      <div style="font-size:9px; color:var(--blue); letter-spacing:2px; font-family:var(--mono); margin-bottom:14px;">GITHUB SETUP INSTRUCTIONS</div>
      <div style="font-size:11px; color:#4a6a80; line-height:1.8;">
        <b style="color:#6090c0;">1. Fork / clone this repo to your GitHub account</b><br>
        2. Go to Settings → Secrets → Actions<br>
        3. Add secret: <code style="background:#0d1520; padding:1px 5px; border-radius:3px;">ANTHROPIC_API_KEY</code> = your Anthropic API key<br>
        4. Go to Settings → Pages → Source: <code style="background:#0d1520; padding:1px 5px; border-radius:3px;">main branch / docs folder</code><br>
        5. The workflow runs daily at 7am UTC automatically<br>
        6. To run manually: Actions tab → Daily Job Scan → Run workflow<br><br>
        <b style="color:#6090c0;">Your dashboard URL will be:</b><br>
        <code style="background:#0d1520; padding:2px 8px; border-radius:4px; color:var(--green);">https://YOUR-USERNAME.github.io/sp-job-tracker/</code>
      </div>
    </div>
  </div>
</div>

<script>
const JOBS = {jobs_json};
const COMPANIES_META = {companies_json};
const STATUS_COLORS = {{
  "Monitoring":"#555","Applied":"#60b4f0","First Contact":"#4ecba0",
  "Screening":"#f0a060","Interview":"#c060f0","Offer":"#4ecba0","Rejected":"#ff6b6b","On Hold":"#888"
}};
const STATUS_OPTIONS = ["Monitoring","Applied","First Contact","Screening","Interview","Offer","Rejected","On Hold"];

// Pipeline stored in localStorage
let pipeline = JSON.parse(localStorage.getItem('sp_pipeline') || '[]');
if (!pipeline.length) {{
  pipeline = [
    {{ id:'p1', company:'BTG Pactual', role:'Head of Technology Delivery', status:'Monitoring', contact:'', notes:'Target #1', date:'2026-05-20', score: null }},
    {{ id:'p2', company:'XP Investimentos', role:'Technology Program Director', status:'Monitoring', contact:'', notes:'Strong AI/tech culture match', date:'2026-05-20', score: null }},
    {{ id:'p3', company:'Pátria Investimentos', role:'Senior Program Manager', status:'Monitoring', contact:'', notes:'PE/alts expansion needs tech ops', date:'2026-05-20', score: null }},
  ];
  savePipeline();
}}

function savePipeline() {{ localStorage.setItem('sp_pipeline', JSON.stringify(pipeline)); }}

function scoreColor(s) {{
  if (!s && s !== 0) return '#444';
  return s >= 70 ? '#4ecba0' : s >= 50 ? '#f0a060' : '#ff6b6b';
}}

function scoreRing(s) {{
  const c = scoreColor(s);
  const val = (s !== null && s !== undefined) ? s : '—';
  return `<div class="score-ring" style="border:3px solid ${{c}}; background:${{c}}11; color:${{c}}">${{val}}</div>`;
}}

function badge(text, color) {{
  return `<span class="badge" style="background:${{color}}22; border:1px solid ${{color}}55; color:${{color}}">${{text}}</span>`;
}}

// ── Stats bar ──
function renderStats() {{
  const total = JOBS.length;
  const strong = JOBS.filter(j => j.score?.score >= 70).length;
  const good = JOBS.filter(j => j.score?.score >= 50 && j.score?.score < 70).length;
  const isNew = JOBS.filter(j => j.is_new).length;
  document.getElementById('stats-bar').innerHTML = [
    ['Total Roles', total, '#888'],
    ['Strong Match', strong, '#4ecba0'],
    ['Good Match', good, '#f0a060'],
    ['New Today', isNew, '#c0a060'],
    ['Pipeline', pipeline.length, '#60b4f0'],
  ].map(([l,v,c]) => `<div class="stat"><div class="val" style="color:${{c}}">${{v}}</div><div class="lbl">${{l}}</div></div>`).join('');
}}

// ── Jobs ──
function renderJobs() {{
  const companyFilter = document.getElementById('filter-company').value;
  const scoreFilter = parseInt(document.getElementById('filter-score').value);
  const verdictFilter = document.getElementById('filter-verdict').value;
  const search = document.getElementById('filter-search').value.toLowerCase();

  // Populate company filter
  const sel = document.getElementById('filter-company');
  if (sel.options.length === 1) {{
    [...new Set(JOBS.map(j => j.company))].sort().forEach(c => {{
      const o = document.createElement('option'); o.value = c; o.textContent = c; sel.appendChild(o);
    }});
  }}

  const filtered = JOBS.filter(j => {{
    if (companyFilter && j.company !== companyFilter) return false;
    if (scoreFilter && (j.score?.score || 0) < scoreFilter) return false;
    if (verdictFilter && j.score?.verdict !== verdictFilter) return false;
    if (search && !j.title.toLowerCase().includes(search) && !j.company.toLowerCase().includes(search)) return false;
    return true;
  }});

  if (!filtered.length) {{
    document.getElementById('jobs-list').innerHTML = '<div style="text-align:center; color:var(--dim); padding:40px; font-family:var(--mono); font-size:11px;">No roles match your filters.<br>The scanner runs daily — check back tomorrow.</div>';
    return;
  }}

  document.getElementById('jobs-list').innerHTML = filtered.map(j => {{
    const s = j.score || {{}};
    const sc = s.score;
    const col = scoreColor(sc);
    return `
    <div class="card" style="border-color:${{col}}22">
      <div style="display:flex; gap:12px; align-items:flex-start">
        ${{scoreRing(sc)}}
        <div style="flex:1">
          <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:4px">
            ${{j.is_new ? '<span class="badge new-badge">NEW</span>' : ''}}
            ${{s.verdict ? badge(s.verdict, col) : ''}}
            ${{badge(j.company, '#888')}}
          </div>
          <div style="font-size:14px; color:var(--text); margin-bottom:2px">${{j.title}}</div>
          <div style="font-size:11px; color:var(--muted); margin-bottom:8px">${{j.location}} · Found ${{j.found_at?.slice(0,10) || ''}}</div>
          <div style="display:flex; gap:6px; flex-wrap:wrap">
            <a href="${{j.url}}" target="_blank" style="color:var(--blue); font-size:10px; font-family:var(--mono)">View role →</a>
            <button onclick="toggleDetail('${{j.id}}')" style="background:none; border:1px solid var(--border2); color:var(--dim); padding:2px 8px; border-radius:4px; cursor:pointer; font-size:10px; font-family:var(--mono)">Details</button>
            <button onclick="addToPipeline('${{j.id}}')" style="background:none; border:1px solid rgba(78,203,160,0.3); color:var(--green); padding:2px 8px; border-radius:4px; cursor:pointer; font-size:10px; font-family:var(--mono)">+ Pipeline</button>
          </div>
        </div>
      </div>
      <div class="detail-panel" id="detail-${{j.id}}">
        ${{s.topReasons?.length ? `<div style="margin-bottom:10px"><div style="font-size:9px; color:var(--green); letter-spacing:2px; font-family:var(--mono); margin-bottom:6px">WHY YOU FIT</div>${{s.topReasons.map(r => `<div style="font-size:11px; color:#4ecba088; margin-bottom:4px">✓ ${{r}}</div>`).join('')}}</div>` : ''}}
        ${{s.gaps?.length ? `<div style="margin-bottom:10px"><div style="font-size:9px; color:var(--red); letter-spacing:2px; font-family:var(--mono); margin-bottom:6px">GAPS</div>${{s.gaps.map(g => `<div style="font-size:11px; color:#ff6b6b88; margin-bottom:4px">✗ ${{g}}</div>`).join('')}}</div>` : ''}}
        ${{s.talkingPoints?.length ? `<div style="margin-bottom:10px"><div style="font-size:9px; color:var(--gold); letter-spacing:2px; font-family:var(--mono); margin-bottom:6px">TALKING POINTS</div>${{s.talkingPoints.map(t => `<div style="font-size:11px; color:#c0a06088; margin-bottom:4px">→ ${{t}}</div>`).join('')}}</div>` : ''}}
        ${{s.suggestedContact ? `<div style="font-size:10px; color:var(--blue); font-family:var(--mono)">Contact to find: ${{s.suggestedContact}}</div>` : ''}}
      </div>
    </div>`;
  }}).join('');
}}

function toggleDetail(id) {{
  const el = document.getElementById('detail-' + id);
  el.classList.toggle('open');
}}

function addToPipeline(jobId) {{
  const j = JOBS.find(x => x.id === jobId);
  if (!j) return;
  if (pipeline.find(p => p.id === jobId)) {{ alert('Already in pipeline.'); return; }}
  pipeline.push({{
    id: jobId, company: j.company, role: j.title,
    status: 'Monitoring', contact: j.score?.suggestedContact || '',
    notes: j.score?.verdict ? `Score ${{j.score.score}}/100 · ${{j.score.verdict}}` : '',
    date: new Date().toISOString().slice(0,10), score: j.score?.score || null, url: j.url
  }});
  savePipeline();
  renderPipeline();
  renderStats();
  alert('Added to pipeline!');
}}

// ── Pipeline ──
function renderPipeline() {{
  const tbody = document.getElementById('pipeline-body');
  tbody.innerHTML = pipeline.map((e,i) => `
    <tr>
      <td>${{e.score !== null && e.score !== undefined ? `<span style="color:${{scoreColor(e.score)}}; font-family:var(--mono); font-weight:700">${{e.score}}</span>` : '<span style="color:var(--dim)">—</span>'}}</td>
      <td style="color:var(--muted)">${{e.company}}</td>
      <td><div style="color:var(--text)">${{e.role}}</div>
          ${{e.url ? `<a href="${{e.url}}" target="_blank" style="font-size:9px; color:var(--blue); font-family:var(--mono)">View →</a>` : ''}}</td>
      <td><select class="status-sel" onchange="updatePipeline(${{i}},'status',this.value)" style="color:${{STATUS_COLORS[e.status] || '#888'}}">${{STATUS_OPTIONS.map(s => `<option value="${{s}}" ${{e.status===s?'selected':''}}>${{s}}</option>`).join('')}}</select></td>
      <td><input value="${{e.contact||''}}" onchange="updatePipeline(${{i}},'contact',this.value)" placeholder="Key contact..." style="background:var(--bg3); border:1px solid var(--border2); border-radius:4px; color:var(--muted); padding:2px 6px; font-size:10px; font-family:var(--mono); width:120px;"></td>
      <td><input value="${{e.notes||''}}" onchange="updatePipeline(${{i}},'notes',this.value)" placeholder="Notes..." style="background:var(--bg3); border:1px solid var(--border2); border-radius:4px; color:var(--muted); padding:2px 6px; font-size:10px; font-family:var(--mono); width:160px;"></td>
      <td style="color:var(--dim); font-family:var(--mono); font-size:10px">${{e.date}}</td>
      <td><button onclick="removePipeline(${{i}})" style="background:none; border:none; color:#663333; cursor:pointer; font-size:14px">×</button></td>
    </tr>
  `).join('');
}}

function updatePipeline(i, field, val) {{
  pipeline[i][field] = val;
  savePipeline();
  renderPipeline();
}}

function removePipeline(i) {{
  pipeline.splice(i, 1);
  savePipeline();
  renderPipeline();
  renderStats();
}}

function addPipelineEntry() {{
  const company = prompt('Company name:');
  const role = prompt('Role title:');
  if (!company || !role) return;
  pipeline.push({{ id:'m'+Date.now(), company, role, status:'Monitoring', contact:'', notes:'', date:new Date().toISOString().slice(0,10), score:null }});
  savePipeline();
  renderPipeline();
  renderStats();
}}

// ── Companies ──
function renderCompanies() {{
  document.getElementById('companies-grid').innerHTML = COMPANIES_META.map(c => {{
    const companyJobs = JOBS.filter(j => j.company === c.name);
    const topScore = companyJobs.length ? Math.max(...companyJobs.map(j => j.score?.score || 0)) : null;
    const col = c.tier === 1 ? '#4ecba0' : '#888';
    return `
    <div class="company-card" style="border-color:${{col}}22">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px">
        <div>
          <div style="font-size:14px; color:${{col}}; font-weight:600; margin-bottom:2px">${{c.name}}</div>
          <div style="font-size:10px; color:var(--dim); font-family:var(--mono)">${{c.sector}}</div>
        </div>
        ${{badge('Tier ' + c.tier, col)}}
      </div>
      <div style="font-size:11px; color:#666; line-height:1.6; margin-bottom:10px">${{c.why}}</div>
      <div style="font-size:10px; color:var(--dim); font-family:var(--mono); margin-bottom:8px">
        ${{companyJobs.length}} role${{companyJobs.length !== 1 ? 's' : ''}} found
        ${{topScore ? `· Best match: <span style="color:${{scoreColor(topScore)}}">${{topScore}}/100</span>` : ''}}
      </div>
      <a href="${{c.careers_url}}" target="_blank" style="font-size:10px; color:var(--blue); font-family:var(--mono)">Careers page →</a>
    </div>`;
  }}).join('');
}}

// ── Alerts ──
function renderAlerts() {{
  const linkedinAlerts = [
    'Technology Program Director São Paulo',
    'Head Technology Delivery BTG Pactual',
    'Senior Program Manager Nubank São Paulo',
    'Regulatory Technology Director Itaú',
    'Technology Director Pátria Investimentos',
    'Program Manager XP Investimentos',
    'Digital Transformation Director São Paulo financial services',
  ];
  const googleAlerts = [
    'BTG Pactual technology director hiring 2026',
    'XP Investimentos senior technology program manager',
    'Nubank head technology delivery São Paulo',
    'Pátria Investimentos technology transformation',
  ];

  document.getElementById('alert-list').innerHTML = linkedinAlerts.map(q => `
    <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border)">
      <span style="font-size:11px; color:var(--muted); font-family:var(--mono)">"${{q}}"</span>
      <a href="https://www.linkedin.com/jobs/search/?keywords=${{encodeURIComponent(q)}}&location=S%C3%A3o+Paulo" target="_blank" style="font-size:9px; color:var(--blue); font-family:var(--mono); white-space:nowrap; margin-left:8px">Search →</a>
    </div>
  `).join('');

  document.getElementById('google-alert-list').innerHTML = googleAlerts.map(q => `
    <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border)">
      <span style="font-size:11px; color:var(--muted); font-family:var(--mono)">"${{q}}"</span>
      <a href="https://www.google.com/alerts#create:${{encodeURIComponent(q)}}" target="_blank" style="font-size:9px; color:var(--orange); font-family:var(--mono); white-space:nowrap; margin-left:8px">Create →</a>
    </div>
  `).join('');
}}

// ── Tab switching ──
function switchTab(name) {{
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}}

// ── Init ──
renderStats();
renderJobs();
renderPipeline();
renderCompanies();
renderAlerts();
</script>
</body>
</html>"""


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"SP Job Tracker — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=api_key) if api_key else None

    data_path = "docs/data.json"
    existing = load_existing(data_path)

    all_new_jobs = []
    for company in COMPANIES:
        print(f"Scanning {company['name']}...")
        scrape_fn = scrape_gupy if company.get("scrape_type") == "gupy" else scrape_greenhouse
        jobs = scrape_fn(company)
        print(f"  Found {len(jobs)} relevant roles")
        all_new_jobs.extend(jobs)
        time.sleep(1)

    merged = merge_jobs(existing.get("jobs", []), all_new_jobs)

    # Score any job that doesn't have a score yet
    if client:
        to_score = [j for j in merged if not j.get("score") and j.get("description")]
        print(f"Scoring {len(to_score)} new roles...")
        for job in to_score[:20]:  # cap at 20 per run to manage API costs
            print(f"  Scoring: {job['title']} @ {job['company']}")
            job["score"] = score_role(client, job)
            time.sleep(0.5)
    else:
        print("No ANTHROPIC_API_KEY — skipping scoring")

    data = {
        "jobs": merged,
        "pipeline": existing.get("pipeline", []),
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    os.makedirs("docs", exist_ok=True)
    with open(data_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(merged)} total jobs to {data_path}")

    html = generate_html(data)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Generated docs/index.html")
    print("Done.")


if __name__ == "__main__":
    main()
