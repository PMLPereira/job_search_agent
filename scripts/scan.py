"""
SP Job Tracker - Daily Scanner v3
Scrapes target company career pages, scores roles against Pedro's CV,
extracts salary/skills/seniority, and generates an enhanced dashboard.
"""

import os, json, time, hashlib, re, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import anthropic

# ── PEDRO'S PROFILE (used by GitHub Actions — kept in sync with CV manually) ─
PEDRO_PROFILE = """
Name: Pedro Miguel Lourenco Pereira
Email: pedro.canario.beta@gmail.com
LinkedIn: linkedin.com/in/pedrolourencopereira

CURRENT ROLE
Technology Manager / Delivery Lead — Bank of America, Global Markets Technology, London (2021–present)
- Lead cross-functional delivery of regulatory technology programs across Global Markets
- Managed $20M+ Fed reporting / BACEN regulatory compliance program end-to-end
- P&L accountability: $5M+ technology budget, vendor contracts, resource planning
- Stakeholder management: C-suite, Legal, Risk, Compliance, Operations, Engineering
- Delivered FRTB, Fed Reporting, and data governance initiatives on time and within budget
- Team leadership: 15+ engineers, PMs, and BAs across London, New York, and Charlotte

PREVIOUS EXPERIENCE
Principal Consultant — Capco Financial Services, London (2014–2021, 7 years)
- Led technology transformation programs at Tier 1 investment banks and asset managers
- Delivered regulatory (MiFID II, EMIR, Dodd-Frank), digital, and data programs
- Client-facing at Director level: HSBC, Standard Chartered, UBS, Lloyds Banking Group
- Designed operating models, target architectures, and programme governance frameworks
- $3M+ project P&L managed as principal

SKILLS
Delivery & Governance: Program management, PMO, portfolio management, agile/scrum, waterfall, PRINCE2, SAFe
Technology: Python, SQL, data pipelines, cloud (Google Cloud), AI/ML, LLMs, prompt engineering
Automation: Make.com, Zapier, n8n, process automation
Domains: Capital markets, investment banking, asset management, regulatory compliance, risk management, trading systems, fintech
Leadership: Cross-functional teams, stakeholder management, budget accountability, vendor management
Data: Data governance, data quality, BCBS 239, regulatory reporting (FRTB, BACEN, Fed)

EDUCATION
MSc Management — Robert Gordon University, Aberdeen, UK
MSc Finance/Management — NOVA School of Business and Economics, Lisbon, Portugal

CERTIFICATIONS
Google Cloud: AI, Data Engineering; Advanced ML (Cambridge Spark)
Project Management: Agile, Scrum Master, PRINCE2 Practitioner

LANGUAGES
Portuguese: Native
English: Fluent (C2)

TARGET
Role: Director / Head of Technology Delivery / Head of Technology / Senior Program Manager
Location: Sao Paulo, Brazil (relocating from London mid-2027)
Sector: Investment banking, asset management, fintech, financial services
Salary: R$35,000-50,000 take-home/month
"""

COMPANIES = [
    {"name":"BTG Pactual",       "tier":1,"sector":"Investment Banking",  "color":"#4ecba0","careers_url":"https://carreiras.btgpactual.com/vagas","scrape_type":"btg",                                   "why":"Fastest growing IB in Brazil. Hire international profiles aggressively.","interview":"Case study + behavioural. Strong emphasis on delivery metrics and stakeholder management."},
    {"name":"XP Investimentos",  "tier":1,"sector":"Financial Services",  "color":"#60b4f0","careers_url":"https://boards.greenhouse.io/xpinc",    "scrape_type":"greenhouse",                             "why":"Tech-forward platform growing institutional markets.","interview":"Technical screen + culture fit. Values autonomy and data-driven decisions."},
    {"name":"Nubank",            "tier":1,"sector":"Fintech",             "color":"#c060f0","careers_url":"https://boards.greenhouse.io/nubank",    "scrape_type":"greenhouse",                             "why":"Building institutional/B2B products. Capital markets background rare here.","interview":"4-stage process: recruiter, hiring manager, case study, exec. Very structured."},
    {"name":"Itaú BBA",          "tier":1,"sector":"Investment Banking",  "color":"#f0a060","careers_url":"https://vemproitau.gupy.io/",            "scrape_type":"gupy","gupy_company":"vemproitau",       "why":"Your BofA regulatory work maps directly to their transformation agenda.","interview":"Competency-based. Focus on regulatory knowledge and large program delivery."},
    {"name":"Pátria Investimentos","tier":1,"sector":"Alternative Assets","color":"#e8a030","careers_url":"https://patriainvestimentos.gupy.io/",   "scrape_type":"gupy","gupy_company":"patriainvestimentos","why":"Growing fast into infrastructure/PE. Technology ops transformation needed.","interview":"Two rounds: technical + partner. Boutique feel, relationship-driven."},
    {"name":"Vinci Partners",    "tier":1,"sector":"Asset Management",    "color":"#a0d0ff","careers_url":"https://vincipartners.gupy.io/",         "scrape_type":"gupy","gupy_company":"vincipartners",    "why":"Nasdaq-listed. Scaling tech as AUM grows. Strong fit for your profile.","interview":"Lean process. Direct access to senior leadership early."},
    {"name":"Kinea Investimentos","tier":2,"sector":"Alternative Assets", "color":"#80c0e0","careers_url":"https://kinea.gupy.io/",                 "scrape_type":"gupy","gupy_company":"kinea",            "why":"Itaú group alt asset manager. Significant tech investment underway.","interview":"Formal. Multi-round. Similar to Itaú process."},
    {"name":"Bradesco BBI",      "tier":2,"sector":"Investment Banking",  "color":"#f06080","careers_url":"https://banco.bradesco/trabalheconosco/","scrape_type":"apify","apify_org":"BANCO BRADESCO SA","apify_domain":"banco.bradesco","why":"Traditional bank modernising. Capco consulting background fits perfectly.","interview":"HR screen + technical + senior leadership. Traditional bank process."},
    {"name":"Santander Brasil",  "tier":2,"sector":"Banking",            "color":"#ff8060","careers_url":"https://santander.wd3.myworkdayjobs.com/pt-BR/SantanderCareers","scrape_type":"apify","apify_org":"Santander","apify_ats":["workday"],"apify_domain":"santanderbank.com","why":"Large operation, active technology transformation. Regulatory profile fits.","interview":"Structured HR process. Focus on leadership competencies."},
    {"name":"Stone / StoneCo",   "tier":2,"sector":"Fintech/Payments",   "color":"#60d0a0","careers_url":"https://boards.greenhouse.io/stone",     "scrape_type":"greenhouse",                             "why":"Serious technology scale. International profile welcome.","interview":"Fast-paced. Case study heavy. Values execution speed."},
    {"name":"Warren Investimentos","tier":2,"sector":"Fintech",          "color":"#d0a060","careers_url":"https://warrenbrasil.gupy.io/",          "scrape_type":"gupy","gupy_company":"warrenbrasil",     "why":"Tech-first investment platform scaling rapidly.","interview":"Startup culture. Values builder mindset and ownership."},
    {"name":"Avenue Securities", "tier":2,"sector":"Brokerage",          "color":"#c0a0ff","careers_url":"https://avenue.gupy.io/",                "scrape_type":"gupy","gupy_company":"avenue",           "why":"Brazilian-American brokerage. Bilingual + capital markets = perfect fit.","interview":"Relaxed culture. Values bilingual profiles strongly."},
    {"name":"Oliver Wyman SP",   "tier":2,"sector":"Consulting",         "color":"#e0e060","careers_url":"https://boards.greenhouse.io/oliverwyman","scrape_type":"greenhouse",                           "why":"Capco pedigree transfers directly. Financial services practice very active.","interview":"Case study mandatory. McKinsey-style structured interviews."},
]

