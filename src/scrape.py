# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "beautifulsoup4",
#   "playwright",
# ]
# ///
"""Stage 3 — Job scraper: drives the staged navigator + rolling history.

The per-company intelligence (render → route → follow/paginate → extract →
validate) lives in src/navigate.py. This module orchestrates it across all active
companies and merges results into the rolling feed (data/open_jobs.json).

History semantics (so the feed reflects what's actually open over time):
  * first_seen / last_seen track a job's lifetime across runs.
  * Re-found this run -> last_seen bumped, status "open".
  * Not found, but the company scraped successfully -> "closed" (the board no
    longer lists it). Company scrape FAILED -> left untouched (transient).
  * Backstop: not seen in STALE_DAYS -> "closed".

LLM use is gated by --llm: without it the navigator is heuristic-only (direct
postings + follow known ATS hosts), which is what GitHub Actions runs keyless.
With --llm (producer box, OAuth) it also reasons over custom/JS/aggregator pages.

Usage:
  python src/scrape.py                 # heuristic-only (keyless / Actions)
  python src/scrape.py --llm            # full navigator (producer box, OAuth)
  python src/scrape.py --company "Verafin" --llm
  python src/scrape.py --limit 10
"""

import argparse
import asyncio
import datetime as dt
import json
import os

from navigate import navigate_company, canon_url

STATE_FILE = "data/companies_state.json"
DEFAULT_OUT_FILE = "data/open_jobs.json"
POINTERS_FILE = "data/pointers.json"
STALE_DAYS = 14


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


def merge_history(existing: list[dict], found: list[dict],
                  scraped_ok_companies: set[str], today: str) -> list[dict]:
    """Merge this run's findings into the rolling feed."""
    by_url = {canon_url(j["url"]): j for j in existing}

    for j in found:
        key = canon_url(j["url"])
        if key in by_url:
            rec = by_url[key]
            rec.update({k: j[k] for k in
                        ("title", "location", "source_board", "description") if k in j})
            if j.get("via"):
                rec["via"] = j["via"]
            rec["last_seen"] = today
            rec["status"] = "open"
        else:
            by_url[key] = {**j, "first_seen": today, "last_seen": today, "status": "open"}

    found_keys = {canon_url(j["url"]) for j in found}
    for key, rec in by_url.items():
        if key in found_keys:
            continue
        company_ok = rec["company"] in scraped_ok_companies
        try:
            age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(rec["last_seen"])).days
        except Exception:
            age = 0
        if company_ok or age >= STALE_DAYS:
            rec["status"] = "closed"

    return sorted(by_url.values(),
                  key=lambda r: (r["status"] != "open", r.get("first_seen", "")))


async def main():
    ap = argparse.ArgumentParser(description="techNL job scraper (staged navigator)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--company", type=str, default=None)
    ap.add_argument("--out", type=str, default=DEFAULT_OUT_FILE)
    ap.add_argument("--llm", "--llm-extract", dest="llm", action="store_true",
                    help="enable LLM reasoning for custom/JS/aggregator pages "
                         "(needs a credential; producer box only). Off = keyless.")
    args = ap.parse_known_args()[0]

    state = load_json(STATE_FILE, [])
    companies = [c for c in state if c.get("status") == "active" and c.get("career_page_url")]
    if args.company:
        companies = [c for c in companies if c["company_name"].lower() == args.company.lower()]
    elif args.limit:
        companies = companies[: args.limit]

    from playwright.async_api import async_playwright

    today = dt.date.today().isoformat()
    mode = "full navigator (LLM)" if args.llm else "heuristic-only (keyless)"
    print(f"Scraping {len(companies)} boards on {today} — {mode}...")

    found, pointers, scraped_ok = [], [], set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        for comp in companies:
            try:
                res = await navigate_company(context, comp, llm_enabled=args.llm)
            except Exception as e:
                print(f"  {comp['company_name']}: navigate error ({e})")
                continue
            if res["scraped_ok"]:
                scraped_ok.add(comp["company_name"])
            found.extend(res["jobs"])
            if res["pointer"]:
                pointers.append(res["pointer"])
        await browser.close()

    existing = load_json(args.out, [])
    merged = merge_history(existing, found, scraped_ok, today)

    n_open = sum(1 for j in merged if j["status"] == "open")
    n_new = sum(1 for j in merged if j.get("first_seen") == today and j["status"] == "open")
    save_json(args.out, merged)
    if pointers:
        save_json(POINTERS_FILE, pointers)
    print(f"\nFound {len(found)} listings across {len(scraped_ok)}/{len(companies)} boards "
          f"({len(pointers)} external pointers). "
          f"Feed: {n_open} open ({n_new} new today), {len(merged)} total tracked.")


if __name__ == "__main__":
    asyncio.run(main())
