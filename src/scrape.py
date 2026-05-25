# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "beautifulsoup4",
#   "playwright",
# ]
# ///
"""Stage 3 — Career-board scraper with rolling history (keyless baseline).

Crawls each active company's careers page with Playwright, extracts direct job
posting links (standard ATS URL patterns + own-site job paths), fetches each
posting's text, and merges the results into a rolling feed at data/open_jobs.json.

History semantics (so the feed reflects what's actually open over time):
  * first_seen / last_seen track a job's lifetime across runs.
  * A job re-found this run -> last_seen bumped, status "open".
  * A job NOT found this run, but whose company scraped successfully -> "closed"
    (a successful board that no longer lists it is strong evidence it's gone).
  * A job whose company FAILED to scrape this run is left untouched (transient).
  * Backstop: any job not seen in STALE_DAYS is closed regardless.

The LLM-powered deep-extraction path (for JS-only boards the baseline can't parse)
is a seam wired in Phase 3/7; the keyless baseline here needs no credentials and
runs in GitHub Actions.

Usage:
  python src/scrape.py                 # full run, merge into data/open_jobs.json
  python src/scrape.py --limit 10       # first 10 active companies (dev)
  python src/scrape.py --company "Verafin"
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
# playwright is imported lazily inside the functions that drive the browser, so
# the history/merge logic stays importable (and unit-testable) without a browser.

STATE_FILE = "data/companies_state.json"
DEFAULT_OUT_FILE = "data/open_jobs.json"

MAX_JOBS_PER_COMPANY = 15
DESCRIPTION_CAP = 1500       # chars kept in the feed; full posting is one click away
STALE_DAYS = 14              # close a job not seen for this long (≈2 weekly runs)

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
    r"career[^/]+_id=\d+",
]

# host fragment -> board label, for tagging where a posting lives
BOARD_TAGS = {
    "greenhouse.io": "greenhouse", "lever.co": "lever", "bamboohr.com": "bamboohr",
    "ashbyhq.com": "ashby", "myworkdayjobs.com": "workday", "taleo.net": "taleo",
    "recruitee.com": "recruitee", "workable.com": "workable", "jobvite.com": "jobvite",
    "smartrecruiters.com": "smartrecruiters", "icims.com": "icims", "breezy.hr": "breezy",
    "bullhorn": "bullhorn", "linkedin.com": "linkedin", "indeed.com": "indeed",
}


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading JSON from {path}: {e}")
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {path}")


def canon_url(url: str) -> str:
    """Canonical form used as the stable per-job key (strip query/fragment/trailing slash)."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/").lower()


def source_board(url: str) -> str:
    lo = url.lower()
    for frag, label in BOARD_TAGS.items():
        if frag in lo:
            return label
    return "own-site"


async def extract_links_from_page(page, base_url: str) -> list[dict]:
    """Scan the page (and relevant iframes) for direct job-posting URLs."""
    job_links = []

    def is_job_link(url: str) -> bool:
        url_lower = url.lower()
        if any(k in url_lower for k in ["/careers", "/jobs", "/search", "/category", "/all-jobs", "?filter="]) \
                and not any(re.search(pat, url) for pat in JOB_URL_PATTERNS[:5]):
            if not re.search(r"\d{4,}", url):
                return False
        return any(re.search(pat, url) or re.search(pat, url_lower) for pat in JOB_URL_PATTERNS)

    try:
        soup = BeautifulSoup(await page.content(), "html.parser")
        for link in soup.find_all("a", href=True):
            href = link["href"].strip()
            if not href or href.startswith(("javascript:", "#", "mailto:")):
                continue
            abs_url = urljoin(page.url, href)
            title = re.sub(r"\s+", " ", link.text.strip().replace("\n", " "))
            if is_job_link(abs_url) and len(title) > 2:
                job_links.append({"title": title, "url": abs_url})
    except Exception as e:
        print(f"   -> main frame scan error: {e}")

    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if any(k in frame.url.lower() for k in ["job", "career", "embed", "apply", "board"]):
                try:
                    fsoup = BeautifulSoup(await frame.content(), "html.parser")
                    for link in fsoup.find_all("a", href=True):
                        href = link["href"].strip()
                        if not href or href.startswith(("javascript:", "#")):
                            continue
                        abs_url = urljoin(frame.url, href)
                        title = re.sub(r"\s+", " ", link.text.strip().replace("\n", " "))
                        if is_job_link(abs_url) and len(title) > 2:
                            job_links.append({"title": title, "url": abs_url})
                except Exception:
                    pass
    except Exception:
        pass

    seen, deduped = set(), []
    for jl in job_links:
        key = canon_url(jl["url"])
        if key not in seen:
            seen.add(key)
            deduped.append(jl)
    return deduped