RELEVANT_KEYWORDS = [
    "technology","tecnologia","programa","program","delivery","entrega",
    "gerente","manager","diretor","director","head","lider","líder",
    "transformação","transformation","operações","operations","produto",
    "product","dados","data","regulatory","regulatório","fintech",
    "capital markets","mercados","estratégia","strategy","senior","sênior",
    "principal","agile","scrum","pmo","portfolio","plataforma","platform",
]

SALARY_BENCHMARKS = {
    "Director":       {"min":35000,"max":55000,"bonus":"20-40%"},
    "Head of":        {"min":40000,"max":60000,"bonus":"30-50%"},
    "Senior Manager": {"min":28000,"max":42000,"bonus":"15-25%"},
    "Program Manager":{"min":22000,"max":35000,"bonus":"10-20%"},
    "Senior":         {"min":20000,"max":32000,"bonus":"10-15%"},
    "Default":        {"min":18000,"max":30000,"bonus":"10-15%"},
}

HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36","Accept":"text/html,application/json"}


def load_cv_text():
    """Read CV text from .docx in application_docs/. Returns None on failure."""
    try:
        from docx import Document as DocxDoc
        path = "application_docs/CV_Pedro Pereira 2026 v1.0.docx"
        doc = DocxDoc(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:5000]
    except Exception as e:
        print(f"  CV load failed ({e}), using fallback profile")
        return None


def scrape_gupy(company):
    """Gupy migrated to SSR — jobs embedded in __NEXT_DATA__."""
    slug = company.get("gupy_company","")
    url  = f"https://{slug}.gupy.io/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"  Gupy {company['name']}: HTTP {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "lxml")
        nd_tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if not nd_tag:
            print(f"  Gupy {company['name']}: no __NEXT_DATA__ tag found")
            return []
        nd         = json.loads(nd_tag.string)
        page_props = nd.get("props",{}).get("pageProps",{})
        subdomain  = page_props.get("subdomain", slug)
        jobs = (
            page_props.get("jobs") or
            page_props.get("jobOpportunities") or
            page_props.get("opportunities") or
            page_props.get("jobList") or []
        )
        if isinstance(jobs, dict):
            jobs = jobs.get("data", jobs.get("items", []))
        print(f"  Gupy {company['name']}: {len(jobs)} raw jobs")
        results = []
        for j in jobs:
            title    = j.get("title","") or j.get("name","")
            wp       = j.get("workplace") or {}
            location = wp.get("city","") or wp.get("state","") or "São Paulo"
            loc_l    = location.lower()
            if not any(x in loc_l for x in ["são paulo","sao paulo","remote","remoto"]):
                continue
            job_id   = j.get("id","")
            job_url  = f"https://{subdomain}.gupy.io/jobs/{job_id}" if job_id else company["careers_url"]
            desc     = j.get("description","") or j.get("responsibilities","") or ""
            if not is_relevant(title + " " + desc):
                continue
            results.append({
                "id":        hashlib.md5(f"{company['name']}{title}".encode()).hexdigest()[:12],
                "company":   company["name"],
                "title":     title,
                "location":  location,
                "url":       job_url,
                "description": desc[:4000],
                "work_type": j.get("type","") or j.get("workplaceType",""),
                "found_at":  datetime.now(timezone.utc).isoformat(),
                "is_new":    True,
            })
        return results
    except Exception as e:
        print(f"  Gupy error {company['name']}: {e}"); return []


def scrape_greenhouse(company):
    board = company["careers_url"].split("greenhouse.io/")[-1].strip("/")
    url   = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200: return []
        jobs, results = r.json().get("jobs",[]), []
        for j in jobs:
            title    = j.get("title","")
            location = (j.get("location") or {}).get("name","") or ""
            loc_l    = location.lower()
            if location and not any(x in loc_l for x in ["são paulo","sao paulo","remote","remoto"]): continue
            desc = BeautifulSoup(j.get("content",""),"lxml").get_text(" ")[:4000]
            if not is_relevant(title+" "+desc): continue
            results.append({
                "id":       hashlib.md5(f"{company['name']}{title}".encode()).hexdigest()[:12],
                "company":  company["name"],
                "title":    title,
                "location": location or "São Paulo",
                "url":      j.get("absolute_url", company["careers_url"]),
                "description": desc,
                "work_type":"",
                "found_at": datetime.now(timezone.utc).isoformat(),
                "is_new":   True,
            })
        return results
    except Exception as e:
        print(f"  Greenhouse error {company['name']}: {e}"); return []


def _apify_payload(companies):
    org_names, ats_filters = [], []
    for c in companies:
        org_names.append(c.get("apify_org", c["name"]))
        ats_filters.extend(c.get("apify_ats", []))
    payload = {
        "timeRange": "7d", "limit": 100,
        "organizationSearch": org_names,
        "locationSearch": ["São Paulo, São Paulo, Brazil"],
        "includeAi": False, "descriptionType": "text",
    }
    if ats_filters:
        payload["ats"] = list(set(ats_filters))
    return payload


def start_apify_run(api_key, companies):
    """Fire off an async Apify run; return (run_id, dataset_id) or (None, None)."""
    try:
        r = requests.post(
            "https://api.apify.com/v2/acts/fantastic-jobs~career-site-job-listing-api/runs",
            json=_apify_payload(companies),
            params={"token": api_key},
            timeout=30,
        )
        if r.status_code not in (200, 201):
            print(f"  Apify start error: HTTP {r.status_code} — {r.text[:200]}")
            return None, None
        run = r.json().get("data", {})
        run_id     = run.get("id")
        dataset_id = run.get("defaultDatasetId")
        print(f"  Apify run started: {run_id}")
        return run_id, dataset_id
    except Exception as e:
        print(f"  Apify start error: {e}")
        return None, None


