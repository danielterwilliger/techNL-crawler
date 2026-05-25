# AGENTS.md — orientation for an AI agent

If you're an AI agent picking up this repo cold, read this first, then `README.md`.
**This repo is public — never commit secrets, SSH coordinates, IPs, tokens, or
OAuth/API credentials.**

## What this is

A rolling, self-updating board of **tech job listings from Newfoundland & Labrador
companies**, built around a reasoning engine that *navigates* each careers page
rather than a brittle scraper. It walks the techNL member directory, figures out
where each company actually posts jobs (on-page list, ATS link-out, aggregator,
LinkedIn/Indeed pointer, …), and publishes one validated feed.

- **Live dashboard:** https://danielterwilliger.github.io/techNL-crawler/
- **Raw feed:** `docs/open_jobs.json`

## Layout

```
src/        discover.py  → find member companies
            map_careers.py → heuristic careers-page mapping (ATS detection)
            map_llm_fallback.py → LLM mapping fallback (OAuth; minimize calls)
            navigate.py  → render → route → follow-to-ATS → paginate → extract → validate
            scrape.py    → thin driver over navigate.py (--llm gates LLM extraction)
            publish.py   → write open_jobs.json + dashboard feeds
            llm.py       → LLM engine (gemini CLI via OAuth, or API key)
data/       companies_state.json, known_jobs.json, techNL_companies.md, open_jobs.json
docs/       index.html dashboard + published JSON feeds (served by GitHub Pages)
deploy/     systemd units (technl-producer.{service,timer}, technl-dashboard.service)
            + PROVISIONING.md (how the box is set up)
scripts/    run_producer.sh (the nightly producer entrypoint)
.github/    pages.yml (publish+deploy on feed push), crawl.yml (dispatch-only keyless)
```

## How it runs

- **Public layer:** keyless crawl in GitHub Actions; publishes to Pages. Forks run
  with zero secrets.
- **Private layer:** a nightly **systemd producer** on the maintainer's deploy box
  does the LLM-enriched crawl (OAuth via the `gemini` CLI) and pushes the feed, which
  triggers `pages.yml` to deploy. See `deploy/PROVISIONING.md`.

## If you have deploy-box access (maintainer's agent only)

The producer runs on a private box. Connect via the `ssh plex` / `ssh plex-ts` alias
already configured in your local `~/.ssh/config` — **the connection details are not
in this repo and must not be added to it.** The repo is cloned at `~/techNL-crawler`
on the box (read-write deploy key). It's a shared, CPU-constrained machine: the
producer is deliberately a good tenant (`Nice=10`, off-peak, bounded batch, port 8088).

## House rules

- **GitHub flow always:** file an issue → branch → PR. Don't commit straight to `main`.
- Roadmap and state are in **Issues #1–#7** and the merged PR history.
- LLM has a **daily OAuth quota** → heuristic-first; reserve LLM for extraction and
  follow-to-ATS navigation, not bulk mapping.
- Each published listing must link to a posting that **validates** (no dead/landing
  links).

## Get oriented fast

```bash
gh issue list --repo danielterwilliger/techNL-crawler --state open
```
