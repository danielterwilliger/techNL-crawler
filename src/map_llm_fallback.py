# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Stage 2b — LLM careers-page mapper (fallback for the hard tail). BYO credential.

For companies the keyless heuristic mapper (map_careers.py) couldn't crack —
careers pages on a different domain, JS-only boards, or ones only findable via
search — ask the LLM (via the gemini CLI's agentic web access) to find the URL.

This needs an operator credential, so it runs on the producer box (the Plex box),
NOT in GitHub Actions. It pushes the recovered mappings back to the repo, where the
keyless Actions pipeline then publishes them. See src/llm.py for the BYO-key story.

Usage (on a box with the gemini CLI logged in):
  python src/map_llm_fallback.py                 # map status=='failed' + 'pending'
  python src/map_llm_fallback.py --limit 10
  python src/map_llm_fallback.py --dry-run
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from llm import llm_call, LLMError

STATE_FILE = "data/companies_state.json"

SYSTEM = ("You are an expert web researcher. Find the official careers/jobs page for a "
          "company. Always anchor searches with the company's own domain (e.g. "
          "'site:domain.com careers') to avoid name collisions with unrelated entities. "
          "The page may be on their own site (/careers, /carrieres) or an ATS "
          "(Greenhouse, Lever, BambooHR, Ashby, Workday, etc.).")


def prompt_for(company: str, website: str) -> str:
    return (f"Target company: {company}\nOfficial website: {website}\n\n"
            "Find this company's official careers/jobs page URL. "
            "Output ONLY a JSON object: {\"career_page_url\": \"<url>\"} if found, "
            "or {\"career_page_url\": null} if there is no findable careers page. "
            "No prose, no markdown — JSON only.")


def parse_url(text: str) -> str | None:
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0)).get("career_page_url")
        except Exception:
            pass
    # last resort: a bare URL anywhere in the output
    u = re.search(r"https?://[^\s\"']+", text)
    return u.group(0) if u else None


def main():
    ap = argparse.ArgumentParser(description="LLM careers-page fallback mapper")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--statuses", default="failed,pending",
                    help="comma list of statuses to target (default failed,pending)")
    args = ap.parse_args()

    targets_status = {s.strip() for s in args.statuses.split(",")}
    with open(STATE_FILE, encoding="utf-8") as f:
        state = json.load(f)

    targets = [c for c in state if c.get("status") in targets_status and c.get("website_url")]
    if args.limit:
        targets = targets[: args.limit]
    print(f"LLM-mapping {len(targets)} companies (statuses: {sorted(targets_status)})...")

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    recovered = 0
    for i, c in enumerate(targets, 1):
        name, web = c["company_name"], c["website_url"]
        try:
            out = llm_call(prompt_for(name, web), system=SYSTEM, ground=True)
            url = parse_url(out)
        except LLMError as e:
            print(f"  [{i}/{len(targets)}] ! {name}: LLM failed ({e})")
            continue

        c["last_checked"] = now
        if url:
            recovered += 1
            c["career_page_url"] = url
            c["status"] = "active"
            c["map_method"] = "llm"
            print(f"  [{i}/{len(targets)}] ✓ {name}: {url}")
        else:
            c["status"] = "failed"
            print(f"  [{i}/{len(targets)}] ✗ {name}: none found")

        if not args.dry_run:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

    print(f"\nRecovered {recovered}/{len(targets)} via LLM.")
    if args.dry_run:
        print("(dry-run: state not written)")


if __name__ == "__main__":
    main()
