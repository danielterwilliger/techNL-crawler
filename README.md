# techNL-crawler

A rolling aggregator of **tech job listings from Newfoundland & Labrador companies**.

Companies in the province don't post their openings in one central place. This tool
walks the [techNL member directory](https://members.technl.ca/memberdirectory), finds
each company's careers page, scrapes the open listings, and publishes them as an open
feed — so job seekers can browse everything in one spot.

> **Status:** actively being generalized from a personal pipeline into a public tool.
> See the [open issues](https://github.com/danielterwilliger/techNL-crawler/issues) for the roadmap.

## How to use it (no setup)

You don't need to run anything. Just read the output:

- **Dashboard:** _(GitHub Pages link — coming in Phase 5)_
- **Raw feed:** [`data/open_jobs.json`](data/open_jobs.json) — machine-readable, for your own tooling.

## How it works

```
discover → map careers pages → scrape listings → publish
```

| Stage | File | LLM? | Where it runs |
| --- | --- | --- | --- |
| 1. Discover companies | `src/discover.py` | no | GitHub Actions |
| 2. Map careers pages (heuristic) | `src/map_careers.py` | no | GitHub Actions |
| 2b. Map careers pages (fallback) | `src/map_llm_fallback.py` | yes | operator's machine |
| 3. Scrape listings | `src/scrape.py` | optional | Actions (baseline) / operator (deep) |
| 4. Publish feed + dashboard | `src/publish.py` | no | GitHub Actions |

### Public code, open output — bring your own LLM key

The heuristic stages and the dashboard run with **no credentials**, free, in GitHub
Actions. The LLM-assisted stages (mapping the tricky careers pages, deep extraction)
need an LLM credential — the **operator supplies their own**; nothing secret is ever
committed. The model-rotation "quota solver" that keeps free tiers happy is open code,
not a secret. If you fork this, plug in your own key to run the LLM stages; otherwise
you still get everything the keyless stages produce.

## Running it yourself

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv run python src/discover.py     # refresh the company roster
uv run python src/scrape.py       # scrape known careers pages → data/open_jobs.json
```

## Data

- `data/techNL_companies.md` — source-of-truth company roster
- `data/companies_state.json` — companies + mapped careers-page URLs + status
- `data/open_jobs.json` — the published job feed
