# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Stage 4 — Publish the public feed + dashboard data (keyless, no LLM).

Reads the rolling feed (data/open_jobs.json), writes the Pages-served copy
(docs/open_jobs.json) plus a small summary (docs/summary.json) that the dashboard
shows at the top. The dashboard itself (docs/index.html) is static and fetches
these at runtime, so there is no build step.

Usage:
  python src/publish.py
"""

import datetime as dt
import json
import os

FEED_IN = "data/open_jobs.json"
FEED_OUT = "docs/open_jobs.json"
SUMMARY_OUT = "docs/summary.json"
NEW_WINDOW_DAYS = 7


def main():
    jobs = []
    if os.path.exists(FEED_IN):
        with open(FEED_IN, encoding="utf-8") as f:
            jobs = json.load(f)

    today = dt.date.today()
    open_jobs = [j for j in jobs if j.get("status") == "open"]

    def is_new(j):
        try:
            return (today - dt.date.fromisoformat(j.get("first_seen", ""))).days <= NEW_WINDOW_DAYS
        except Exception:
            return False

    companies = sorted({j["company"] for j in open_jobs})
    boards = sorted({j.get("source_board", "own-site") for j in open_jobs})
    summary = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "open_count": len(open_jobs),
        "new_count": sum(1 for j in open_jobs if is_new(j)),
        "company_count": len(companies),
        "total_tracked": len(jobs),
        "companies": companies,
        "boards": boards,
    }

    os.makedirs("docs", exist_ok=True)
    # The dashboard only needs OPEN jobs; closed ones stay in data/ for history.
    with open(FEED_OUT, "w", encoding="utf-8") as f:
        json.dump(open_jobs, f, ensure_ascii=False, separators=(",", ":"))
    with open(SUMMARY_OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"Published {summary['open_count']} open jobs "
          f"({summary['new_count']} new) from {summary['company_count']} companies "
          f"-> {FEED_OUT}")


if __name__ == "__main__":
    main()
