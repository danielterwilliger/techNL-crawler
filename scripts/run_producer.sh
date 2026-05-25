#!/usr/bin/env bash
# techNL-crawler producer run (the LLM-navigator crawl).
#
# Runs on the operator's box (the Plex box), where the OAuth credential lives. Does
# the full pipeline including the LLM reasoning the keyless GitHub Actions run can't:
# discover -> heuristic map -> navigator scrape (--llm) -> publish, then pushes the
# enriched data back to the repo.
#
# Auth: OAuth via the gemini CLI (~/.gemini/oauth_creds.json). The env below selects
# the Code Assist path; src/llm.py's CLI engine drops any stray API key.
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUNBUFFERED=1          # stream per-company progress to the journal live
export TECHNL_LLM_ENGINE=cli
export GOOGLE_GENAI_USE_GCA=true
export GEMINI_CLI_TRUST_WORKSPACE=true

echo "=== producer run $(date -u +%FT%TZ) ==="
git pull --rebase --autostash || echo "(git pull skipped/failed; continuing with local code)"
uv sync --quiet

uv run python src/discover.py            # refresh roster (keyless)
uv run python src/map_careers.py         # heuristic careers-page mapping (keyless)
uv run python src/scrape.py --llm --batch 40  # navigator over the 40 least-recently
                                         # -scraped companies (rolling: covers all
                                         # ~171 over ~4-5 daily runs, within OAuth
                                         # daily quota + a sane runtime)
uv run python src/publish.py             # build feed + dashboard data

# NB: src/map_llm_fallback.py (LLM careers-page *mapping*) is intentionally not run
# here — that task needs web search, which hangs the agentic CLI. Mapping stays
# heuristic; the LLM's value is in navigation/extraction (scrape --llm).

if [ -n "$(git status --porcelain data/ docs/)" ]; then
  git add data/ docs/
  git -c user.name="technl-producer" -c user.email="producer@plex.local" \
      commit -m "chore: producer refresh $(date -u +%FT%TZ)"
  git push && echo "pushed refreshed data" || echo "push failed (check deploy key)"
else
  echo "no changes to commit"
fi
echo "=== producer run complete ==="
