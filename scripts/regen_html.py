"""
Quick one-shot script: reads docs/data.json and regenerates docs/index.html
using the current generate_html() from scan.py.
Run from the repo root: python scripts/regen_html.py
"""
import sys, json, os, re

sys.stdout.reconfigure(encoding="utf-8")

# ── exec scan.py in a namespace, stripping the main block ───────────────────
with open("scripts/scan.py", encoding="utf-8") as f:
    src = f.read()

# Strip everything from `def main():` onward so nothing auto-executes
src_no_main = re.split(r'\ndef main\(\)', src)[0]

ns = {"__name__": "scan_import", "__file__": "scripts/scan.py"}
exec(compile(src_no_main, "scripts/scan.py", "exec"), ns)

generate_html = ns["generate_html"]
print("generate_html loaded OK")

# ── load existing data ───────────────────────────────────────────────────────
with open("docs/data.json", encoding="utf-8") as f:
    data = json.load(f)

print(f"Loaded {len(data['jobs'])} jobs from data.json")

# ── regenerate HTML ──────────────────────────────────────────────────────────
html = generate_html(data)

with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("docs/index.html regenerated successfully.")
