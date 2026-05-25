# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx",
#   "beautifulsoup4",
# ]
# ///
"""Stage 2 — Heuristic careers-page mapper (keyless, no LLM).

For each company missing a verified careers page, this tries to find one WITHOUT
spending any LLM quota, by:

  1. Scanning the homepage for links whose text/href looks like a careers page,
     and for embedded ATS boards (Greenhouse, Lever, BambooHR, Ashby, Workday, ...).
  2. Probing a list of common careers-page paths on the company's own domain.
  3. Validating each candidate actually loads and looks like a careers page
     (job/careers keywords or an ATS embed) rather than a soft-404 homepage echo.

Companies it can't crack stay status="failed" for the LLM fallback (map_llm_fallback.py).

Usage:
  python src/map_careers.py                 # map new/failed/stale companies, write state
  python src/map_careers.py --only-failed    # only retry current 'failed'
  python src/map_careers.py --dry-run        # measure recovery, don't write state
  python src/map_careers.py --limit 20       # cap how many companies to process
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

STATE_FILE = "data/companies_state.json"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Common careers-page paths, most-likely first.
CANDIDATE_PATHS = [
    "/careers", "/careers/", "/career", "/careers.html", "/jobs", "/jobs/",
    "/job", "/join-us", "/join", "/work-with-us", "/work-here", "/employment",
    "/opportunities", "/current-opportunities", "/about/careers",
    "/company/careers", "/en/careers", "/careers/jobs", "/about-us/careers",
    "/get-involved/careers", "/team/careers", "/carrieres", "/carriere",
]

# Known ATS / job-board host fragments. A link to any of these is a strong signal.
ATS_HOSTS = [
    "boards.greenhouse.io", "greenhouse.io", "jobs.lever.co", "lever.co",
    "bamboohr.com", "ashbyhq.com", "myworkdayjobs.com", "taleo.net",
    "recruitee.com", "workable.com", "jobvite.com", "smartrecruiters.com",
    "breezy.hr", "icims.com", "bullhorn", "dayforcehcm.com", "applytojob.com",
    "rippling.com", "paylocity.com", "isolvedhire.com", "applicantpro.com",
    "jazz.co", "jazzhr.com", "hrmdirect.com", "careerplug.com",
]

# Words that, present on a page, suggest it really is a careers/jobs page.
CAREERS_WORDS = [
    "career", "job", "position", "vacancy", "opening", "opportunit",
    "join our team", "join the team", "we're hiring", "we are hiring",
    "apply now", "current opening", "open role", "employment",
]

# Link text that suggests a nav link points to the careers page.
LINK_TEXT_HINTS = [
    "career", "job", "join", "work with us", "work here", "we're hiring",
    "opportunit", "employment", "open positions", "vacanc",
]


def load_state() -> list[dict]:
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: list[dict]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_ats_url(url: str) -> bool:
    u = url.lower()
    return any(host in u for host in ATS_HOSTS)


def root_url(website: str) -> str | None:
    """Return scheme://host for a website URL, or None if unusable."""
    if not website:
        return None
    if not re.match(r"^https?://", website):
        website = "https://" + website
    p = urlparse(website)
    if not p.netloc:
        return None
    return f"{p.scheme}://{p.netloc}"


