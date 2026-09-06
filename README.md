# AA Efficiency Dashboard v1.1.0

Local Windows dashboard that refreshes live data from Artificial Analysis.

## Sources

The scraper only uses these two AA pages:

- Intelligence / pricing:
  https://artificialanalysis.ai/leaderboards/models
- Coding Agent Index / coding cost:
  https://artificialanalysis.ai/agents/coding-agents

## What Refresh does

1. Opens the AA Intelligence leaderboard in a headless Chromium browser.
2. Extracts model, creator, Intelligence Index, and Cost per Task.
3. Opens the AA Coding Agent page.
4. Finds the `N of N models` selector and attempts to select **all models**.
5. Scrolls through lazy-loaded coding sections so all relevant data requests fire.
6. Extracts Coding Agent Index and Coding Cost per Task from DOM tables and captured JSON.
7. Keeps Intelligence and Coding as independent datasets/tabs. Coding is not filtered by or dependent on Intelligence.
8. Calculates `INT/# AA Efficiency Dashboard v1.1.0

Local Windows dashboard that refreshes live data from Artificial Analysis.

## Sources

The scraper only uses these two AA pages:

- Intelligence / pricing:
  https://artificialanalysis.ai/leaderboards/models
- Coding Agent Index / coding cost:
  https://artificialanalysis.ai/agents/coding-agents

## What Refresh does

1. Opens the AA Intelligence leaderboard in a headless Chromium browser.
2. Extracts model, creator, Intelligence Index, and Cost per Task.
3. Opens the AA Coding Agent page.
4. Finds the `N of N models` selector and attempts to select **all models**.
5. Scrolls through lazy-loaded coding sections so all relevant data requests fire.
6. Extracts Coding Agent Index and Coding Cost per Task from DOM tables and captured JSON.
 only from Intelligence score/cost and `CODE/# AA Efficiency Dashboard v1.1.0

Local Windows dashboard that refreshes live data from Artificial Analysis.

## Sources

The scraper only uses these two AA pages:

- Intelligence / pricing:
  https://artificialanalysis.ai/leaderboards/models
- Coding Agent Index / coding cost:
  https://artificialanalysis.ai/agents/coding-agents

## What Refresh does

1. Opens the AA Intelligence leaderboard in a headless Chromium browser.
2. Extracts model, creator, Intelligence Index, and Cost per Task.
3. Opens the AA Coding Agent page.
4. Finds the `N of N models` selector and attempts to select **all models**.
5. Scrolls through lazy-loaded coding sections so all relevant data requests fire.
6. Extracts Coding Agent Index and Coding Cost per Task from DOM tables and captured JSON.
 only from Coding score/cost.
9. Saves the last successful result under:
   `%LOCALAPPDATA%\AAEfficiencyDashboard\data.json`

If a refresh obviously fails (for example, fewer than 20 Intelligence rows), the app keeps the previous good cache instead of overwriting it.

## Install

Double-click:

`Install-AA-Dashboard.bat`

Then run:

`Run-AA-Dashboard.bat`

The app opens at:

`http://127.0.0.1:8765/`

No console window is shown by the normal launcher.

## Browser handling

The scraper tries, in order:

1. Microsoft Edge
2. Google Chrome
3. Playwright Chromium

The installer downloads Playwright Chromium as a fallback.

## Diagnostics

Click **Diagnostics** in the app.

Raw debug captures are also saved to:

`%LOCALAPPDATA%\AAEfficiencyDashboard\debug\`

That folder contains the last AA page HTML and captured JSON payloads. This is intentional: if AA changes its frontend, the extractor can be updated from the captured payloads instead of guessing.

## UI features

- Refresh AA data
- Separate Intelligence and Coding tabs with independent scores/costs
- Full `leaderboards/models` extraction with lazy-table expansion and multi-table merging
- Min INT is a GUI-only view filter (default 0); refresh always stores the full AA model leaderboard
- Separate live Search that highlights entire rows
- Persistent saved highlight filters
- Hide rows by model / creator / any text
- Hide/show columns
- Adjustable persistent column widths
- Left-side scrolling control panel so the leaderboard uses full viewport height
- Persistent row/table scale slider for row height, text, creator swatches, and INT blocks
- Creator-colored INT Level blocks (1 block per INT point starting at 40)
- INT efficiency and coding efficiency shown side by side
- Last-refresh timestamp
- Last-good-data cache

## Version

v1.1.0