def collect_apify_results(api_key, run_id, dataset_id, companies, timeout=240):
    """Poll until the Apify run finishes, then fetch and parse the dataset."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r      = requests.get(f"https://api.apify.com/v2/runs/{run_id}",
                                  params={"token": api_key}, timeout=15)
            status = r.json().get("data", {}).get("status", "")
            print(f"  Apify status: {status}")
            if status == "SUCCEEDED":
                break
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                print(f"  Apify run ended with status {status}")
                return {c["name"]: [] for c in companies}
        except Exception as e:
            print(f"  Apify poll error: {e}")
        time.sleep(15)
    else:
        print(f"  Apify run did not finish within {timeout}s — skipping")
        return {c["name"]: [] for c in companies}

    try:
        r     = requests.get(f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                             params={"token": api_key}, timeout=30)
        items = r.json()
    except Exception as e:
        print(f"  Apify dataset fetch error: {e}")
        return {c["name"]: [] for c in companies}

    results = {c["name"]: [] for c in companies}
    for item in items:
        org    = (item.get("organization") or "").upper()
        domain = (item.get("domain_derived") or "")
        title  = item.get("title", "")
        desc   = item.get("description_text", "") or ""
        if not is_relevant(title + " " + desc):
            continue
        loc     = (item.get("locations_derived") or [""])[0]
        for c in companies:
            apify_org    = c.get("apify_org", c["name"]).upper()
            apify_domain = c.get("apify_domain", "")
            if apify_org in org or (apify_domain and apify_domain in domain):
                results[c["name"]].append({
                    "id":          hashlib.md5(f"{c['name']}{title}".encode()).hexdigest()[:12],
                    "company":     c["name"],
                    "title":       title,
                    "location":    loc or "São Paulo",
                    "url":         item.get("url", c["careers_url"]),
                    "description": desc[:4000],
                    "work_type":   "",
                    "found_at":    datetime.now(timezone.utc).isoformat(),
                    "is_new":      True,
                })
                break
    return results


def scrape_btg(company):
    """BTG Pactual custom Angular portal — try known API patterns."""
    endpoints = [
        "https://carreiras.btgpactual.com/api/v1/jobs",
        "https://carreiras.btgpactual.com/api/jobs",
        "https://api.btgpactual.com/careers/jobs",
    ]
    for ep in endpoints:
        try:
            r = requests.get(ep, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                data = r.json()
                jobs = data if isinstance(data, list) else data.get("jobs", data.get("data", []))
                results = []
                for j in jobs:
                    title = j.get("title","") or j.get("name","")
                    if not title or not is_relevant(title): continue
                    results.append({
                        "id":          hashlib.md5(f"BTG{title}".encode()).hexdigest()[:12],
                        "company":     company["name"],
                        "title":       title,
                        "location":    j.get("location","São Paulo"),
                        "url":         j.get("url", company["careers_url"]),
                        "description": str(j.get("description",""))[:4000],
                        "work_type":   "",
                        "found_at":    datetime.now(timezone.utc).isoformat(),
                        "is_new":      True,
                    })
                if results: return results
        except Exception:
            continue
    print(f"  BTG Pactual: custom portal not API-accessible — check manually at carreiras.btgpactual.com")
    return []


def is_relevant(text):
    t = text.lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


def estimate_salary(title):
    t = title.lower()
    for key, band in SALARY_BENCHMARKS.items():
        if key.lower() in t:
            return band
    return SALARY_BENCHMARKS["Default"]


def score_role(client, job, cv_text=None):
    candidate = cv_text or PEDRO_PROFILE
    prompt = f"""You are a senior executive recruiter specialising in financial services technology leadership in Brazil.

Analyse the candidate vs the job posting. Return ONLY valid JSON — no markdown, no commentary.

{{
  "score": <integer 0-100, must equal sum of scoreBreakdown fields>,
  "scoreBreakdown": {{"skills": <0-40>, "seniority": <0-30>, "sector": <0-20>, "language": <0-10>}},
  "verdict": "<Strong Match|Good Match|Partial Match|Weak Match>",
  "topReasons": ["<reason 1>","<reason 2>","<reason 3>"],
  "gaps": ["<gap 1>","<gap 2>"],
  "gapActions": ["<action 1>","<action 2>"],
  "talkingPoints": ["<point 1>","<point 2>","<point 3>"],
  "suggestedContact": "<job title to find, e.g. Head of Technology Recruiting>",
  "keySkillsRequired": ["<skill 1>","<skill 2>","<skill 3>","<skill 4>","<skill 5>"],
  "skillsYouHave": ["<skills from keySkillsRequired that candidate has>"],
  "skillsYouLack": ["<skills from keySkillsRequired that candidate lacks>"],
  "salaryRange": "<e.g. R$35,000-45,000/month estimated>",
  "seniorityLevel": "<Junior|Mid|Senior|Director|Head|C-Level>",
  "yearsExpRequired": "<e.g. 8-12 years>",
  "languagesRequired": ["Portuguese","English"],
  "workArrangement": "<Remote|Hybrid|On-site>",
  "applyRecommendation": "<Yes|Yes with tweaks|No>",
  "outreachTemplate": "<2-sentence LinkedIn outreach — address as the role title from suggestedContact, NEVER use literal [Name] placeholder>",
  "coverLetterPoints": ["<point 1 tying candidate experience to this role>","<point 2>","<point 3>","<point 4>"],
  "atsKeywords": {{"match": ["<keyword in CV>"], "missing": ["<keyword not in CV>"]}},
  "cvTweaks": ["<specific bullet in CV to reword for this application>","<tweak 2>"],
  "interviewQuestions": [
    {{"q": "<likely question 1>", "hint": "<STAR answer hint from candidate experience>"}},
    {{"q": "<likely question 2>", "hint": "<STAR hint>"}},
    {{"q": "<likely question 3>", "hint": "<STAR hint>"}}
  ]
}}

SCORING CALIBRATION:
- skills (0-40): match between candidate's technical/delivery skills and role requirements
- seniority (0-30): 30=exact Director/Head/VP/C-Level match, 20=one level off, 5=IC/Specialist role
- sector (0-20): 20=capital markets/investment banking exact match, 15=fintech/financial services, 8=other
- language (0-10): 10=both Portuguese+English required and candidate has both, 5=one required

Score thresholds:
- 70-100 Strong Match: Director/Head+ level, strong skill+sector fit — Pedro should prioritise these
- 50-69 Good Match: Near right level, minor gaps — worth applying with tweaks
- 30-49 Partial Match: Significant seniority or sector gap
- 0-29 Weak Match: IC/Specialist/Analyst roles — cap score at 30 regardless of skills

CRITICAL RULES:
- Do NOT penalise for relocation — candidate actively plans to move to São Paulo by 2027
- Do NOT penalise for Portuguese — he is a native speaker
- Do NOT score low because "overqualified" — he is targeting these companies deliberately
- If seniorityLevel is IC/Specialist/Analyst, seniority sub-score must be 5 or below

