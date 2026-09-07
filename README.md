# AA Efficiency Dashboard v1.1.2

Live dashboard for Artificial Analysis Intelligence and Coding Agent data.

## Sources

- Intelligence / pricing: https://artificialanalysis.ai/leaderboards/models
- Coding Agent Index / coding cost: https://artificialanalysis.ai/agents/coding-agents

## Main behavior

- Intelligence and Coding are separate tabs and separate datasets.
- Coding is scraped from AA server-rendered Coding Model Variants tables rather than depending on the chart UI.
- Coding rows preserve the evaluated agent harness + model variant.
- Refresh stores the full AA model leaderboard; Min INT is only a view filter.
- INT/$ = Intelligence Index / Intelligence Cost per Task.
- CODE/$ = Coding Agent Index / Coding Cost per Task.
- Last-good data is cached instead of being overwritten by an obviously incomplete scrape.

## UI

- Live search and saved row highlights.
- Persistent hide rules.
- Creator color presets with editable colors.
- Adjustable persistent column widths.
- Adjustable row/content scale and row height.
- Creator-colored INT Level blocks.
- Installed-font dropdown using the browser Local Font Access API when supported.
- Full Google Fonts dropdown loaded from Google Fonts metadata; selected Google fonts are loaded on demand.
- Font selection persists in localStorage.
- Full-height left control sidebar.

## Version

v1.1.2
