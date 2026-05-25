# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx",
# ]
# ///
"""Stage 1 — Company discovery (keyless, no LLM).

Rebuilds the company roster authoritatively from the live techNL member directory
each run, so the data self-heals instead of accumulating cruft:

  * Source of truth = the live directory listing.
  * Each company is keyed by its stable techNL **member id** (the trailing number
    in its detail-page slug), so renames don't create duplicate entries.
  * The company's website is re-fetched from its techNL detail page every run, so
    stale/dead URLs get corrected.
  * Existing careers-page mappings (career_page_url/status/map_method) are carried
    forward for companies that persist — we never re-map work already done, unless
    the website changed.
  * Companies that have left the directory are marked status="inactive" (history
    preserved), not deleted.

Outputs:
  data/companies_state.json  (canonical state)
  data/techNL_companies.md   (human-readable roster)
"""

import asyncio
import datetime as dt
import html
import json
import os
import re
from urllib.parse import urlparse

import httpx

DIRECTORY_URL = "https://members.technl.ca/memberdirectory/FindStartsWith?term=%23%21"
DETAIL_BASE = "https://members.technl.ca/memberdirectory/Details/"
STATE_FILE = "data/companies_state.json"
MD_FILE = "data/techNL_companies.md"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

LIST_RE = re.compile(
    r'href="?//members\.technl\.ca/memberdirectory/Details/([^"]+)"?[^>]*>([^<]+)</a>',
    re.IGNORECASE)
ID_RE = re.compile(r"-(\d+)$")
WEBSITE_RE = re.compile(r'gz-details-website.*?href="([^"]+)"', re.IGNORECASE | re.DOTALL)
LOCATION_RE = re.compile(r">\s*([A-Z][A-Za-z.\'\- ]+,\s*NL)\s*<")


def norm_domain(url: str | None) -> str:
    if not url:
        return ""
    if not re.match(r"^https?://", url):
        url = "https://" + url
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def load_state() -> list[dict]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


async def fetch_directory(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(DIRECTORY_URL, timeout=30.0)
    r.raise_for_status()
    seen, companies = set(), []
    for m in LIST_RE.finditer(r.text):
        slug = m.group(1).strip()
        name = html.unescape(m.group(2).strip())
        idm = ID_RE.search(slug)
        member_id = idm.group(1) if idm else slug
        if not name or member_id in seen:
            continue
        seen.add(member_id)
        companies.append({
            "member_id": member_id,
            "company_name": name,
            "detail_url": DETAIL_BASE + slug,
        })
    return companies


async def fetch_detail(client: httpx.AsyncClient, detail_url: str, retries: int = 1) -> tuple[str | None, str | None]:
    for attempt in range(retries + 1):
        try:
            r = await client.get(detail_url, timeout=15.0)
            if r.status_code >= 400:
                return None, None
            web = WEBSITE_RE.search(r.text)
            loc = LOCATION_RE.search(r.text)
            website = web.group(1).strip() if web else None
            location = loc.group(1).strip() if loc else None
            return website, location
        except Exception:
            if attempt < retries:
                await asyncio.sleep(0.5)
                continue
            return None, None
    return None, None


def build_indexes(old: list[dict]):
    by_id, by_domain, by_name = {}, {}, {}
    for c in old:
        if c.get("member_id"):
            by_id[str(c["member_id"])] = c
        dom = norm_domain(c.get("website_url"))
        if dom:
            by_domain.setdefault(dom, c)
        by_name[c["company_name"].lower()] = c
    return by_id, by_domain, by_name


async def main():
    old = load_state()
    by_id, by_domain, by_name = build_indexes(old)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True) as client:
        print("Fetching live techNL member directory...")
        live = await fetch_directory(client)
        print(f"  {len(live)} companies in the live directory.")

        sem = asyncio.Semaphore(8)

        async def enrich(c: dict):
            async with sem:
                c["website_url"], c["location"] = await fetch_detail(client, c["detail_url"])

        print("Re-fetching websites from detail pages...")
        await asyncio.gather(*(enrich(c) for c in live))

    # Reconcile against prior state. A persisting company keeps its careers-page
    # mapping regardless of whether its marketing-site domain changed — the careers
    # page is still theirs. Match by stable id first, then exact name, then domain.
    new_state, carried, fresh = [], 0, 0
    for c in live:
        prior = (by_id.get(c["member_id"])
                 or by_name.get(c["company_name"].lower())
                 or by_domain.get(norm_domain(c.get("website_url"))))
        # Never overwrite a known website/location with a failed (None) re-fetch.
        website = c.get("website_url") or (prior.get("website_url") if prior else None)
        location = c.get("location") or (prior.get("location") if prior else None)
        rec = {
            "member_id": c["member_id"],
            "company_name": c["company_name"],
            "website_url": website,
            "location": location,
            "career_page_url": None,
            "status": "pending",
            "map_method": None,
            "last_checked": None,
        }
        if prior:
            if prior.get("career_page_url"):
                rec["career_page_url"] = prior["career_page_url"]
                rec["status"] = prior.get("status") or "active"
                rec["map_method"] = prior.get("map_method")
                rec["last_checked"] = prior.get("last_checked")
                carried += 1
            elif prior.get("status") == "failed":
                rec["status"] = "failed"
                rec["last_checked"] = prior.get("last_checked")
        else:
            fresh += 1
        new_state.append(rec)

    # Companies that left the directory -> inactive (keep history).
    live_ids = {c["member_id"] for c in live}
    live_names = {x["company_name"].lower() for x in live}
    departed = 0
    for c in old:
        if str(c.get("member_id")) not in live_ids and c.get("company_name", "").lower() not in live_names:
            if c.get("status") != "inactive":
                departed += 1
            c["status"] = "inactive"
            new_state.append(c)

    new_state.sort(key=lambda c: c["company_name"].lower())

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(new_state, f, indent=2, ensure_ascii=False)

    # Human-readable roster.
    lines = ["# techNL Member Companies (auto-generated by src/discover.py)",
             "", f"_Last refreshed: {now}_", "",
             "| Company | Location | Website | Careers page |",
             "| :--- | :--- | :--- | :--- |"]
    for c in new_state:
        if c.get("status") == "inactive":
            continue
        web = f"[{norm_domain(c['website_url'])}]({c['website_url']})" if c.get("website_url") else "—"
        car = f"[link]({c['career_page_url']})" if c.get("career_page_url") else "—"
        lines.append(f"| {c['company_name']} | {c.get('location') or '—'} | {web} | {car} |")
    with open(MD_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nRebuilt roster: {len(live)} live "
          f"({carried} mappings carried over, {fresh} new -> pending), "
          f"{departed} newly marked inactive.")
    print(f"Wrote {STATE_FILE} and {MD_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
