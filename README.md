# techNL-crawler

A rolling, self-updating board of **tech job listings from Newfoundland & Labrador companies** — built around a *reasoning engine that navigates each careers page*, not a brittle scraper.

Companies in the province don't post their openings in one central place. This tool walks the [techNL member directory](https://members.technl.ca/memberdirectory), figures out where each company actually posts its jobs, and publishes the open listings as one browsable feed.

**▶ Live dashboard: https://danielterwilliger.github.io/techNL-crawler/**

## Just want the jobs?

No setup. Read the output:

- **Dashboard** — search/filter by company, source, keyword; new-this-week badges: the link above.
- **Raw feed** — [`docs/open_jobs.json`](docs/open_jobs.json) (open listings) for your own tooling.

Each listing links to the company's own posting, validated to resolve (no dead/landing-page links).

## Why a reasoning engine

NL companies expose jobs every possible way, and no fixed ruleset covers them all:

| Pattern | Example |
| --- | --- |
| Roles listed on the careers page | Focus FS |
| Per-posting links to an ATS | CoLab → Greenhouse |
| A "View Jobs" button to an ATS on another domain | Verafin → Workday, Kraken → Rippling, Mysa → Lever, Avalon → ADP |
| Recruiter / aggregator boards (jobs at other firms) | Venor |
| Jobs only on LinkedIn / Indeed | (recorded as pointers) |
| Anti-bot walls, or genuinely no openings | (handled gracefully) |

So instead of pattern-matching, the crawler **navigates in stages and decides at each hop** — the way a person would.

## How it works

```
discover → map careers pages → navigate & scrape → publish
```

The scraper is a **staged navigator** (`src/navigate.py`):

```
render (Playwright)  →  route (heuristics first, LLM when uncertain)
  →  follow landing→ATS / paginate / extract on-page roles / record pointer
  →  validate every posting URL  →  rolling feed
```

| Stage | File | LLM? |
| --- | --- | --- |
| Discover companies (live techNL roster, self-healing) | `src/discover.py` | no |
| Map careers-page URLs (heuristic: ATS detection + path probing) | `src/map_careers.py` | no |
| Map fallback (hard tail) | `src/map_llm_fallback.py` | yes |
| Navigate + scrape listings | `src/navigate.py` + `src/scrape.py` | optional |
| Reusable LLM client (model-rotation quota solver) | `src/llm.py` | — |
| Publish feed + dashboard data | `src/publish.py` | no |

### Open code, open output — bring your own LLM credential

The heuristic stages and the dashboard run **keyless** (free, in GitHub Actions). The
LLM reasoning (navigating ambiguous/custom/JS pages) needs a credential — the
**operator supplies their own**; nothing secret is ever committed. The model-rotation
"quota solver" that survives free-tier limits is open code, not a secret. Fork this and
plug in your own key for the LLM stages; without one you still get everything the
keyless stages produce.

## Architecture (this instance)

Two layers:

- **Producer** (`deploy/`, a homelab box): runs the full LLM-powered navigator nightly
  on rolling batches (a Gemini API key; see `src/llm.py`), and pushes the enriched feed.
- **GitHub** (`.github/workflows/`): `pages.yml` rebuilds + deploys the dashboard
  whenever the feed changes; `crawl.yml` is a **dispatch-only keyless** crawl for forks
  / manual use (never auto-overwrites the producer's data).

A fork with no producer just uses `crawl.yml` and gets the keyless baseline.

## Run it yourself

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv run python src/discover.py        # refresh the company roster
uv run python src/map_careers.py     # heuristic careers-page mapping
uv run python src/scrape.py          # keyless navigator → data/open_jobs.json
uv run python src/scrape.py --llm    # full navigator (needs a credential; see src/llm.py)
uv run python src/publish.py         # build the dashboard data
```

To stand up a nightly **producer** on your own box, see [`deploy/PROVISIONING.md`](deploy/PROVISIONING.md).

## Data

- `data/techNL_companies.md` — human-readable company roster
- `data/companies_state.json` — companies + mapped careers-page URLs + status
- `data/open_jobs.json` — the rolling job feed (open + recently-closed, with history)
- `data/pointers.json` — companies whose jobs live on LinkedIn/Indeed or behind a bot-wall
- `docs/` — the published dashboard + its feed (GitHub Pages root)