CANDIDATE CV:
{candidate}

JOB:
Company: {job['company']}
Title: {job['title']}
Location: {job['location']}
Description: {job['description'][:4000]}
"""
    try:
        msg  = client.messages.create(model="claude-sonnet-4-6", max_tokens=2000,
                                      messages=[{"role":"user","content":prompt}])
        text = msg.content[0].text.strip().replace("```json","").replace("```","")
        result = json.loads(text)
        # Ensure score matches breakdown sum if breakdown is present
        bd = result.get("scoreBreakdown", {})
        if bd:
            computed = sum(bd.get(k, 0) for k in ["skills","seniority","sector","language"])
            result["score"] = computed
        return result
    except Exception as e:
        print(f"  Scoring error {job['title']}: {e}")
        return {}


def load_existing(path):
    if os.path.exists(path):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                with open(path, encoding=enc) as f:
                    return json.load(f)
            except (UnicodeDecodeError, ValueError):
                continue
    return {"jobs":[],"pipeline":[],"last_updated":"","run_history":[]}


def merge_jobs(existing, new_jobs):
    existing_ids = {j["id"] for j in existing}
    merged = list(existing)
    for j in new_jobs:
        if j["id"] not in existing_ids:
            j["is_new"] = True
            merged.append(j)
        else:
            for e in merged:
                if e["id"] == j["id"]: e["is_new"] = False
    return merged


def prune_jobs(jobs, max_age_days=60):
    """Drop jobs older than max_age_days to prevent indefinite growth."""
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 86400)
    kept = []
    for j in jobs:
        try:
            ts = datetime.fromisoformat(j.get("found_at", "").replace("Z", "+00:00")).timestamp()
            if ts >= cutoff:
                kept.append(j)
        except Exception:
            kept.append(j)
    return kept


def generate_html(data):
    import json as _json

    # Filter jobs below score 30
    jobs = [
        j for j in (data.get("jobs") or [])
        if (j.get("score") or {}).get("score", 0) >= 30
    ]
    jobs_json = _json.dumps(jobs)
    scan_date = data.get("scan_date", data.get("last_updated", ""))
    total = len(jobs)
    strong = sum(1 for j in jobs if (j.get("score") or {}).get("score", 0) >= 65)
    good = sum(1 for j in jobs if 50 <= (j.get("score") or {}).get("score", 0) < 65)
    pipeline = len(data.get("pipeline", []))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoleIQ · Pedro · SP Job Intelligence</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#FAFAF8;--surface:#FFFFFF;--surface2:#F7F7F4;
  --border:#E8E8E4;--border2:#D8D8D2;
  --text:#0F0F14;--text-secondary:#3D3D4E;--text-muted:#6B6B80;--muted:#9090A0;
  --accent:#6366F1;--accent-hover:#4F46E5;--accent-soft:rgba(99,102,241,0.08);
  --green:#10B981;--green-soft:rgba(16,185,129,0.10);
  --red:#F43F5E;--red-soft:rgba(244,63,94,0.10);
  --amber:#F59E0B;--amber-soft:rgba(245,158,11,0.10);
  --purple:#8B5CF6;
  --font:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --radius:10px;--radius-sm:6px;
  --shadow-sm:0 1px 3px rgba(0,0,0,0.06),0 1px 2px rgba(0,0,0,0.04);
  --shadow:0 4px 16px rgba(0,0,0,0.08),0 1px 4px rgba(0,0,0,0.04);
}}
html,body{{font-family:var(--font);background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;height:100%;overflow:hidden}}
#app{{display:flex;flex-direction:column;height:100vh;overflow:hidden}}

/* TOP BAR */
#topbar{{background:#1C1C28;border-bottom:1px solid #2A2A3E;padding:0 24px;height:56px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;z-index:100}}
#brand{{display:flex;align-items:center;gap:10px}}
#logo{{width:32px;height:32px;background:linear-gradient(135deg,#6366F1,#8B5CF6);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:800;color:#fff;letter-spacing:-0.5px;box-shadow:0 2px 8px rgba(99,102,241,0.35);flex-shrink:0}}
#brand-name{{font-size:15px;font-weight:700;color:#FFFFFF;letter-spacing:-0.3px}}
#brand-sub{{font-size:11px;color:#6B6B88;font-weight:400}}
#kpis{{display:flex;align-items:center;gap:4px}}
.kpi{{display:flex;flex-direction:column;align-items:center;padding:6px 14px;border-radius:8px;cursor:pointer;transition:background 0.15s;min-width:70px}}
.kpi:hover{{background:rgba(255,255,255,0.07)}}
.kpi-val{{font-size:17px;font-weight:700;color:#FFFFFF;line-height:1}}
.kpi-val.g{{color:#10B981}}.kpi-val.a{{color:#818CF8}}.kpi-val.amber{{color:#F59E0B}}
.kpi-label{{font-size:10px;color:#6B6B88;font-weight:500;margin-top:2px;text-transform:uppercase;letter-spacing:0.05em}}
.kpi-div{{width:1px;height:28px;background:#2A2A3E;margin:0 4px}}
#pipeline-btn{{background:linear-gradient(135deg,#6366F1,#4F46E5);color:#fff;border:none;border-radius:8px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 2px 10px rgba(99,102,241,0.35);transition:all 0.2s;font-family:var(--font)}}
#pipeline-btn:hover{{transform:translateY(-1px);box-shadow:0 4px 16px rgba(99,102,241,0.45)}}
#scan-info{{font-size:10.5px;color:#4A4A60;padding:6px 24px;background:#16161F;border-bottom:1px solid #2A2A3E;text-align:center}}
#scan-info a{{color:#6366F1;text-decoration:none}}

/* SUBNAV */
#subnav{{background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 24px;gap:0;height:44px;flex-shrink:0}}
.nav-tab{{padding:0 16px;height:44px;display:flex;align-items:center;font-size:13px;font-weight:500;color:var(--text-muted);cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s;white-space:nowrap;user-select:none}}
.nav-tab:hover{{color:var(--text-secondary)}}
.nav-tab.active{{color:var(--accent);font-weight:600;border-bottom-color:var(--accent)}}

/* FILTER BAR */
#filterbar{{background:var(--surface2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;gap:8px;align-items:center;flex-shrink:0;flex-wrap:wrap}}
.f-select{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);padding:7px 10px;font-size:12.5px;font-family:var(--font);color:var(--text-secondary);cursor:pointer;outline:none;transition:border-color 0.15s;min-width:130px}}
.f-select:focus{{border-color:var(--accent)}}
#search{{flex:1;min-width:200px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);padding:7px 12px 7px 32px;font-size:12.5px;font-family:var(--font);color:var(--text);outline:none;transition:border-color 0.15s;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%239090A0' stroke-width='2'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='m21 21-4.35-4.35'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:10px center}}
#search:focus{{border-color:var(--accent)}}
#search::placeholder{{color:var(--muted)}}
#result-count{{font-size:12px;color:var(--muted);white-space:nowrap}}

/* MAIN */
#main{{display:flex;flex:1;overflow:hidden;min-height:0}}

/* LIST PANEL */
#list-panel{{width:360px;flex-shrink:0;border-right:1px solid var(--border);overflow-y:auto;background:var(--bg)}}
#list-panel::-webkit-scrollbar{{width:4px}}
#list-panel::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:2px}}
#list-header{{padding:10px 16px;font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;border-bottom:1px solid var(--border);background:var(--surface);position:sticky;top:0;z-index:10}}
.jc{{padding:13px 16px;border-bottom:1px solid var(--border);cursor:pointer;transition:background 0.12s;display:flex;gap:12px;align-items:flex-start;background:var(--surface)}}
.jc:hover{{background:#F4F4FF}}
.jc.sel{{background:var(--accent-soft);border-left:3px solid var(--accent)}}
.jc.sel .jc-title{{color:var(--accent)}}
.sr{{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;flex-shrink:0;border:2.5px solid}}
.sr.s{{border-color:#10B981;background:rgba(16,185,129,0.10);color:#059669}}
.sr.g{{border-color:#6366F1;background:rgba(99,102,241,0.10);color:#6366F1}}
.sr.p{{border-color:#F59E0B;background:rgba(245,158,11,0.10);color:#B45309}}
.sr.l{{border-color:#F43F5E;background:rgba(244,63,94,0.08);color:#BE123C}}
.jc-info{{flex:1;min-width:0}}
.jc-co{{font-size:10px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.10em;margin-bottom:2px}}
.jc-title{{font-size:13px;font-weight:600;color:var(--text);line-height:1.3;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.jc-meta{{display:flex;gap:6px;align-items:center;flex-wrap:wrap}}
.jc-sal{{font-size:11px;color:var(--text-secondary);font-weight:500}}
.vb{{font-size:10px;font-weight:600;padding:2px 7px;border-radius:99px;letter-spacing:0.02em}}
.vb.s{{background:rgba(16,185,129,0.12);color:#059669}}
.vb.g{{background:rgba(99,102,241,0.10);color:#6366F1}}
.vb.p{{background:rgba(245,158,11,0.10);color:#B45309}}
.vb.l{{background:rgba(244,63,94,0.08);color:#BE123C}}
.wt{{font-size:10px;color:var(--muted);background:var(--surface2);padding:2px 6px;border-radius:4px;border:1px solid var(--border)}}

/* DETAIL PANEL */
#detail{{flex:1;overflow-y:auto;padding:28px 32px;background:var(--bg);min-width:0}}
#detail::-webkit-scrollbar{{width:4px}}
#detail::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:2px}}
#empty{{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--muted);gap:12px;font-size:14px}}

/* DETAIL CONTENT */
.d-co-row{{display:flex;align-items:center;gap:8px;margin-bottom:6px}}
.d-co{{font-size:11px;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:0.12em}}
.d-snr{{font-size:11px;color:var(--muted);background:var(--surface2);padding:2px 7px;border-radius:4px;border:1px solid var(--border)}}
.d-title{{font-size:22px;font-weight:800;color:var(--text);line-height:1.2;letter-spacing:-0.03em;margin-bottom:10px}}
.d-meta{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}}
.d-meta-i{{font-size:12.5px;color:var(--text-secondary);display:flex;align-items:center;gap:5px}}
.d-actions{{display:flex;gap:10px;align-items:center;padding-top:12px;border-top:1px solid var(--border);margin-bottom:24px}}
.btn-p{{background:linear-gradient(135deg,#6366F1,#4F46E5);color:#fff;border:none;border-radius:8px;padding:9px 18px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 2px 10px rgba(99,102,241,0.30);transition:all 0.2s;font-family:var(--font);text-decoration:none;display:inline-flex;align-items:center;gap:5px}}
.btn-p:hover{{transform:translateY(-1px);box-shadow:0 4px 16px rgba(99,102,241,0.40)}}
.btn-s{{background:var(--surface);color:var(--text-secondary);border:1px solid var(--border);border-radius:8px;padding:9px 18px;font-size:13px;font-weight:500;cursor:pointer;transition:all 0.15s;font-family:var(--font)}}
.btn-s:hover{{border-color:var(--accent);color:var(--accent)}}

/* SCORE SECTION */
.sc{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px;box-shadow:var(--shadow-sm)}}
.sc-lbl{{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.10em;margin-bottom:14px}}
.sc-hero{{display:flex;align-items:center;gap:20px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--border)}}
.sc-ring{{width:68px;height:68px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:24px;font-weight:800;flex-shrink:0;border:3px solid;letter-spacing:-1px}}
.sc-ring.s{{border-color:#10B981;background:rgba(16,185,129,0.08);color:#059669}}
.sc-ring.g{{border-color:#6366F1;background:rgba(99,102,241,0.08);color:#6366F1}}
.sc-ring.p{{border-color:#F59E0B;background:rgba(245,158,11,0.08);color:#B45309}}
.sc-ring.l{{border-color:#F43F5E;background:rgba(244,63,94,0.06);color:#BE123C}}
.sc-verdict{{font-size:16px;font-weight:700;color:var(--text);margin-bottom:4px}}
.sc-apply{{font-size:12px;color:var(--text-muted)}}
.sc-apply strong{{color:var(--text-secondary)}}

/* SCORE BARS */
.bars{{display:flex;flex-direction:column;gap:8px}}
.bar-i{{display:flex;flex-direction:column;gap:3px}}
.bar-frac{{font-size:11px;font-weight:500;color:var(--muted);text-align:right;line-height:1}}
.bar-track{{height:5px;border-radius:6px;background:#F0F0EC;overflow:hidden}}
.bar-fill{{height:5px;border-radius:6px;transition:width 0.6s cubic-bezier(0.4,0,0.2,1)}}

/* TWO COL */
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow-sm)}}
.card.gt{{border-color:rgba(16,185,129,0.20);background:rgba(16,185,129,0.03)}}
.card.rt{{border-color:rgba(244,63,94,0.15);background:rgba(244,63,94,0.025)}}
.card-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px}}
.card-title.green{{color:#059669}}.card-title.red{{color:#BE123C}}.card-title.accent{{color:var(--accent)}}.card-title.muted{{color:var(--muted)}}
.li{{font-size:12.5px;color:var(--text-secondary);line-height:1.5;padding:5px 0;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:flex-start}}
.li:last-child{{border-bottom:none}}
.dot{{width:5px;height:5px;border-radius:50%;flex-shrink:0;margin-top:6px}}
.dot.g{{background:#10B981}}.dot.r{{background:#F43F5E}}.dot.a{{background:var(--accent)}}

/* CHIPS */
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}
.chip{{font-size:11.5px;padding:3px 10px;border-radius:99px;font-weight:500}}
.chip.h{{background:rgba(16,185,129,0.12);color:#059669;border:1px solid rgba(16,185,129,0.20)}}
.chip.lk{{background:rgba(244,63,94,0.08);color:#BE123C;border:1px solid rgba(244,63,94,0.15)}}

/* TALKING POINTS */
.tp{{border-left:3px solid var(--accent);padding:8px 12px;margin-bottom:8px;font-size:12.5px;color:var(--text-secondary);line-height:1.55;background:var(--accent-soft);border-radius:0 6px 6px 0}}
.tp:last-child{{margin-bottom:0}}

/* OUTREACH */
.outreach{{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 16px;font-size:12.5px;color:var(--text-secondary);line-height:1.6;font-style:italic;position:relative}}
.copy-btn{{position:absolute;top:10px;right:10px;background:var(--surface);border:1px solid var(--border);border-radius:5px;padding:4px 10px;font-size:11px;font-weight:600;color:var(--accent);cursor:pointer;font-family:var(--font);transition:all 0.15s}}
.copy-btn:hover{{background:var(--accent);color:#fff;border-color:var(--accent)}}

/* FULL SECTION */
.fs{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow-sm);margin-bottom:14px}}
.sec-sub{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:5px}}
.sec-sub.g{{color:#059669}}.sec-sub.r{{color:#BE123C}}

/* INTERVIEW */
.iq{{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 14px;margin-bottom:8px}}
.iq:last-child{{margin-bottom:0}}
.iq-q{{font-size:13px;font-weight:600;color:var(--text);margin-bottom:6px;line-height:1.4}}
.iq-hint{{font-size:12px;color:var(--text-muted);line-height:1.5}}

/* COVER LETTER */
.cl-point{{font-size:12.5px;color:var(--text-secondary);line-height:1.55;padding:6px 0;border-bottom:1px solid var(--border);display:flex;gap:8px}}
.cl-point:last-child{{border-bottom:none}}
</style>
</head>
<body>
<div id="app">
  <div id="topbar">
    <div id="brand">
      <div id="logo">RI</div>
      <div>
        <div id="brand-name">RoleIQ</div>
        <div id="brand-sub">Pedro · São Paulo</div>
      </div>
    </div>
    <div id="kpis">
      <div class="kpi"><div class="kpi-val">{total}</div><div class="kpi-label">Total</div></div>
      <div class="kpi-div"></div>
      <div class="kpi"><div class="kpi-val g">{strong}</div><div class="kpi-label">Strong</div></div>
      <div class="kpi"><div class="kpi-val a">{good}</div><div class="kpi-label">Good</div></div>
      <div class="kpi-div"></div>
      <div class="kpi"><div class="kpi-val amber">0</div><div class="kpi-label">New Today</div></div>
      <div class="kpi-div"></div>
      <div class="kpi"><div class="kpi-val">{pipeline}</div><div class="kpi-label">Pipeline</div></div>
    </div>
    <button id="pipeline-btn">+ Pipeline</button>
  </div>
  <div id="scan-info">Last scanned: {scan_date} · Auto-updates daily via GitHub Actions · <a href="https://github.com/PMLPereira/job_search_agent" target="_blank">GitHub →</a></div>
  <div id="subnav">
    <div class="nav-tab active" onclick="showTab('jobs')">Jobs Found</div>
    <div class="nav-tab" onclick="showTab('intel')">Market Intel</div>
    <div class="nav-tab" onclick="showTab('pipeline')">My Pipeline</div>
    <div class="nav-tab" onclick="showTab('companies')">Companies</div>
    <div class="nav-tab" onclick="showTab('remote')">Remote 🌍</div>
    <div class="nav-tab" onclick="showTab('setup')">Setup</div>
  </div>
  <div id="filterbar">
    <select class="f-select" id="f-score" onchange="renderList()">
      <option value="0">All Scores</option>
      <option value="65">Strong (65+)</option>
      <option value="50">Good (50+)</option>
      <option value="30">Partial (30+)</option>
    </select>
    <select class="f-select" id="f-co" onchange="renderList()">
      <option value="">All Companies</option>
    </select>
    <select class="f-select" id="f-type" onchange="renderList()">
      <option value="">Any Arrangement</option>
      <option value="Remote">Remote</option>
      <option value="Hybrid">Hybrid</option>
      <option value="On-site">On-site</option>
    </select>
    <select class="f-select" id="f-sort" onchange="renderList()">
      <option value="score">Sort: Best Match</option>
      <option value="newest">Sort: Newest</option>
    </select>
    <input type="text" id="search" placeholder="Search title, skill, keyword..." oninput="renderList()">
    <span id="result-count"></span>
  </div>
  <div id="main">
    <div id="list-panel">
      <div id="list-header">0 Roles</div>
      <div id="cards"></div>
    </div>
    <div id="detail">
      <div id="empty">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/></svg>
        <p>Select a role to see the full AI analysis</p>
      </div>
      <div id="detail-content" style="display:none"></div>
    </div>
  </div>
</div>

<script>
const JOBS = {jobs_json};

function sc(score) {{
  if (score >= 65) return 's';
  if (score >= 50) return 'g';
  if (score >= 35) return 'p';
  return 'l';
}}
function vl(score) {{
  if (score >= 65) return 'Strong Match';
  if (score >= 50) return 'Good Match';
  if (score >= 35) return 'Partial Match';
  return 'Weak Match';
}}
function bc(pct) {{
  if (pct >= 0.70) return '#10B981';
  if (pct >= 0.45) return '#6366F1';
  if (pct >= 0.25) return '#F59E0B';
  return '#F43F5E';
}}
function esc(s) {{
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// Populate company filter
const cos = [...new Set(JOBS.map(j => j.company).filter(Boolean))].sort();
const coSel = document.getElementById('f-co');
cos.forEach(c => {{ const o = document.createElement('option'); o.value = c; o.textContent = c; coSel.appendChild(o); }});

let selectedIdx = -1;

function renderList() {{
  const minScore = parseInt(document.getElementById('f-score').value) || 0;
  const filterCo = document.getElementById('f-co').value;
  const filterType = document.getElementById('f-type').value.toLowerCase();
  const sortBy = document.getElementById('f-sort').value;
  const search = document.getElementById('search').value.toLowerCase();

  let filtered = JOBS.filter((j, i) => {{
    const s = (j.score||{{}}).score || 0;
    if (s < minScore) return false;
    if (filterCo && j.company !== filterCo) return false;
    const wt = (j.work_type || (j.score||{{}}).workArrangement || '').toLowerCase();
    if (filterType && !wt.includes(filterType)) return false;
    if (search) {{
      const hay = ((j.title||'') + ' ' + (j.company||'') + ' ' + ((j.score||{{}}).keySkillsRequired||[]).join(' ')).toLowerCase();
      if (!hay.includes(search)) return false;
    }}
    return true;
  }});

  if (sortBy === 'score') filtered.sort((a,b) => ((b.score||{{}}).score||0) - ((a.score||{{}}).score||0));
  else if (sortBy === 'newest') filtered.sort((a,b) => new Date(b.found_at||0) - new Date(a.found_at||0));

  document.getElementById('list-header').textContent = filtered.length + ' Roles';
  document.getElementById('result-count').textContent = filtered.length + ' of ' + JOBS.length;

  document.getElementById('cards').innerHTML = filtered.map(job => {{
    const origIdx = JOBS.indexOf(job);
    const s = (job.score||{{}}).score || 0;
    const cls = sc(s);
    const sal = ((job.score||{{}}).salaryRange||'').replace('/month estimated','').replace(' estimated','');
    const wt = job.work_type || (job.score||{{}}).workArrangement || '';
    const sel = origIdx === selectedIdx ? ' sel' : '';
    return `<div class="jc${{sel}}" onclick="selectJob(${{origIdx}})">
      <div class="sr ${{cls}}">${{s}}</div>
      <div class="jc-info">
        <div class="jc-co">${{esc(job.company)}}</div>
        <div class="jc-title" title="${{esc(job.title)}}">${{esc(job.title)}}</div>
        <div class="jc-meta">
          <span class="jc-sal">${{esc(sal)}}</span>
          <span class="vb ${{cls}}">${{vl(s)}}</span>
          ${{wt ? `<span class="wt">${{esc(wt)}}</span>` : ''}}
        </div>
      </div>
    </div>`;
  }}).join('');
}}

function selectJob(origIdx) {{
  selectedIdx = origIdx;
  document.querySelectorAll('.jc').forEach(c => c.classList.remove('sel'));
  const card = document.querySelector(`.jc[onclick="selectJob(${{origIdx}})"]`);
  if (card) card.classList.add('sel');

  const job = JOBS[origIdx];
  const sc2 = job.score || {{}};
  const score = sc2.score || 0;
  const cls = sc(score);
  const bd = sc2.scoreBreakdown || {{}};

  const bars = [
    {{k:'skills', v:bd.skills||0, max:40}},
    {{k:'seniority', v:bd.seniority||0, max:30}},
    {{k:'sector', v:bd.sector||0, max:20}},
    {{k:'language', v:bd.language||0, max:10}}
  ].map(b => {{
    const pct = b.v / b.max;
    const col = bc(pct);
    return `<div class="bar-i"><div class="bar-frac">${{b.v}}/${{b.max}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.round(pct*100)}}%;background:${{col}}"></div></div></div>`;
  }}).join('');

  const reasons = (sc2.topReasons||[]).map(r => `<div class="li"><div class="dot g"></div><div>${{esc(r)}}</div></div>`).join('');
  const gaps = (sc2.gaps||[]).map(g => `<div class="li"><div class="dot r"></div><div>${{esc(g)}}</div></div>`).join('');
  const haveChips = (sc2.skillsYouHave||[]).map(s => `<span class="chip h">${{esc(s)}}</span>`).join('');
  const lackChips = (sc2.skillsYouLack||[]).map(s => `<span class="chip lk">${{esc(s)}}</span>`).join('');
  const tps = (sc2.talkingPoints||[]).map(t => `<div class="tp">${{esc(t)}}</div>`).join('');
  const actions = (sc2.gapActions||[]).map(a => `<div class="li"><div class="dot a"></div><div>${{esc(a)}}</div></div>`).join('');
  const tweaks = (sc2.cvTweaks||[]).map(t => `<div class="li"><div class="dot a"></div><div>${{esc(t)}}</div></div>`).join('');
  const clPts = (sc2.coverLetterPoints||[]).map(p => `<div class="cl-point"><div class="dot a" style="margin-top:5px"></div><div>${{esc(p)}}</div></div>`).join('');
  const iqs = (sc2.interviewQuestions||[]).map(q => `<div class="iq"><div class="iq-q">${{esc(q.q||'')}}</div><div class="iq-hint"><strong>Hint:</strong> ${{esc(q.hint||'')}}</div></div>`).join('');
  const outreach = esc(sc2.outreachTemplate||'');
  const sal = (sc2.salaryRange||'').replace(' estimated','');
  const wt = job.work_type || sc2.workArrangement || '—';
  const yrs = sc2.yearsExpRequired || '—';
  const langs = (sc2.languagesRequired||[]).join(', ') || '—';
  const loc = (job.location||'').split(';')[0];
  const applyRec = sc2.applyRecommendation || 'No';

  const html = `
    <div class="d-co-row"><span class="d-co">${{esc(job.company)}}</span><span class="d-snr">${{esc(sc2.seniorityLevel||'')}}</span></div>
    <div class="d-title">${{esc(job.title)}}</div>
    <div class="d-meta">
      <div class="d-meta-i">💰 ${{esc(sal)}}</div>
      <div class="d-meta-i">📍 ${{esc(loc)}}</div>
      <div class="d-meta-i">🗓 ${{esc(yrs)}}</div>
      <div class="d-meta-i">🌐 ${{esc(langs)}}</div>
      <div class="d-meta-i">💼 ${{esc(wt)}}</div>
    </div>
    <div class="d-actions">
      <a class="btn-p" href="${{esc(job.url||'#')}}" target="_blank" rel="noopener">View Role →</a>
      <button class="btn-s" onclick="addPipeline(${{origIdx}})">+ Pipeline</button>
      <button class="btn-s" onclick="copyCL(${{origIdx}})">Copy CL</button>
    </div>

    <div class="sc">
      <div class="sc-lbl">AI Match Score</div>
      <div class="sc-hero">
        <div class="sc-ring ${{cls}}">${{score}}</div>
        <div>
          <div class="sc-verdict">${{vl(score)}}</div>
          <div class="sc-apply">Apply recommendation: <strong>${{applyRec}}</strong></div>
        </div>
      </div>
      <div class="bars">${{bars}}</div>
    </div>

    <div class="two-col">
      <div class="card gt">
        <div class="card-title green">✓ Why You Fit</div>
        ${{reasons || '<div class="li"><div class="dot g"></div><div>No specific fit points listed</div></div>'}}
      </div>
      <div class="card rt">
        <div class="card-title red">⚠ Gaps</div>
        ${{gaps || '<div class="li"><div class="dot r"></div><div>No major gaps identified</div></div>'}}
      </div>
    </div>

    ${{(haveChips || lackChips) ? `
    <div class="fs">
      <div class="card-title muted" style="margin-bottom:10px">Skills</div>
      ${{haveChips ? `<div style="margin-bottom:10px"><div class="sec-sub g">You Have</div><div class="chips">${{haveChips}}</div></div>` : ''}}
      ${{lackChips ? `<div><div class="sec-sub r">You Lack</div><div class="chips">${{lackChips}}</div></div>` : ''}}
    </div>` : ''}}

    ${{tps ? `
    <div class="fs">
      <div class="card-title accent" style="margin-bottom:12px">Talking Points</div>
      ${{tps}}
    </div>` : ''}}

    ${{actions ? `
    <div class="fs">
      <div class="card-title accent" style="margin-bottom:10px">Actions</div>
      ${{actions}}
    </div>` : ''}}

    ${{tweaks ? `
    <div class="fs">
      <div class="card-title accent" style="margin-bottom:10px">CV Tweaks</div>
      ${{tweaks}}
    </div>` : ''}}

    ${{clPts ? `
    <div class="fs">
      <div class="card-title accent" style="margin-bottom:10px">Cover Letter Points</div>
      ${{clPts}}
    </div>` : ''}}

    ${{iqs ? `
    <div class="fs">
      <div class="card-title accent" style="margin-bottom:10px">Interview Prep</div>
      ${{iqs}}
    </div>` : ''}}

    ${{outreach ? `
    <div class="fs">
      <div class="card-title accent" style="margin-bottom:10px">LinkedIn Outreach</div>
      <div class="outreach" id="outreach-${{origIdx}}">
        <button class="copy-btn" onclick="copyOutreach(${{origIdx}})">Copy</button>
        ${{outreach}}
      </div>
    </div>` : ''}}
  `;

  document.getElementById('empty').style.display = 'none';
  const dc = document.getElementById('detail-content');
  dc.style.display = 'block';
  dc.innerHTML = html;
  document.getElementById('detail').scrollTop = 0;
}}

function addPipeline(idx) {{
  const job = JOBS[idx];
  alert('Added to pipeline: ' + (job.title||'') + ' at ' + (job.company||''));
}}

function copyCL(idx) {{
  const job = JOBS[idx];
  const pts = ((job.score||{{}}).coverLetterPoints||[]).join('\\n\\n');
  if (pts) {{
    navigator.clipboard.writeText(pts).then(() => alert('Cover letter points copied!')).catch(() => alert('Copy failed — please copy manually'));
  }} else {{
    alert('No cover letter points available for this role');
  }}
}}

function copyOutreach(idx) {{
  const job = JOBS[idx];
  const txt = (job.score||{{}}).outreachTemplate || '';
  if (txt) {{
    navigator.clipboard.writeText(txt).then(() => alert('Outreach template copied!')).catch(() => alert('Copy failed'));
  }}
}}

function showTab(tab) {{
  document.querySelectorAll('.nav-tab').forEach((t,i) => {{
    const tabs = ['jobs','intel','pipeline','companies','remote','setup'];
    t.classList.toggle('active', tabs[i] === tab);
  }});
}}

// Init
renderList();
if (JOBS.length > 0) {{
  setTimeout(() => selectJob(0), 50);
}}
</script>
</body>
</html>"""

    return html


