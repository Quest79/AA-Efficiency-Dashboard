# AA Efficiency Dashboard v1.2.0

Live dashboard for Artificial Analysis Intelligence and Coding Agent data.

## Exact refresh sources

- Intelligence refresh reads:
  https://artificialanalysis.ai/leaderboards/models
- Coding refresh reads:
  https://artificialanalysis.ai/agents/coding-agents
  and also reads the model leaderboard only to join shared model metadata such as Cost per Task.

Refreshing Coding does not overwrite the Intelligence cache.

## Codespaces

- Codespaces binds to `0.0.0.0:8765` so the forwarded URL can reach the app.
- Starting `app.py` replaces an older stale dashboard listener instead of falsely reporting that it is already running.
- `codespace-repair.sh` verifies both `/api/info` and the actual root HTML before reporting success.
- Existing Codespaces are forced to auto-forward port 8765 by printing `http://localhost:8765/`, then the script asks GitHub for the real forwarded `browseUrl` instead of guessing it.

## Refresh behavior

- Intelligence and Coding have separate caches.
- Refresh updates only the currently selected tab.
- A failed Intelligence refresh cannot block a Coding refresh.
- A failed Coding refresh cannot replace valid Intelligence data.
- Coding accepts any non-zero set of valid rows from the Coding page instead of discarding useful results because the count is below an arbitrary threshold.
- Coding extraction combines the exact page's rendered DOM, captured JSON/network responses, a looser schema-aware JSON extractor, and the exact page's own HTML.
- The Coding model selector is expanded toward all models before chart/network extraction.
- Diagnostics preserve detailed scraper logs even when validation fails.

## UI

All existing creator colors, saved highlights, filters, column controls, row/content scaling, row height, and font controls are preserved.

The Intelligence table also shows `TOK/S`, scraped from Artificial Analysis' `Median Tokens/s` field and paired directly from the same model leaderboard row. Models without an AA speed result show `N/A`.

## Version

v1.2.0
