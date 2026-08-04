# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""
Fetch jobs from Silicon Harbour and reconcile with our data.
"""

import datetime as dt
import json
import logging
import os
import re
import sys
import httpx

logging.basicConfig(level=logging.INFO, format="%(message)s")

def normalize_company_name(name):
    # Remove punctuation by replacing with space
    name = re.sub(r'[^\w\s]', ' ', name)
    # Remove multiple spaces
    name = re.sub(r'\s+', ' ', name)
    # Casefold
    name = name.casefold().strip()
    
    # Remove suffixes
    while True:
        prev = name
        name = re.sub(r'\s+(inc|ltd|limited|corp|llp|software)$', '', name).strip()
        if name == prev:
            break
            
    # Explicit aliases for known variants that might not naturally match
    # (Though most of these are handled by suffix removal)
    aliases = {
        'compusult limited': 'compusult',
        'solace power inc': 'solace power',
        'trudell medical limited': 'trudell medical',
        'vish limited': 'vish',
        'virtual marine inc': 'virtual marine',
        'colab software': 'colab',
    }
    return aliases.get(name, name)

def main():
    try:
        run_reconcile()
    except Exception as e:
        logging.error(f"SH reconcile failed: {e}")
        sys.exit(0)

def run_reconcile():
    # 1. Fetch from Silicon Harbour API
    sh_jobs = []
    limit = 100
    offset = 0
    with httpx.Client(timeout=15.0) as client:
        while True:
            resp = client.get(f"https://siliconharbour.dev/api/jobs?limit={limit}&offset={offset}")
            resp.raise_for_status()
            payload = resp.json()
            jobs = payload.get("data", [])
            sh_jobs.extend(jobs)
            
            data = payload
            
            pagination = data.get("pagination", {})
            if not pagination.get("hasMore", False):
                break
            offset += limit

    # Reduce SH jobs to distinct companies
    sh_companies = {}
    for job in sh_jobs:
        raw_name = job.get("companyName", "Unknown")
        norm_name = normalize_company_name(raw_name)
        url = job.get("url")
        
        if norm_name not in sh_companies:
            sh_companies[norm_name] = {
                "company_name": raw_name,
                "job_count": 0,
                "urls": set()
            }
        
        sh_companies[norm_name]["job_count"] += 1
        if url:
            sh_companies[norm_name]["urls"].add(url)
            
    # Convert sets to lists
    for norm_name, data in sh_companies.items():
        data["urls"] = sorted(list(data["urls"]))

    # 2. Read local data
    companies_state_path = "data/companies_state.json"
    open_jobs_path = "docs/open_jobs.json"
    
    roster = []
    if os.path.exists(companies_state_path):
        with open(companies_state_path, encoding="utf-8") as f:
            roster = json.load(f)
            
    open_jobs = []
    if os.path.exists(open_jobs_path):
        with open(open_jobs_path, encoding="utf-8") as f:
            open_jobs = json.load(f)
            
    # Normalize roster names
    roster_norm_names = {normalize_company_name(c["company_name"]) for c in roster}
    
    # Identify companies in feed
    feed_norm_names = {normalize_company_name(j["company"]) for j in open_jobs}
    
    # 3. Bucket SH companies
    not_in_roster = []
    scrape_gap = []
    overlap_count = 0
    
    for norm_name, data in sh_companies.items():
        if norm_name not in roster_norm_names:
            not_in_roster.append(data)
        elif norm_name not in feed_norm_names:
            scrape_gap.append(data)
        else:
            overlap_count += 1
            
    # Sort for consistent output
    not_in_roster.sort(key=lambda x: x["company_name"].lower())
    scrape_gap.sort(key=lambda x: x["company_name"].lower())
    
    # 4. Output results
    out_data = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "not_in_roster": not_in_roster,
        "scrape_gap": scrape_gap,
        "overlap": overlap_count
    }
    
    os.makedirs("data", exist_ok=True)
    with open("data/sh_reconcile.json", "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2, ensure_ascii=False)
        
    # 5. Log summary line
    total_sh = len(sh_companies)
    num_not_in_roster = len(not_in_roster)
    num_scrape_gap = len(scrape_gap)
    logging.info(f"SH reconcile: {total_sh} companies — {num_not_in_roster} not in roster, {num_scrape_gap} scrape gaps, {overlap_count} overlap")

if __name__ == "__main__":
    main()