def main():
    print(f"SP Job Tracker v3 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    client  = anthropic.Anthropic(api_key=api_key) if api_key else None
    cv_text = load_cv_text()

    # Profile hash — triggers re-scoring of recent jobs when CV changes
    profile_hash = hashlib.md5((cv_text or PEDRO_PROFILE).encode()).hexdigest()[:8]

    data_path = "docs/data.json"
    existing  = load_existing(data_path)

    old_hash = existing.get("profile_hash","")
    if old_hash and old_hash != profile_hash:
        cutoff = datetime.now(timezone.utc).timestamp() - (45 * 86400)
        cleared = 0
        for j in existing.get("jobs", []):
            try:
                jts = datetime.fromisoformat(j.get("found_at","").replace("Z","+00:00")).timestamp()
                if jts > cutoff:
                    j.pop("score", None)
                    cleared += 1
            except Exception:
                pass
        print(f"Profile changed ({old_hash} → {profile_hash}) — cleared scores for {cleared} recent jobs")

    all_new = []

    # ── Apify: fire async run BEFORE scraping other sites ──────────────────
    apify_cos  = [c for c in COMPANIES if c.get("scrape_type") == "apify"]
    apify_run_id = apify_dataset_id = None
    api_key_apify = os.environ.get("APIFY_API_KEY", "")
    if apify_cos and api_key_apify:
        print(f"Apify async start: {[c['name'] for c in apify_cos]}")
        apify_run_id, apify_dataset_id = start_apify_run(api_key_apify, apify_cos)
    elif apify_cos:
        print("  APIFY_API_KEY not set — skipping Apify companies")

    # ── Scrape non-Apify companies while Apify runs in the cloud ───────────
    SCRAPER_FNS = {"gupy": scrape_gupy, "greenhouse": scrape_greenhouse, "btg": scrape_btg}
    for company in COMPANIES:
        st = company.get("scrape_type", "greenhouse")
        if st == "apify":
            continue
        print(f"Scanning {company['name']}...")
        fn   = SCRAPER_FNS.get(st, scrape_greenhouse)
        jobs = fn(company)
        print(f"  {len(jobs)} relevant roles")
        all_new.extend(jobs)
        time.sleep(1)

    # ── Collect Apify results (polls until done or timeout) ─────────────────
    if apify_run_id and apify_dataset_id:
        apify_results = collect_apify_results(api_key_apify, apify_run_id, apify_dataset_id, apify_cos)
        for c in apify_cos:
            jobs = apify_results.get(c["name"], [])
            print(f"  {c['name']}: {len(jobs)} relevant roles")
            all_new.extend(jobs)

    merged = prune_jobs(merge_jobs(existing.get("jobs",[]), all_new))
    print(f"Jobs after merge + 60-day prune: {len(merged)}")

    # ── Score unscored jobs in parallel (5 threads) ─────────────────────────
    if client:
        to_score = [j for j in merged if not j.get("score") and j.get("description")]
        cap      = min(len(to_score), 50)
        print(f"Scoring {cap} unscored roles (5 threads)...")

        def _score_one(job):
            result = score_role(client, job, cv_text)
            time.sleep(0.3)
            return job, result

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_score_one, job): job for job in to_score[:cap]}
            for future in as_completed(futures):
                job, result = future.result()
                job["score"] = result
                print(f"  ✓ {job['title']} @ {job['company']}")

    history = existing.get("run_history",[])
    history.append({"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "count": len(merged)})
    history = history[-60:]

    data = {
        "jobs":         merged,
        "pipeline":     existing.get("pipeline",[]),
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "run_history":  history,
        "profile_hash": profile_hash,
    }

    os.makedirs("docs", exist_ok=True)
    with open(data_path,"w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(merged)} jobs · profile hash {profile_hash}")

    with open("docs/index.html","w",encoding="utf-8") as f:
        f.write(generate_html(data))
    print("Dashboard written to docs/index.html")


if __name__ == "__main__":
    main()
