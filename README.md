# AA Efficiency Dashboard v1.1.3

Live dashboard for Artificial Analysis Intelligence and Coding Agent data.

## Exact refresh sources

- Intelligence tab refreshes only:
  https://artificialanalysis.ai/leaderboards/models
- Coding tab refreshes only:
  https://artificialanalysis.ai/agents/coding-agents

A tab refresh does not open or scrape the other tab's source.

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

## Version

v1.1.3