def careers_score(url: str, html: str, base_host: str) -> int:
    """Score how strongly a fetched page looks like a real careers page."""
    score = 0
    lo = html.lower()

    # ATS embed in the page body is the strongest own-page signal.
    if any(host in lo for host in ATS_HOSTS):
        score += 5

    # Careers keyword density.
    hits = sum(lo.count(w) for w in CAREERS_WORDS)
    score += min(hits, 6)

    # The URL path itself looking careers-y.
    path = urlparse(url).path.lower()
    if re.search(r"car(e|rie)|/jobs?|join|employ|opportunit|vacanc", path):
        score += 3

    # Presence of multiple "apply"/job links hints at a real board.
    apply_links = len(re.findall(r"apply|/job[s]?/|/career", lo))
    score += min(apply_links // 3, 3)

    return score


TIMEOUT = httpx.Timeout(7.0, connect=4.0)
PER_COMPANY_BUDGET = 35.0  # seconds, hard cap so one slow site can't stall the run


async def fetch(client: httpx.AsyncClient, url: str, retries: int = 1) -> httpx.Response | None:
    for attempt in range(retries + 1):
        try:
            r = await client.get(url, timeout=TIMEOUT)
            if r.status_code < 400 and "html" in r.headers.get("content-type", "text/html"):
                return r
            return None
        except Exception:
            if attempt < retries:
                await asyncio.sleep(0.5)
                continue
            return None
    return None


def toggle_www(base: str) -> str:
    p = urlparse(base)
    host = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
    return f"{p.scheme}://{host}"


async def resolve_home(client: httpx.AsyncClient, base: str):
    """Return (working_base, homepage_response) trying both www and non-www."""
    for cand in (base, toggle_www(base)):
        r = await fetch(client, cand)
        if r is not None:
            return f"{urlparse(str(r.url)).scheme}://{urlparse(str(r.url)).netloc}", r
    return base, None


async def find_careers_page(client: httpx.AsyncClient, website: str) -> dict | None:
    """Return {'url':..., 'method':..., 'score':...} or None."""
    base0 = root_url(website)
    if not base0:
        return None

    candidates: list[tuple[str, str]] = []  # (url, why)

    # --- 1. Scan homepage for careers links + ATS embeds (try www + non-www) ---
    base, home = await resolve_home(client, base0)
    base_host = urlparse(base).netloc
    if home is not None:
        soup = BeautifulSoup(home.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_url = urljoin(str(home.url), href)
            text = re.sub(r"\s+", " ", a.get_text(" ").strip().lower())
            if is_ats_url(abs_url):
                candidates.append((abs_url, "homepage-ats-link"))
            elif any(h in text for h in LINK_TEXT_HINTS) or \
                    re.search(r"car(e|rie)|/jobs?|join-us|employ", href.lower()):
                candidates.append((abs_url, "homepage-careers-link"))

        # Also catch ATS boards embedded via iframe/script src.
        for tag in soup.find_all(["iframe", "script"], src=True):
            src = urljoin(str(home.url), tag["src"].strip())
            if is_ats_url(src):
                candidates.append((src, "homepage-ats-embed"))

    # --- 2. Probe common paths on the company domain ---
    for path in CANDIDATE_PATHS:
        candidates.append((base + path, "path-probe"))

    # De-dup, preserving order/priority.
    seen, ordered = set(), []
    for url, why in candidates:
        key = url.split("#")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            ordered.append((url, why))

    # --- 3. Validate candidates concurrently (bounded per company), then score ---
    csem = asyncio.Semaphore(6)

    async def check(url: str, why: str) -> dict | None:
        async with csem:
            r = await fetch(client, url)
        if r is None:
            return None
        final = str(r.url)
        if is_ats_url(final):
            return {"url": final, "method": why, "score": 10}
        # Reject soft-404s that bounced back to the homepage root.
        if urlparse(final).path.rstrip("/") in ("", "/") and why == "path-probe":
            return None
        sc = careers_score(final, r.text, base_host)
        return {"url": final, "method": why, "score": sc} if sc >= 4 else None

    checked = await asyncio.gather(*(check(u, w) for u, w in ordered))
    hits = [c for c in checked if c]
    if not hits:
        return None
    # Prefer ATS / highest score; ties broken by candidate priority (gather order).
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[0]


async def process(companies: list[dict], concurrency: int = 6) -> dict:
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, dict | None] = {}
    done = 0
    total = len(companies)

    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, follow_redirects=True, http2=False,
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
    ) as client:
        async def worker(c: dict):
            nonlocal done
            name = c["company_name"]
            async with sem:
                website = c.get("website_url") or ""
                try:
                    res = await asyncio.wait_for(
                        find_careers_page(client, website), timeout=PER_COMPANY_BUDGET)
                except (asyncio.TimeoutError, Exception):
                    res = None
                results[name] = res
            done += 1
            mark = "✓" if res else "✗"
            tail = f"  {res['url']}  [{res['method']}, score {res['score']}]" if res else ""
            print(f"  [{done:>2}/{total}] {mark} {name}{tail}", flush=True)

        await asyncio.gather(*(worker(c) for c in companies))
    return results


def needs_mapping(c: dict, only_failed: bool, stale_days: int = 30) -> bool:
    status = c.get("status")
    if status == "inactive":
        return False
    if only_failed:
        return status == "failed"
    if status == "failed" or not c.get("career_page_url"):
        return True
    # stale re-verification
    last = c.get("last_checked")
    if not last:
        return False
    try:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last)).days
        return age > stale_days
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="Heuristic careers-page mapper")
    ap.add_argument("--only-failed", action="store_true", help="only retry status=='failed'")
    ap.add_argument("--dry-run", action="store_true", help="don't write state; just report")
    ap.add_argument("--limit", type=int, default=None, help="cap companies processed")
    args = ap.parse_args()

    state = load_state()
    by_name = {c["company_name"]: c for c in state}

    targets = [c for c in state if c.get("website_url") and needs_mapping(c, args.only_failed)]
    if args.limit:
        targets = targets[: args.limit]

    print(f"Heuristic mapping {len(targets)} companies "
          f"({'failed only' if args.only_failed else 'new/failed/stale'})...")

    results = asyncio.run(process(targets))

    recovered = 0
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for name, res in results.items():
        c = by_name[name]
        c["last_checked"] = now
        if res:
            recovered += 1
            c["career_page_url"] = res["url"]
            c["status"] = "active"
            c["map_method"] = "heuristic"
        else:
            c["status"] = "failed"

    n = len(targets) or 1
    print(f"\nRecovered {recovered}/{len(targets)} "
          f"({recovered / n * 100:.0f}%) without an LLM.")

    if args.dry_run:
        print("(dry-run: state not written)")
    else:
        save_state(state)
        print(f"Wrote {STATE_FILE}")


if __name__ == "__main__":
    main()
