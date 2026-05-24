"""Programmatic Career Board Scraper (Newfoundland & Labrador Tech Jobs Aggregator).

This script programmatically crawls company career pages using Playwright,
extracts all active job posting links (leveraging patterns for standard ATS platforms
like Greenhouse, Lever, BambooHR, Ashby, Workday, etc.), fetches the full-text
description of each job, and saves the aggregated results to open_jobs.json.

It runs completely offline and without LLM costs, making it ideal as a public community tool.
"""

import asyncio
import json
import os
import re
import argparse
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

STATE_FILE = "techNL_crawler/companies_state.json"
DEFAULT_OUT_FILE = "open_jobs.json"

# Common job/apply patterns in URLs to identify direct job posts
JOB_URL_PATTERNS = [
    r"boards\.greenhouse\.io/[^/]+/jobs/\d+",
    r"jobs\.lever\.co/[^/]+/[0-9a-fA-F-]+",
    r"[^/]+\.bamboohr\.com/careers/\d+",
    r"ashbyhq\.com/[^/]+/[^/]+",
    r"myworkdayjobs\.com/[^/]+/job/[^/]+",
    r"/jobs?/\d+",
    r"/careers?/\d+",
    r"/apply?/\d+",
    r"/job/[^/]+",
    r"/vacancy/[^/]+",
    r"career[^/]+_id=\d+"
]

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading JSON from {path}: {e}")
    return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Successfully saved to: {path}")
    except Exception as e:
        print(f"Error saving JSON to {path}: {e}")

async def extract_links_from_page(page, base_url: str) -> list[dict]:
    """Scans the page and all iframes to extract potential direct job posting URLs."""
    job_links = []
    
    # 1. Helper to test if a URL looks like a direct job post
    def is_job_link(url: str) -> bool:
        url_lower = url.lower()
        # Avoid generic/landing pages
        if any(k in url_lower for k in ["/careers", "/jobs", "/search", "/category", "/all-jobs", "?filter="]) and not any(re.search(pat, url) for pat in JOB_URL_PATTERNS[:5]):
            if not re.search(r"\d{4,}", url): # If no ID is present
                return False
        return any(re.search(pat, url) or re.search(pat, url_lower) for pat in JOB_URL_PATTERNS)

    # 2. Extract links from main frame
    try:
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        for link in soup.find_all('a', href=True):
            href = link['href'].strip()
            if not href or href.startswith("javascript:") or href.startswith("#") or href.startswith("mailto:"):
                continue
            abs_url = urljoin(page.url, href)
            title = link.text.strip().replace("\n", " ")
            title = re.sub(r'\s+', ' ', title)
            if is_job_link(abs_url) and len(title) > 2:
                job_links.append({"title": title, "url": abs_url})
    except Exception as e:
        print(f"   -> Main frame scan error: {e}")

    # 3. Extract links from all relevant child frames (Lever embeds, Lever widgets, etc.)
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            frame_url = frame.url.lower()
            if any(k in frame_url for k in ["job", "career", "embed", "apply", "board"]):
                try:
                    frame_content = await frame.content()
                    frame_soup = BeautifulSoup(frame_content, 'html.parser')
                    for link in frame_soup.find_all('a', href=True):
                        href = link['href'].strip()
                        if not href or href.startswith("javascript:") or href.startswith("#"):
                            continue
                        abs_url = urljoin(frame.url, href)
                        title = link.text.strip().replace("\n", " ")
                        title = re.sub(r'\s+', ' ', title)
                        if is_job_link(abs_url) and len(title) > 2:
                            job_links.append({"title": title, "url": abs_url})
                except Exception:
                    pass
    except Exception:
        pass
        
    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for jl in job_links:
        clean_url = jl["url"].split("?")[0].rstrip("/") # strip query params for deduplication
        if clean_url not in seen_urls:
            seen_urls.add(clean_url)
            deduped.append(jl)
            
    return deduped

async def scrape_job_description(context, url: str) -> str:
    """Visits a direct job posting URL and extracts the raw description text."""
    page = None
    try:
        page = await context.new_page()
        # Enable lightweight loading (omit images and stylesheets to save bandwidth and speed up)
        await page.route("**/*.{png,jpg,jpeg,gif,webp,css,svg,woff2}", lambda route: route.abort())
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.0)
        text = await page.evaluate("document.body.innerText")
        return text.strip() if text else ""
    except Exception as e:
        print(f"   -> Failed to scrape description for {url}: {e}")
        return ""
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass

async def main():
    parser = argparse.ArgumentParser(description="Programmatic Tech Jobs Aggregator (techNL-crawler)")
    parser.add_argument("--limit", type=int, default=None, help="Limit to the first N active companies.")
    parser.add_argument("--company", type=str, default=None, help="Crawl only a specific company by name.")
    parser.add_argument("--out", type=str, default=DEFAULT_OUT_FILE, help="Path to save the output JSON feed.")
    args = parser.parse_known_args()[0]

    if not os.path.exists(STATE_FILE):
        print(f"Error: Companies state file not found at '{STATE_FILE}'. Please run Pipeline 1 & 2 first.")
        return

    state = load_json(STATE_FILE, [])
    active_companies = [c for c in state if c.get("status") == "active" and c.get("career_page_url")]

    if args.company:
        active_companies = [c for c in active_companies if c["company_name"].lower() == args.company.lower()]
        print(f"Filtering aggregator run to company: '{args.company}'")
    elif args.limit:
        active_companies = active_companies[:args.limit]
        print(f"Limiting aggregator run to first {args.limit} active companies.")

    print(f"Starting crawl for {len(active_companies)} active career boards...")
    all_discovered_jobs = []

    async with async_playwright() as p:
        # Launch headless browser
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        main_page = await context.new_page()

        for comp in active_companies:
            name = comp["company_name"]
            url = comp["career_page_url"]
            print(f"\nScanning {name} board ({url})...")

            try:
                await main_page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2.5) # allow JS to execute listings
                
                # Step 1: Scan for direct job links
                job_links = await extract_links_from_page(main_page, url)
                print(f" -> Found {len(job_links)} potential direct job links.")
                
                if not job_links:
                    print(" -> No job links identified on landing page. (Check if board is empty or using dynamic shadow-DOM).")
                    continue
                
                # Restrict to first 15 jobs per company to keep execution fast and prevent aggressive blocks
                job_links = job_links[:15]
                
                # Step 2: Crawl direct job postings to fetch descriptions
                company_jobs = []
                for jl in job_links:
                    title = jl["title"]
                    job_url = jl["url"]
                    print(f"   * Scraped Listing: '{title}'")
                    print(f"     Fetching description: {job_url}...")
                    
                    description = await scrape_job_description(context, job_url)
                    if description:
                        print(f"     Done (Length: {len(description)} chars)")
                    else:
                        print("     Warning: Empty description retrieved.")
                        
                    company_jobs.append({
                        "company": name,
                        "title": title,
                        "url": job_url,
                        "description": description
                    })
                    await asyncio.sleep(0.5) # soft delay between fetches
                
                all_discovered_jobs.extend(company_jobs)
                
            except Exception as e:
                print(f" -> Failed to scan career board for {name}: {e}")
                continue

        await browser.close()

    # Save final aggregated feed
    save_json(args.out, all_discovered_jobs)
    print(f"\nCrawl complete! Aggregated {len(all_discovered_jobs)} active tech jobs in Newfoundland & Labrador.")

if __name__ == "__main__":
    asyncio.run(main())