async def scrape_job_description(context, url: str) -> str:
    page = None
    try:
        page = await context.new_page()
        await page.route("**/*.{png,jpg,jpeg,gif,webp,css,svg,woff2}", lambda r: r.abort())
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.0)
        text = await page.evaluate("document.body.innerText")
        return (text or "").strip()
    except Exception as e:
        print(f"   -> failed to scrape description for {url}: {e}")
        return ""
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def scrape_company(context, comp: dict) -> tuple[bool, list[dict]]:
    """Return (scrape_succeeded, [job dicts]) for one company."""
    name, url = comp["company_name"], comp["career_page_url"]
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2.5)
        links = await extract_links_from_page(page, url)
        print(f"  {name}: {len(links)} job link(s)")
        jobs = []
        for jl in links[:MAX_JOBS_PER_COMPANY]:
            desc = await scrape_job_description(context, jl["url"])
            jobs.append({
                "company": name,
                "title": jl["title"],
                "url": jl["url"],
                "location": comp.get("location"),
                "source_board": source_board(jl["url"]),
                "description": desc[:DESCRIPTION_CAP],
            })
            await asyncio.sleep(0.4)
        return True, jobs
    except Exception as e:
        print(f"  {name}: board scrape FAILED ({e})")
        return False, []
    finally:
        try:
            await page.close()
        except Exception:
            pass


def merge_history(existing: list[dict], found: list[dict],
                  scraped_ok_companies: set[str], today: str) -> list[dict]:
    """Merge this run's findings into the rolling feed."""
    by_url = {canon_url(j["url"]): j for j in existing}

    # Upsert everything found this run.
    for j in found:
        key = canon_url(j["url"])
        if key in by_url:
            rec = by_url[key]
            rec.update({
                "title": j["title"], "location": j["location"],
                "source_board": j["source_board"], "description": j["description"],
                "last_seen": today, "status": "open",
            })
        else:
            by_url[key] = {**j, "first_seen": today, "last_seen": today, "status": "open"}

    found_keys = {canon_url(j["url"]) for j in found}
    for key, rec in by_url.items():
        if key in found_keys:
            continue
        # Not found this run.
        company_ok = rec["company"] in scraped_ok_companies
        try:
            age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(rec["last_seen"])).days
        except Exception:
            age = 0
        if company_ok or age >= STALE_DAYS:
            rec["status"] = "closed"

    # Sort: open first, newest first_seen first.
    return sorted(by_url.values(),
                  key=lambda r: (r["status"] != "open", r.get("first_seen", "")),
                  reverse=False)


async def main():
    ap = argparse.ArgumentParser(description="techNL career-board scraper")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--company", type=str, default=None)
    ap.add_argument("--out", type=str, default=DEFAULT_OUT_FILE)
    args = ap.parse_known_args()[0]

    state = load_json(STATE_FILE, [])
    companies = [c for c in state if c.get("status") == "active" and c.get("career_page_url")]
    if args.company:
        companies = [c for c in companies if c["company_name"].lower() == args.company.lower()]
    elif args.limit:
        companies = companies[: args.limit]

    from playwright.async_api import async_playwright

    today = dt.date.today().isoformat()
    print(f"Scraping {len(companies)} active boards on {today}...")

    found, scraped_ok = [], set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        for comp in companies:
            ok, jobs = await scrape_company(context, comp)
            if ok:
                scraped_ok.add(comp["company_name"])
            found.extend(jobs)
        await browser.close()

    existing = load_json(args.out, [])
    merged = merge_history(existing, found, scraped_ok, today)

    n_open = sum(1 for j in merged if j["status"] == "open")
    n_new = sum(1 for j in merged if j.get("first_seen") == today and j["status"] == "open")
    save_json(args.out, merged)
    print(f"\nFound {len(found)} listings across {len(scraped_ok)}/{len(companies)} boards. "
          f"Feed: {n_open} open ({n_new} new today), {len(merged)} total tracked.")


if __name__ == "__main__":
    asyncio.run(main())
