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

# ── PEDRO'S PROFILE (fallback if CV file unavailable) ──────────────────────
PEDRO_PROFILE = """
Name: Pedro Miguel Lourenco Pereira
Current: Technology Manager / Delivery Lead - Bank of America Global Markets, London
Previous: Principal Consultant - Capco Financial Services (7 years)

Strengths:
- 12+ years senior program/technology delivery in global capital markets
- $20M regulatory compliance program (BofA/Capco), $5M+ P&L accountability
- Cross-functional leadership: Product, Engineering, Risk, Operations, Compliance
- Regulatory: Fed reporting, BACEN, risk management, data governance
- AI & automation: Python, SQL, LLMs, prompt engineering, Make, Zapier
- Languages: Portuguese (native), English (fluent)
- Education: MSc Management (Robert Gordon UK), MSc (NOVA Portugal)
- Certs: Google Cloud AI, Data Science, Advanced ML (Cambridge Spark)

Target: Director / Head of Technology Delivery / Senior Program Manager in São Paulo
Target salary: R$35,000–50,000 take-home/month
Available: mid-2027 (relocating from London)
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


def generate_html(data):
    jobs_json       = json.dumps(data["jobs"],           ensure_ascii=False).replace("</", "<\\/")
    companies_json  = json.dumps(COMPANIES,               ensure_ascii=False).replace("</", "<\\/")
    history_json    = json.dumps(data.get("run_history",[]), ensure_ascii=False).replace("</", "<\\/")
    last_updated    = data.get("last_updated","—")
    total_jobs      = len(data["jobs"])
    new_today       = sum(1 for j in data["jobs"] if j.get("is_new"))
    strong_matches  = sum(1 for j in data["jobs"] if (j.get("score") or {}).get("score",0) >= 65)

    all_skills = {}
    for j in data["jobs"]:
        for sk in (j.get("score") or {}).get("keySkillsRequired",[]):
            if sk and sk.strip():
                all_skills[sk] = all_skills.get(sk,0) + 1
    top_skills      = sorted(all_skills.items(), key=lambda x: -x[1])[:15]
    top_skills_json = json.dumps(top_skills).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pedro · SP Job Intelligence</title>
<style>
:root{{
  --bg:#f5f0e8;--bg2:#ede8df;--bg3:#e6e0d5;--bg4:#c2edda;
  --border:#c9c3b8;--border2:#b8b2a6;--border3:#a8a298;
  --text:#2c2420;--muted:#7a7068;--dim:#a09890;--dimmer:#b8b0a8;
  --green:#28a848;--gold:#d48000;--blue:#3a7cbf;
  --red:#c82e00;--purple:#7c5cbf;--orange:#c87840;
  --tint-green:rgba(104,211,136,.13);--tint-red:rgba(244,58,9,.09);
  --font:'Georgia',serif;--mono:'Courier New',monospace;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;padding:20px 14px 80px}}
.wrap{{max-width:1160px;margin:0 auto}}
.hdr{{text-align:center;margin-bottom:24px;padding-bottom:20px;border-bottom:1px solid var(--border)}}
.hdr h1{{font-size:24px;font-weight:400;letter-spacing:-0.5px}}
.hdr .sub{{font-size:9px;color:var(--dim);font-family:var(--mono);letter-spacing:4px;margin-bottom:8px}}
.hdr .upd{{font-size:10px;color:var(--dimmer);font-family:var(--mono)}}
.stats{{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:20px}}
.stat{{background:var(--bg2);border:1px solid var(--border);border-radius:9px;padding:10px 18px;text-align:center;min-width:90px}}
.stat .v{{font-size:26px;font-family:var(--mono);font-weight:700}}
.stat .l{{font-size:9px;color:var(--dim);font-family:var(--mono);margin-top:2px}}
.tabs{{display:flex;gap:5px;justify-content:center;margin-bottom:22px;flex-wrap:wrap}}
.tab{{padding:6px 15px;background:none;border:1px solid transparent;border-radius:7px;
      color:var(--dim);cursor:pointer;font-size:10px;font-family:var(--mono);letter-spacing:1px;transition:all .2s}}
.tab.active{{background:var(--bg2);border-color:var(--border2);color:var(--text)}}
.section{{display:none}}.section.active{{display:block}}
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:10px;transition:border-color .2s}}
.card:hover{{border-color:var(--border3)}}
.badge{{display:inline-block;font-size:9px;letter-spacing:1px;padding:2px 7px;border-radius:20px;font-family:var(--mono);white-space:nowrap}}
.ring{{width:50px;height:50px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:14px;font-weight:700;flex-shrink:0}}
.filters{{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center}}
.filters select,.filters input{{background:var(--bg2);border:1px solid var(--border2);border-radius:6px;
  color:var(--muted);padding:5px 10px;font-size:11px;font-family:var(--mono);cursor:pointer}}
.filters input{{flex:1;min-width:140px}}
.skill{{display:inline-block;background:var(--bg3);border:1px solid var(--border2);color:var(--muted);
  font-size:9px;padding:2px 8px;border-radius:20px;font-family:var(--mono);margin:2px}}
.skill.have{{background:var(--tint-green);border-color:rgba(40,168,72,.35);color:var(--green)}}
.skill.lack{{background:var(--tint-red);border-color:rgba(200,46,0,.3);color:var(--red)}}
.detail{{background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:14px;margin-top:10px;display:none}}
.detail.open{{display:block}}
.detail-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.dsec{{margin-top:12px}}
.dsec-title{{font-size:9px;letter-spacing:2px;font-family:var(--mono);margin-bottom:7px}}
.ptable{{width:100%;border-collapse:collapse}}
.ptable th{{font-size:9px;color:var(--dim);font-family:var(--mono);letter-spacing:2px;text-align:left;padding:8px 10px;border-bottom:1px solid var(--border)}}
.ptable td{{font-size:12px;padding:9px 10px;border-bottom:1px solid var(--border);vertical-align:middle}}
.psel{{background:var(--bg3);border:1px solid var(--border2);border-radius:4px;color:var(--text);padding:2px 5px;font-size:10px;font-family:var(--mono)}}
.pinput{{background:var(--bg3);border:1px solid var(--border2);border-radius:4px;color:var(--muted);padding:2px 6px;font-size:10px;font-family:var(--mono);width:100%}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.mbar{{background:var(--bg4);border-radius:4px;height:8px;margin-top:4px}}
.mfill{{height:8px;border-radius:4px;transition:width .4s}}
.outreach{{background:var(--bg3);border:1px solid var(--border2);border-radius:7px;padding:10px 12px;font-size:11px;color:var(--muted);font-family:var(--mono);line-height:1.6;margin-top:8px}}
.co-card{{cursor:pointer;transition:box-shadow .2s}}
.co-card:hover{{box-shadow:0 2px 12px rgba(44,36,32,.08)}}
.score-bd{{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}}
.score-bd-item{{text-align:center;min-width:48px}}
.score-bd-item .v{{font-size:18px;font-weight:700;font-family:var(--mono)}}
.score-bd-item .l{{font-size:8px;color:var(--dim);font-family:var(--mono)}}
.score-bd-item .max{{font-size:8px;color:var(--dimmer);font-family:var(--mono)}}
@media(max-width:640px){{.g2,.g3{{grid-template-columns:1fr}}.detail-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="wrap">

<div class="hdr">
  <div class="sub">SP CAREER INTELLIGENCE · PEDRO PEREIRA</div>
  <h1>🇧🇷 São Paulo Job Tracker</h1>
  <div class="upd">Last scanned: {last_updated} &nbsp;·&nbsp; Auto-updates daily via GitHub Actions &nbsp;·&nbsp; <a href="https://github.com/PMLPereira/job_search_agent" style="color:var(--blue);font-size:10px">GitHub →</a></div>
</div>

<div class="stats" id="stats-bar"></div>

<div class="tabs">
  <button class="tab active" onclick="sw('jobs',this)">JOBS FOUND</button>
  <button class="tab" onclick="sw('market',this)">MARKET INTEL</button>
  <button class="tab" onclick="sw('pipeline',this)">MY PIPELINE</button>
  <button class="tab" onclick="sw('companies',this)">COMPANIES</button>
  <button class="tab" onclick="sw('remote',this)">REMOTE 🌍</button>
  <button class="tab" onclick="sw('alerts',this)">SETUP</button>
</div>

<!-- ══ JOBS ══ -->
<div id="tab-jobs" class="section active">
  <div class="filters">
    <select id="f-company" onchange="renderJobs()"><option value="">All companies</option></select>
    <select id="f-score" onchange="renderJobs()">
      <option value="0">All scores</option>
      <option value="65">Strong (65+)</option>
      <option value="50">Good (50+)</option>
    </select>
    <select id="f-seniority" onchange="renderJobs()">
      <option value="">All levels</option>
      <option value="senior">Director+ only</option>
    </select>
    <select id="f-arrange" onchange="renderJobs()">
      <option value="">Any arrangement</option>
      <option value="Remote">Remote</option>
      <option value="Hybrid">Hybrid</option>
      <option value="On-site">On-site</option>
    </select>
    <select id="f-new" onchange="renderJobs()">
      <option value="">All roles</option>
      <option value="new">New today</option>
    </select>
    <select id="f-sort" onchange="renderJobs()">
      <option value="score">Sort: Best Match</option>
      <option value="new">Sort: Newest</option>
      <option value="salary">Sort: Salary</option>
    </select>
    <input id="f-search" placeholder="Search title, skill, keyword…" oninput="renderJobs()">
  </div>
  <div id="jobs-list"></div>
</div>

<!-- ══ MARKET INTEL ══ -->
<div id="tab-market" class="section">
  <div class="g2" style="margin-bottom:14px">
    <div class="card">
      <div style="font-size:9px;color:var(--gold);letter-spacing:2px;font-family:var(--mono);margin-bottom:14px">SALARY BENCHMARKS · SÃO PAULO 2026</div>
      <div id="salary-bands"></div>
    </div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="font-size:9px;color:var(--blue);letter-spacing:2px;font-family:var(--mono)">TOP SKILLS DEMANDED</div>
        <div style="font-size:9px;color:var(--dim);font-family:var(--mono)">
          <span style="color:var(--green)">● you have it</span>&nbsp;&nbsp;<span style="color:var(--red)">● gap</span>
        </div>
      </div>
      <div id="skills-heatmap"></div>
    </div>
  </div>
  <div class="card" style="margin-bottom:14px">
    <div style="font-size:9px;color:var(--green);letter-spacing:2px;font-family:var(--mono);margin-bottom:14px">SKILLS GAP · YOUR PROFILE VS MARKET</div>
    <div id="skills-gap"></div>
  </div>
  <div class="card">
    <div style="font-size:9px;color:var(--muted);letter-spacing:2px;font-family:var(--mono);margin-bottom:14px">SCAN HISTORY · ROLES FOUND PER DAY</div>
    <div id="scan-history"></div>
  </div>
</div>

<!-- ══ PIPELINE ══ -->
<div id="tab-pipeline" class="section">
  <div style="display:flex;justify-content:flex-end;margin-bottom:12px">
    <button onclick="addManual()" style="background:rgba(40,168,72,.1);border:1px solid rgba(40,168,72,.3);color:var(--green);padding:6px 16px;border-radius:7px;cursor:pointer;font-size:10px;font-family:var(--mono)">+ ADD MANUALLY</button>
  </div>
  <div style="overflow-x:auto">
    <table class="ptable">
      <thead><tr>
        <th>SCORE</th><th>COMPANY</th><th>ROLE</th><th>SALARY</th>
        <th>STATUS</th><th>NEXT ACTION</th><th>DUE DATE</th><th>CONTACT</th><th>NOTES</th><th></th>
      </tr></thead>
      <tbody id="ptbody"></tbody>
    </table>
  </div>
  <div style="margin-top:20px">
    <div style="font-size:9px;color:var(--dim);letter-spacing:2px;font-family:var(--mono);margin-bottom:12px">PIPELINE FUNNEL</div>
    <div id="funnel"></div>
  </div>
</div>

<!-- ══ COMPANIES ══ -->
<div id="tab-companies" class="section">
  <div style="font-size:10px;color:var(--dim);font-family:var(--mono);margin-bottom:12px">Click a company to filter jobs →</div>
  <div class="g2" id="companies-grid"></div>
</div>

<!-- ══ REMOTE ══ -->
<div id="tab-remote" class="section">
  <div style="margin-bottom:16px">
    <div style="font-size:9px;color:var(--gold);letter-spacing:2px;font-family:var(--mono);margin-bottom:6px">REMOTE ROLES · WORKABLE FROM BRAZIL</div>
    <div style="font-size:11px;color:var(--muted);line-height:1.6">Roles flagged as Remote or Hybrid by AI scoring. Target: R$35,000+/month.</div>
  </div>
  <div id="remote-list"></div>
</div>

<!-- ══ SETUP ══ -->
<div id="tab-alerts" class="section">
  <div class="g2" style="margin-bottom:14px">
    <div class="card">
      <div style="font-size:9px;color:var(--blue);letter-spacing:2px;font-family:var(--mono);margin-bottom:14px">LINKEDIN JOB ALERTS</div>
      <div id="li-alerts"></div>
    </div>
    <div class="card">
      <div style="font-size:9px;color:var(--orange);letter-spacing:2px;font-family:var(--mono);margin-bottom:14px">GOOGLE ALERTS</div>
      <div id="g-alerts"></div>
    </div>
  </div>
  <div class="card">
    <div style="font-size:9px;color:var(--blue);letter-spacing:2px;font-family:var(--mono);margin-bottom:14px">GITHUB SETUP · CLAUDE CODE WORKFLOW</div>
    <div style="font-size:11px;color:var(--muted);line-height:1.9">
      <b style="color:var(--text)">Update this tracker with Claude Code:</b><br>
      1. Install: <code style="background:var(--bg3);padding:1px 6px;border-radius:3px">npm install -g @anthropic/claude-code</code><br>
      2. Clone: <code style="background:var(--bg3);padding:1px 6px;border-radius:3px">git clone https://github.com/PMLPereira/job_search_agent</code><br>
      3. Run: <code style="background:var(--bg3);padding:1px 6px;border-radius:3px">cd job_search_agent && claude</code><br><br>
      <b style="color:var(--text)">Dashboard:</b>
      <code style="background:var(--bg3);padding:2px 8px;border-radius:4px;color:var(--green)">https://pmlpereira.github.io/job_search_agent/</code>
    </div>
  </div>
</div>

</div><!-- /wrap -->

<script>
const JOBS = {jobs_json};
const COMPANIES = {companies_json};
const RUN_HISTORY = {history_json};
const TOP_SKILLS = {top_skills_json};

const STATUS_OPTS = ["Monitoring","Applied","Phone Screen","Interview","Offer","Rejected","On Hold"];
const STATUS_COL  = {{
  "Monitoring":"#a09890","Applied":"#3a7cbf","Phone Screen":"#d48000",
  "Interview":"#7c5cbf","Offer":"#28a848","Rejected":"#c82e00","On Hold":"#b8b0a8"
}};

const MY_SKILLS = ["program management","delivery","stakeholder management","regulatory","capital markets",
  "p&l","budget","python","sql","agile","scrum","data governance","risk management",
  "product management","fintech","technology transformation","ai","automation","portuguese","english"];

let pipeline = JSON.parse(localStorage.getItem('sp_pl3') || 'null') || [
  {{id:'p1',company:'BTG Pactual',role:'Head of Technology Delivery',status:'Monitoring',next_action:'Send LinkedIn message',due_date:'',contact:'',notes:'Target #1',date:'2026-05-20',score:null,salary:'R$40-55k/mo'}},
  {{id:'p2',company:'XP Investimentos',role:'Technology Program Director',status:'Monitoring',next_action:'Research hiring manager',due_date:'',contact:'',notes:'AI/tech culture match',date:'2026-05-20',score:null,salary:'R$35-48k/mo'}},
  {{id:'p3',company:'Pátria Investimentos',role:'Senior Program Manager',status:'Monitoring',next_action:'Check for new postings',due_date:'',contact:'',notes:'PE expansion needs tech ops',date:'2026-05-20',score:null,salary:'R$32-45k/mo'}},
];
function saveP(){{localStorage.setItem('sp_pl3',JSON.stringify(pipeline));}}

function sc(s){{return s>=65?'var(--green)':s>=50?'var(--gold)':'var(--red)';}}
function ring(s){{
  const c=s!=null?sc(s):'var(--dimmer)', v=s!=null?s:'—';
  return `<div class="ring" style="border:3px solid ${{c}};background:${{c}}18;color:${{c}}">${{v}}</div>`;
}}
function badge(t,c){{return `<span class="badge" style="background:${{c}}1a;border:1px solid ${{c}}55;color:${{c}}">${{t}}</span>`;}}
function pill(t,cls){{return `<span class="skill ${{cls}}">${{t}}</span>`;}}

function daysAgo(iso){{
  if(!iso) return null;
  const d=Math.round((Date.now()-new Date(iso))/86400000);
  return d===0?'today':d===1?'1d ago':d+'d ago';
}}

function isBelowTarget(job){{
  const sl=((job.score||{{}}).seniorityLevel||'').toLowerCase();
  if(!sl) return false;
  return !['director','head','c-level','vp','managing','executive','chief'].some(t=>sl.includes(t));
}}

function filterByCompany(name){{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.querySelector('.tab').classList.add('active');
  document.getElementById('tab-jobs').classList.add('active');
  document.getElementById('f-company').value=name;
  renderJobs();
}}

// ── Stats ──
function renderStats(){{
  const total=JOBS.length,
        strong=JOBS.filter(j=>(j.score||{{}}).score>=65).length,
        good=JOBS.filter(j=>{{const s=(j.score||{{}}).score;return s>=50&&s<65;}}).length,
        isNew=JOBS.filter(j=>j.is_new).length;
  document.getElementById('stats-bar').innerHTML=[
    ['Total Roles',total,'var(--muted)'],['Strong Match',strong,'var(--green)'],
    ['Good Match',good,'var(--gold)'],['New Today',isNew,'var(--gold)'],['Pipeline',pipeline.length,'var(--blue)'],
  ].map(([l,v,c])=>`<div class="stat"><div class="v" style="color:${{c}}">${{v}}</div><div class="l">${{l}}</div></div>`).join('');
}}

// ── Jobs ──
function renderJobs(){{
  const fc=document.getElementById('f-company').value,
        fs=parseInt(document.getElementById('f-score').value)||0,
        fa=document.getElementById('f-arrange').value,
        fn=document.getElementById('f-new').value,
        fsen=document.getElementById('f-seniority').value,
        fsort=document.getElementById('f-sort').value,
        fq=document.getElementById('f-search').value.toLowerCase();

  const sel=document.getElementById('f-company');
  if(sel.options.length===1){{
    [...new Set(JOBS.map(j=>j.company))].sort().forEach(c=>{{
      const o=document.createElement('option');o.value=c;o.textContent=c;sel.appendChild(o);
    }});
  }}

  let filtered=JOBS.filter(j=>{{
    const sc2=(j.score||{{}});
    if(fc&&j.company!==fc)return false;
    if(fs&&(sc2.score||0)<fs)return false;
    if(fa&&sc2.workArrangement!==fa)return false;
    if(fn==='new'&&!j.is_new)return false;
    if(fsen==='senior'&&isBelowTarget(j))return false;
    if(fq&&!j.title.toLowerCase().includes(fq)&&!j.company.toLowerCase().includes(fq)&&
       !(sc2.keySkillsRequired||[]).join(' ').toLowerCase().includes(fq)&&
       !(sc2.skillsYouHave||[]).join(' ').toLowerCase().includes(fq))return false;
    return true;
  }});

  if(fsort==='new')      filtered.sort((a,b)=>new Date(b.found_at||0)-new Date(a.found_at||0));
  else if(fsort==='salary') filtered.sort((a,b)=>{{
    const pa=parseInt(((a.score||{{}}).salaryRange||'0').replace(/[^\d]/g,'').slice(0,6)||0);
    const pb=parseInt(((b.score||{{}}).salaryRange||'0').replace(/[^\d]/g,'').slice(0,6)||0);
    return pb-pa;
  }});
  else filtered.sort((a,b)=>((b.score||{{}}).score||0)-((a.score||{{}}).score||0));

  if(!filtered.length){{
    document.getElementById('jobs-list').innerHTML='<div style="text-align:center;color:var(--dim);padding:50px;font-family:var(--mono);font-size:11px">No roles match — try relaxing filters.</div>';
    return;
  }}

  document.getElementById('jobs-list').innerHTML=filtered.map(j=>{{
    const s=j.score||{{}}, score=s.score, col=score!=null?sc(score):'var(--dim)';
    const salary=s.salaryRange||estimateSalary(j.title);
    const skills=(s.keySkillsRequired||[]);
    const have=(s.skillsYouHave||[]);
    const lack=(s.skillsYouLack||[]);
    const age=daysAgo(j.found_at);
    const ageUrgent=age&&!['today','1d ago'].includes(age)&&parseInt(age)>5;
    const applyCol={{'Yes':'var(--green)','Yes with tweaks':'var(--gold)','No':'var(--red)'}}[s.applyRecommendation]||'var(--dim)';
    const belowTarget=isBelowTarget(j);
    const bd=s.scoreBreakdown||{{}};

    const outreachSafe=s.outreachTemplate?(s.outreachTemplate.replace(/\[Name\]/g,
      '<mark style="background:rgba(212,128,0,.25);color:var(--gold);border-radius:3px;padding:0 3px;font-style:italic">[Name — fill in before sending]</mark>')):'';

    return `
<div class="card" style="border-color:${{col}}2a">
  <div style="display:flex;gap:12px;align-items:flex-start">
    ${{ring(score!=null?score:null)}}
    <div style="flex:1;min-width:0">
      <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:5px">
        ${{j.is_new?badge('NEW','var(--gold)'):''}}
        ${{age?`<span class="badge" style="background:${{ageUrgent?'rgba(200,46,0,.1)':'rgba(160,152,144,.1)'}};border:1px solid ${{ageUrgent?'rgba(200,46,0,.3)':'var(--border2)'}};color:${{ageUrgent?'var(--red)':'var(--dim)'}}">${{ageUrgent?'⚠ ':''}}${{age}}</span>`:'&nbsp;'}}
        ${{s.verdict?badge(s.verdict,col):''}}
        ${{s.applyRecommendation?`<span class="badge" style="background:${{applyCol}}18;border:1px solid ${{applyCol}}44;color:${{applyCol}}">${{s.applyRecommendation==='Yes'?'✓ Apply':'⚡ '+s.applyRecommendation}}</span>`:''}}
        ${{belowTarget?badge('Below target level','var(--red)'):'&nbsp;'}}
      </div>
      <div style="font-size:15px;color:var(--text);margin-bottom:3px;font-weight:500">${{j.title}}</div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">${{j.company}}${{s.seniorityLevel?` · <span style="color:var(--dim)">${{s.seniorityLevel}}</span>`:''}}</div>
      <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px">
        <span style="font-size:11px;color:var(--green);font-family:var(--mono)">💰 ${{salary}}</span>
        <span style="font-size:11px;color:var(--muted)">📍 ${{j.location}}</span>
        ${{s.yearsExpRequired?`<span style="font-size:11px;color:var(--dim)">🗓 ${{s.yearsExpRequired}}</span>`:''}}
        ${{(s.languagesRequired||[]).length?`<span style="font-size:11px;color:var(--dim)">🌐 ${{s.languagesRequired.join('+')}}` :''}}</div>
      ${{skills.length?`<div style="margin-bottom:8px">${{skills.filter(sk=>sk&&sk.trim()).map(sk=>{{
        const has=have.some(h=>h&&h.toLowerCase()===sk.toLowerCase());
        const lacks=lack.some(l=>l&&l.toLowerCase()===sk.toLowerCase());
        return pill(sk,has?'have':lacks?'lack':'');
      }}).join('')}}</div>`:''}}
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        ${{j.url?`<a href="${{j.url}}" target="_blank" style="color:var(--blue);font-size:10px;font-family:var(--mono)">View role →</a>`:''}}
        <button onclick="tog('d${{j.id}}')" style="background:none;border:1px solid var(--border2);color:var(--dim);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px;font-family:var(--mono)">Details ▾</button>
        <button onclick="addPL('${{j.id}}')" style="background:rgba(40,168,72,.08);border:1px solid rgba(40,168,72,.3);color:var(--green);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px;font-family:var(--mono)">+ Pipeline</button>
      </div>
    </div>
  </div>
  <div class="detail" id="d${{j.id}}">
    ${{(bd.skills!=null)?`
    <div class="dsec">
      <div class="dsec-title" style="color:var(--blue)">SCORE BREAKDOWN</div>
      <div class="score-bd">
        ${{[['skills',bd.skills,40],['seniority',bd.seniority,30],['sector',bd.sector,20],['language',bd.language,10]].map(([k,v,m])=>{{
          const vc=v!=null?v:0;
          const c=vc>=m*.75?'var(--green)':vc>=m*.5?'var(--gold)':'var(--red)';
          return `<div class="score-bd-item"><div class="v" style="color:${{c}}">${{vc!=null?vc:'—'}}</div><div class="l">${{k.toUpperCase()}}</div><div class="max">/${{m}}</div></div>`;
        }}).join('')}}
      </div>
    </div>`:''}}
    <div class="detail-grid" style="margin-top:12px">
      ${{s.topReasons&&s.topReasons.length?`<div><div class="dsec-title" style="color:var(--green)">WHY YOU FIT</div>${{s.topReasons.map(r=>`<div style="font-size:11px;color:var(--green);opacity:.8;margin-bottom:5px;line-height:1.5">✓ ${{r}}</div>`).join('')}}</div>`:''}}
      ${{s.gaps&&s.gaps.length?`<div><div class="dsec-title" style="color:var(--red)">GAPS & ACTIONS</div>${{s.gaps.map((g,i)=>`<div style="font-size:11px;color:var(--red);opacity:.8;margin-bottom:3px">✗ ${{g}}</div>${{(s.gapActions||[])[i]?`<div style="font-size:10px;color:var(--muted);margin-bottom:6px;padding-left:10px">→ ${{s.gapActions[i]}}</div>`:''}}`).join('')}}</div>`:''}}
    </div>
    ${{s.talkingPoints&&s.talkingPoints.length?`<div class="dsec"><div class="dsec-title" style="color:var(--gold)">TALKING POINTS</div>${{s.talkingPoints.map(t=>`<div style="font-size:11px;color:var(--muted);margin-bottom:5px;line-height:1.5">→ ${{t}}</div>`).join('')}}</div>`:''}}
    ${{outreachSafe?`<div class="dsec"><div class="dsec-title" style="color:var(--blue)">LINKEDIN OUTREACH</div><div class="outreach">${{outreachSafe}}</div></div>`:''}}
    ${{s.suggestedContact?`<div style="margin-top:8px;font-size:10px;color:var(--blue);font-family:var(--mono)">🔍 Find: ${{s.suggestedContact}} · <a href="https://www.linkedin.com/search/results/people/?keywords=${{encodeURIComponent(s.suggestedContact+' '+j.company)}}" target="_blank" style="color:var(--blue)">Search LinkedIn →</a></div>`:''}}
    ${{(s.atsKeywords&&(s.atsKeywords.match||s.atsKeywords.missing))?`
    <div class="dsec">
      <div class="dsec-title" style="color:var(--orange)">ATS KEYWORD CHECK</div>
      <div style="display:flex;gap:5px;flex-wrap:wrap">
        ${{(s.atsKeywords.match||[]).map(k=>`<span style="background:var(--tint-green);border:1px solid rgba(40,168,72,.35);color:var(--green);font-size:10px;padding:2px 7px;border-radius:4px;font-family:var(--mono)">✓ ${{k}}</span>`).join('')}}
        ${{(s.atsKeywords.missing||[]).map(k=>`<span style="background:var(--tint-red);border:1px solid rgba(200,46,0,.3);color:var(--red);font-size:10px;padding:2px 7px;border-radius:4px;font-family:var(--mono)">✗ ${{k}}</span>`).join('')}}
      </div>
      ${{(s.atsKeywords.missing||[]).length?`<div style="font-size:10px;color:var(--dim);margin-top:6px">Add missing keywords to CV/LinkedIn before applying.</div>`:''}}
    </div>`:''}}
    ${{s.cvTweaks&&s.cvTweaks.length?`
    <div class="dsec">
      <div class="dsec-title" style="color:var(--purple)">CV TWEAKS FOR THIS ROLE</div>
      ${{s.cvTweaks.map(t=>`<div style="font-size:11px;color:var(--muted);margin-bottom:5px;line-height:1.5;padding-left:8px;border-left:2px solid var(--purple)">${{t}}</div>`).join('')}}
    </div>`:''}}
    ${{s.coverLetterPoints&&s.coverLetterPoints.length?`
    <div class="dsec">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px">
        <div class="dsec-title" style="color:var(--gold);margin-bottom:0">COVER LETTER POINTS</div>
        <button onclick="copyCL('${{j.id}}')" style="background:rgba(212,128,0,.12);border:1px solid rgba(212,128,0,.35);color:var(--gold);padding:2px 10px;border-radius:4px;cursor:pointer;font-size:9px;font-family:var(--mono)">Copy draft ↗</button>
      </div>
      ${{s.coverLetterPoints.map((p,i)=>`<div style="font-size:11px;color:var(--muted);margin-bottom:6px;line-height:1.5;padding-left:8px;border-left:2px solid var(--gold)"><b style="color:var(--text)">${{i+1}}.</b> ${{p}}</div>`).join('')}}
    </div>`:''}}
    ${{s.interviewQuestions&&s.interviewQuestions.length?`
    <div class="dsec">
      <div class="dsec-title" style="color:var(--green)">INTERVIEW PREP</div>
      ${{s.interviewQuestions.map(q=>`
        <div style="margin-bottom:10px">
          <div style="font-size:11px;color:var(--text);font-weight:600;margin-bottom:3px">Q: ${{q.q}}</div>
          <div style="font-size:10px;color:var(--muted);line-height:1.6;padding-left:8px;border-left:2px solid var(--green)">→ ${{q.hint}}</div>
        </div>`).join('')}}
    </div>`:''}}
  </div>
</div>`;
  }}).join('');
}}

function estimateSalary(title){{
  const t=title.toLowerCase();
  if(t.includes('head')||t.includes('diretor')||t.includes('director'))return'R$40,000-60,000/mo (est.)';
  if(t.includes('senior')||t.includes('sênior'))return'R$28,000-42,000/mo (est.)';
  return'R$22,000-35,000/mo (est.)';
}}
function tog(id){{const el=document.getElementById(id);el&&el.classList.toggle('open');}}
function copyCL(jid){{
  const j=JOBS.find(x=>x.id===jid);if(!j)return;
  const s=j.score||{{}};
  const pts=(s.coverLetterPoints||[]).map((p,i)=>`${{i+1}}. ${{p}}`).join('\n\n');
  const text=`Dear Hiring Team,\n\nI am writing to express my interest in the ${{j.title}} position at ${{j.company}}.\n\n${{pts}}\n\nI would welcome the opportunity to discuss how my background aligns with your needs.\n\nWarm regards,\nPedro Pereira\nmpereira.pedro@gmail.com | linkedin.com/in/pedrolourencopereira`;
  navigator.clipboard.writeText(text).then(()=>alert('Cover letter draft copied to clipboard ✓'));
}}
function addPL(jid){{
  const j=JOBS.find(x=>x.id===jid);if(!j)return;
  if(pipeline.find(p=>p.id===jid)){{alert('Already in pipeline');return;}}
  const s=j.score||{{}};
  pipeline.push({{id:jid,company:j.company,role:j.title,status:'Monitoring',
    next_action:'Review job description',due_date:'',
    contact:s.suggestedContact||'',notes:s.verdict?`Score ${{s.score}}/100 · ${{s.verdict}}`:'',
    date:new Date().toISOString().slice(0,10),score:s.score||null,
    salary:s.salaryRange||estimateSalary(j.title),url:j.url||''}});
  saveP();renderPipeline();renderStats();alert('Added to pipeline ✓');
}}

// ── Market Intel ──
let showAllHave=false, showAllLack=false;
function renderMarket(){{
  const bands=[
    ['C-Level / MD','R$60,000-90,000+','30-50%',90],
    ['Head of / VP','R$40,000-60,000','30-50%',75],
    ['Director','R$35,000-55,000','20-40%',65],
    ['Senior Manager','R$28,000-42,000','15-25%',52],
    ['Program Manager','R$22,000-35,000','10-20%',40],
    ['Senior IC','R$18,000-30,000','10-15%',32],
  ];
  document.getElementById('salary-bands').innerHTML=bands.map(([t,r,b,w])=>`
    <div style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;margin-bottom:3px">
        <span style="font-size:11px;color:var(--muted)">${{t}}</span>
        <span style="font-size:11px;color:var(--green);font-family:var(--mono)">${{r}}</span>
      </div>
      <div class="mbar"><div class="mfill" style="width:${{w}}%;background:var(--green)"></div></div>
      <div style="font-size:9px;color:var(--dim);font-family:var(--mono);margin-top:2px">Bonus: ${{b}} of base</div>
    </div>`).join('');

  const maxCount=TOP_SKILLS.length?Math.max(...TOP_SKILLS.map(([,c])=>c),1):1;
  document.getElementById('skills-heatmap').innerHTML=TOP_SKILLS
    .filter(([sk])=>sk&&sk.trim())
    .map(([sk,cnt])=>{{
      const w=Math.round(cnt/maxCount*100);
      const iHave=MY_SKILLS.some(m=>sk.toLowerCase().includes(m)||m.includes(sk.toLowerCase()));
      return `<div style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;margin-bottom:2px">
          <span style="font-size:11px;color:${{iHave?'var(--green)':'var(--red)'}}">${{sk}} ${{iHave?'✓':'✗'}}</span>
          <span style="font-size:10px;color:var(--dim);font-family:var(--mono)">${{cnt}} roles</span>
        </div>
        <div class="mbar"><div class="mfill" style="width:${{w}}%;background:${{iHave?'var(--green)':'var(--red)'}}"></div></div>
      </div>`;
    }}).join('') || '<div style="color:var(--dim);font-size:11px;font-family:var(--mono)">Run the scanner to populate skill data.</div>';

  const allSkills=new Set();const haveSet=new Set();const lackSet=new Set();
  JOBS.forEach(j=>{{
    (j.score?.keySkillsRequired||[]).filter(s=>s&&s.trim()).forEach(s=>allSkills.add(s));
    (j.score?.skillsYouHave||[]).filter(s=>s&&s.trim()).forEach(s=>haveSet.add(s));
    (j.score?.skillsYouLack||[]).filter(s=>s&&s.trim()).forEach(s=>lackSet.add(s));
  }});
  const haveArr=[...haveSet], lackArr=[...lackSet];
  document.getElementById('skills-gap').innerHTML=`
    <div class="g2">
      <div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div style="font-size:9px;color:var(--green);font-family:var(--mono)">YOU HAVE (${{haveSet.size}})</div>
          ${{haveArr.length>10?`<button onclick="showAllHave=!showAllHave;renderMarket()" style="background:none;border:none;color:var(--blue);cursor:pointer;font-size:9px;font-family:var(--mono)">${{showAllHave?'show less':'show all '+haveArr.length}}</button>`:'&nbsp;'}}
        </div>
        <div>${{(showAllHave?haveArr:haveArr.slice(0,10)).map(s=>pill(s,'have')).join('')}}</div>
      </div>
      <div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div style="font-size:9px;color:var(--red);font-family:var(--mono)">GAPS (${{lackSet.size}})</div>
          ${{lackArr.length>10?`<button onclick="showAllLack=!showAllLack;renderMarket()" style="background:none;border:none;color:var(--blue);cursor:pointer;font-size:9px;font-family:var(--mono)">${{showAllLack?'show less':'show all '+lackArr.length}}</button>`:'&nbsp;'}}
        </div>
        <div>${{(showAllLack?lackArr:lackArr.slice(0,10)).map(s=>pill(s,'lack')).join('')}}</div>
        ${{lackSet.size?'<div style="font-size:10px;color:var(--dim);margin-top:8px;line-height:1.6">Add missing keywords to CV/LinkedIn before applying.</div>':''}}
      </div>
    </div>`;

  if(RUN_HISTORY.length){{
    const recent=RUN_HISTORY.slice(-30);
    const maxH=Math.max(...recent.map(r=>r.count),1);
    const bars=recent.map((r,i)=>{{
      const h=Math.round(r.count/maxH*64)+4;
      const isFirst=(i===0),isLast=(i===recent.length-1),isMid=(i===Math.floor(recent.length/2));
      const label=(isFirst||isLast||isMid)?`<div style="position:absolute;bottom:-18px;left:50%;transform:translateX(-50%);font-size:7px;color:var(--dim);white-space:nowrap;font-family:var(--mono)">${{r.date.slice(5)}}</div>`:'';
      return `<div style="position:relative;flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center">
        <div style="font-size:8px;color:var(--dim);font-family:var(--mono);margin-bottom:2px">${{r.count>0&&(isFirst||isLast)?r.count:''}}</div>
        <div title="${{r.date}}: ${{r.count}} roles" style="width:100%;height:${{h}}px;background:var(--green);border-radius:2px 2px 0 0;opacity:0.65;cursor:default"></div>
        ${{label}}
      </div>`;
    }}).join('');
    document.getElementById('scan-history').innerHTML=`
      <div style="display:flex;align-items:flex-end;position:relative">
        <div style="position:absolute;top:0;left:0;font-size:8px;color:var(--dim);font-family:var(--mono)">${{maxH}}</div>
        <div style="position:absolute;bottom:22px;left:0;font-size:8px;color:var(--dim);font-family:var(--mono)">0</div>
        <div style="flex:1;display:flex;gap:2px;align-items:flex-end;height:90px;margin-left:16px;padding-bottom:20px">
          ${{bars}}
        </div>
      </div>
      <div style="font-size:9px;color:var(--dim);font-family:var(--mono);margin-top:8px">Last ${{recent.length}} daily scans · hover bars for details</div>`;
  }} else {{
    document.getElementById('scan-history').innerHTML='<div style="color:var(--dim);font-size:11px;font-family:var(--mono)">History will appear after the first few scans.</div>';
  }}
}}

// ── Pipeline ──
function renderPipeline(){{
  document.getElementById('ptbody').innerHTML=pipeline.map((e,i)=>`
    <tr>
      <td>${{e.score!=null?`<span style="color:${{sc(e.score)}};font-family:var(--mono);font-weight:700">${{e.score}}</span>`:'<span style="color:var(--dim)">—</span>'}}</td>
      <td style="color:var(--muted);white-space:nowrap">${{e.company}}</td>
      <td><div style="color:var(--text)">${{e.role}}</div>
          ${{e.url?`<a href="${{e.url}}" target="_blank" style="font-size:9px;color:var(--blue);font-family:var(--mono)">View →</a>`:''}}</td>
      <td style="color:var(--green);font-family:var(--mono);font-size:10px;white-space:nowrap">${{e.salary||'—'}}</td>
      <td><select class="psel" onchange="upP(${{i}},'status',this.value)" style="color:${{STATUS_COL[e.status]||'var(--muted)'}}">${{STATUS_OPTS.map(s=>`<option ${{e.status===s?'selected':''}}>${{s}}</option>`).join('')}}</select></td>
      <td><input class="pinput" value="${{e.next_action||''}}" onchange="upP(${{i}},'next_action',this.value)" placeholder="e.g. Send LinkedIn message" style="width:180px"></td>
      <td><input class="pinput" type="date" value="${{e.due_date||''}}" onchange="upP(${{i}},'due_date',this.value)" style="width:120px"></td>
      <td><input class="pinput" value="${{e.contact||''}}" onchange="upP(${{i}},'contact',this.value)" placeholder="Contact name/title" style="width:140px"></td>
      <td><input class="pinput" value="${{e.notes||''}}" onchange="upP(${{i}},'notes',this.value)" placeholder="Notes..." style="width:140px"></td>
      <td><button onclick="rmP(${{i}})" style="background:none;border:none;color:var(--red);opacity:.6;cursor:pointer;font-size:16px">×</button></td>
    </tr>`).join('');

  const counts=STATUS_OPTS.map(s=>([s,pipeline.filter(e=>e.status===s).length])).filter(([,c])=>c>0);
  const total=pipeline.length||1;
  document.getElementById('funnel').innerHTML=counts.map(([s,c])=>`
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:7px">
      <div style="width:120px;font-size:10px;color:var(--muted);font-family:var(--mono)">${{s}}</div>
      <div style="flex:1;background:var(--bg4);border-radius:4px;height:22px">
        <div style="width:${{Math.min(c/total*100,100)}}%;min-width:24px;height:22px;background:${{STATUS_COL[s]||'var(--dim)'}};border-radius:4px;display:flex;align-items:center;padding-left:8px;opacity:.85">
          <span style="font-size:10px;color:#fff;font-weight:700">${{c}}</span>
        </div>
      </div>
    </div>`).join('') || '<div style="color:var(--dim);font-size:11px">Add roles to see funnel.</div>';
}}
function upP(i,f,v){{pipeline[i][f]=v;saveP();renderPipeline();}}
function rmP(i){{pipeline.splice(i,1);saveP();renderPipeline();renderStats();}}
function addManual(){{
  const co=prompt('Company:'),ro=prompt('Role title:');if(!co||!ro)return;
  pipeline.push({{id:'m'+Date.now(),company:co,role:ro,status:'Monitoring',
    next_action:'',due_date:'',contact:'',notes:'',
    date:new Date().toISOString().slice(0,10),score:null,salary:'',url:''}});
  saveP();renderPipeline();renderStats();
}}

// ── Companies ──
function renderCompanies(){{
  document.getElementById('companies-grid').innerHTML=COMPANIES.map(c=>{{
    const cJobs=JOBS.filter(j=>j.company===c.name);
    const top=cJobs.length?Math.max(...cJobs.map(j=>(j.score||{{}}).score||0)):null;
    const inPL=pipeline.filter(p=>p.company===c.name).length;
    const lastFound=cJobs.length?cJobs.reduce((a,b)=>(a.found_at>b.found_at?a:b)).found_at:null;
    const lastAge=lastFound?daysAgo(lastFound):null;
    return`<div class="card co-card" style="border-color:${{c.color}}33" onclick="filterByCompany('${{c.name}}')">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
        <div>
          <div style="font-size:14px;color:${{c.color}};font-weight:600;margin-bottom:2px">${{c.name}}</div>
          <div style="font-size:10px;color:var(--dim);font-family:var(--mono)">${{c.sector}}</div>
        </div>
        ${{badge('Tier '+c.tier,c.tier===1?'var(--green)':'var(--muted)')}}
      </div>
      <div style="font-size:11px;color:var(--muted);line-height:1.6;margin-bottom:8px">${{c.why}}</div>
      <div style="font-size:10px;color:var(--muted);background:var(--bg3);border-radius:6px;padding:8px 10px;margin-bottom:10px;line-height:1.6;border:1px solid var(--border)">
        <b style="color:var(--text)">Interview:</b> ${{c.interview}}
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
        <span style="font-size:10px;color:var(--dim);font-family:var(--mono)">${{cJobs.length}} role${{cJobs.length!==1?'s':''}} found</span>
        ${{lastAge?`<span style="font-size:10px;color:var(--dim);font-family:var(--mono)">· last: ${{lastAge}}</span>`:''}}
        ${{top?badge(top+'/100',sc(top)):''}}
        ${{inPL?badge(inPL+' in pipeline','var(--blue)'):''}}
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <a href="${{c.careers_url}}" target="_blank" onclick="event.stopPropagation()" style="font-size:10px;color:var(--blue);font-family:var(--mono)">Careers →</a>
        <a href="https://www.linkedin.com/search/results/people/?keywords=${{encodeURIComponent('technology director '+c.name)}}&origin=GLOBAL_SEARCH_HEADER" target="_blank" onclick="event.stopPropagation()" style="font-size:10px;color:var(--purple);font-family:var(--mono)">LinkedIn contacts →</a>
      </div>
    </div>`;
  }}).join('');
}}

// ── Alerts ──
function renderAlerts(){{
  const liQ=[
    'Technology Program Director São Paulo financial services',
    'Head Technology Delivery BTG Pactual XP','Senior Program Manager Nubank Pátria fintech',
    'Regulatory Technology Director Itaú BBA','Digital Transformation Director São Paulo banking',
    'Technology Director capital markets Brazil','Program Management Director São Paulo investment bank',
  ];
  const gQ=[
    'BTG Pactual technology director hiring 2026','XP Investimentos senior technology program manager',
    'Nubank head technology delivery São Paulo','Pátria Investimentos technology transformation director',
    'Vinci Partners technology senior manager',
  ];
  document.getElementById('li-alerts').innerHTML=liQ.map(q=>`
    <div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)">
      <span style="font-size:10px;color:var(--muted);font-family:var(--mono)">"${{q}}"</span>
      <a href="https://www.linkedin.com/jobs/search/?keywords=${{encodeURIComponent(q)}}&location=S%C3%A3o+Paulo" target="_blank"
         style="font-size:9px;color:var(--blue);font-family:var(--mono);margin-left:8px;white-space:nowrap">Search →</a>
    </div>`).join('');
  document.getElementById('g-alerts').innerHTML=gQ.map(q=>`
    <div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)">
      <span style="font-size:10px;color:var(--muted);font-family:var(--mono)">"${{q}}"</span>
      <a href="https://www.google.com/alerts#create:${{encodeURIComponent(q)}}" target="_blank"
         style="font-size:9px;color:var(--orange);font-family:var(--mono);margin-left:8px;white-space:nowrap">Create →</a>
    </div>`).join('');
}}

// ── Remote ──
function renderRemote(){{
  const remoteJobs=JOBS.filter(j=>{{
    const wt=(j.work_type||'').toLowerCase();
    const wa=((j.score||{{}}).workArrangement||'').toLowerCase();
    const desc=(j.description||'').toLowerCase();
    return wt.includes('remot')||wa.includes('remot')||
           desc.includes('remoto')||desc.includes('home office');
  }});
  if(!remoteJobs.length){{
    document.getElementById('remote-list').innerHTML=
      '<div class="card" style="text-align:center;color:var(--dim);padding:40px;font-family:var(--mono);font-size:11px">No remote roles detected yet.<br>Remote roles are identified by AI scoring — run the scanner to populate.</div>';
    return;
  }}
  document.getElementById('remote-list').innerHTML=remoteJobs.map(j=>{{
    const s=j.score||{{}};
    const score=s.score;
    const col=score!=null?sc(score):'var(--dim)';
    const salary=s.salaryRange||estimateSalary(j.title);
    return `<div class="card" style="margin-bottom:10px;border-color:${{col}}2a">
      <div style="display:flex;gap:12px;align-items:flex-start">
        ${{ring(score!=null?score:null)}}
        <div style="flex:1;min-width:0">
          <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:5px">
            ${{j.is_new?badge('NEW','var(--gold)'):''}}
            ${{s.verdict?badge(s.verdict,col):''}}
            ${{badge(s.workArrangement||'Remote','var(--blue)')}}
          </div>
          <div style="font-size:14px;color:var(--text);font-weight:600;margin-bottom:2px">${{j.title}}</div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:6px">${{j.company}} · ${{j.location}}</div>
          <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px">
            <span style="font-size:11px;color:var(--green);font-family:var(--mono)">${{salary}}</span>
            ${{s.seniorityLevel?`<span style="font-size:10px;color:var(--dim);font-family:var(--mono)">${{s.seniorityLevel}}</span>`:''}}
            ${{j.url?`<a href="${{j.url}}" target="_blank" style="font-size:10px;color:var(--blue);font-family:var(--mono)">View →</a>`:''}}
          </div>
          ${{(s.topReasons||[]).length?`<div style="font-size:10px;color:var(--muted);line-height:1.7">${{s.topReasons.map(r=>`· ${{r}}`).join('<br>')}}</div>`:''}}
        </div>
      </div>
    </div>`;
  }}).join('');
}}

// ── Tab switch ──
function sw(name,btn){{
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  btn.classList.add('active');
  if(name==='market')renderMarket();
  if(name==='remote')renderRemote();
}}

// ── Init ──
renderStats();renderJobs();renderPipeline();renderCompanies();renderAlerts();
</script>
</body>
</html>"""


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

    merged = merge_jobs(existing.get("jobs",[]), all_new)

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
