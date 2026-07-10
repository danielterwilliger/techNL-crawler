#!/usr/bin/env bash
# techNL-crawler producer run (the LLM-navigator crawl).
#
# Runs on the operator's box (the Plex box), where the API credential lives. Does
# the full pipeline including the LLM reasoning the keyless GitHub Actions run can't:
# discover -> heuristic map -> navigator scrape (--llm) -> publish, then pushes the
# enriched data back to the repo.
#
# Auth: an AI Studio Gemini API key in .env.producer (GEMINI_API_KEY), used by the
# "api" engine in src/llm.py (a direct REST call). The legacy OAuth "cli" engine is
# still available (TECHNL_LLM_ENGINE=cli) but is not the production path.
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUNBUFFERED=1          # stream per-company progress to the journal live
export TECHNL_LLM_ENGINE="${TECHNL_LLM_ENGINE:-api}"

echo "=== producer run $(date -u +%FT%TZ) ==="
git pull --rebase --autostash || echo "(git pull skipped/failed; continuing with local code)"
uv sync --quiet

uv run python src/discover.py            # refresh roster (keyless)
uv run python src/map_careers.py         # heuristic careers-page mapping (keyless)
uv run python src/scrape.py --llm --batch 40  # navigator over the 40 least-recently
                                         # -scraped companies (rolling: covers all
                                         # ~171 over ~4-5 daily runs, within the free-tier
                                         # daily quota + a sane runtime)
uv run python src/publish.py             # build feed + dashboard data

# NB: src/map_llm_fallback.py (LLM careers-page *mapping*) is intentionally not run
# here — that task needs web search, which hangs the agentic CLI. Mapping stays
# heuristic; the LLM's value is in navigation/extraction (scrape --llm).

if [ -n "$(git status --porcelain data/ docs/)" ]; then
  git add data/ docs/
  git -c user.name="technl-producer" -c user.email="producer@plex.local" \
      commit -m "chore: producer refresh $(date -u +%FT%TZ)"
  # origin may have advanced during the (long) run — integrate before pushing so the
  # push always fast-forwards (data-only commit rarely conflicts with code changes).
  git -c user.name="technl-producer" -c user.email="producer@plex.local" \
      pull --rebase --autostash origin main || echo "(rebase-before-push had trouble; pushing anyway)"
  git push && echo "pushed refreshed data" || echo "push failed (check deploy key)"
else
  echo "no changes to commit"
fi
echo "=== producer run complete ==="
