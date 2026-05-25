#!/usr/bin/env bash
# techNL-crawler producer run (the LLM-enriched crawl).
#
# Runs on the operator's box (the Plex box), where the LLM credential lives. Does
# the full pipeline INCLUDING the LLM stages the keyless GitHub Actions run can't:
# heuristic map -> LLM map fallback -> LLM-assisted scrape -> publish, then pushes
# the enriched data back to the repo.
#
# Invoked by the systemd timer (deploy/technl-producer.timer). GEMINI_API_KEY is
# supplied via the systemd EnvironmentFile (.env.producer), never committed.
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

echo "=== producer run $(date -u +%FT%TZ) ==="
git pull --rebase --autostash || echo "(git pull skipped/failed; continuing with local code)"
uv sync --quiet

uv run python src/discover.py              # refresh roster (keyless)
uv run python src/map_careers.py           # heuristic mapping first (free)
uv run python src/map_llm_fallback.py      # LLM only for the hard tail
uv run python src/scrape.py --llm-extract  # LLM extraction for JS-only boards
uv run python src/publish.py               # build feed + dashboard data

if [ -n "$(git status --porcelain data/ docs/)" ]; then
  git add data/ docs/
  git -c user.name="technl-producer" -c user.email="producer@plex.local" \
      commit -m "chore: producer refresh $(date -u +%FT%TZ)"
  git push && echo "pushed refreshed data" || echo "push failed (check deploy key)"
else
  echo "no changes to commit"
fi
echo "=== producer run complete ==="
