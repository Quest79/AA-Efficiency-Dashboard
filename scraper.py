from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

MODEL_URL = "https://artificialanalysis.ai/leaderboards/models"
CODING_URL = "https://artificialanalysis.ai/agents/coding-agents"

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
COUNT_RE = re.compile(r"(\d+)\s+of\s+(\d+)\s+models?", re.I)

MODEL_ALIASES = {
    "model", "modelname", "model_name", "name", "displayname", "display_name",
    "variant", "modelvariant", "model_variant"
}
CREATOR_ALIASES = {
    "creator", "creatorname", "creator_name", "company", "provider",
    "organization", "organisation", "lab", "vendor"
}
INT_ALIASES = {
    "intelligenceindex", "intelligence_index", "intelligence",
    "artificialanalysisintelligenceindex", "artificial_analysis_intelligence_index",
    "aaintelligenceindex", "aa_intelligence_index"
}
MODEL_COST_ALIASES = {
    "costpertask", "cost_per_task", "taskcost", "task_cost",
    "costpertaskusd", "cost_per_task_usd", "averagecostpertask",
    "average_cost_per_task"
}
CODING_ALIASES = {
    "codingagentindex", "coding_agent_index",
    "artificialanalysiscodingagentindex", "artificial_analysis_coding_agent_index",
    "aacodingagentindex", "aa_coding_agent_index",
    "codingindex", "coding_index"
}
CODING_COST_ALIASES = MODEL_COST_ALIASES | {
    "codingcostpertask", "coding_cost_per_task",
    "agentcostpertask", "agent_cost_per_task"
}

def _nk(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())

def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if math.isfinite(float(v)):
            return float(v)
        return None
    s = str(v).strip()
    if not s or s in {"--", "—", "-", "n/a", "N/A", "null", "None"}:
        return None
    s = s.replace("$", "").replace(",", "").replace("%", "")
    m = NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None

def normalize_model_name(s: str) -> str:
    s = str(s or "").lower()
    s = s.replace("with fallback", "fallback")
    s = s.replace("non-reasoning", "nonreasoning")
    s = s.replace("non reasoning", "nonreasoning")
    s = re.sub(r"\b(the|model)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)

def _key_lookup(d: dict[str, Any], aliases: set[str]) -> tuple[str | None, Any]:
    for k, v in d.items():
        if _nk(k) in aliases or str(k).lower() in aliases:
            return k, v
    return None, None

def _walk_lists(obj: Any) -> Iterable[list[Any]]:
    if isinstance(obj, list):
        yield obj
        for x in obj:
            yield from _walk_lists(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_lists(v)

def _walk_dicts(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_dicts(v)

def parse_table_rows(headers: list[str], rows: list[list[str]], mode: str) -> list[dict[str, Any]]:
    norm = [_nk(h) for h in headers]
    def find_col(words: tuple[str, ...]) -> int | None:
        for i, h in enumerate(norm):
            if all(w in h for w in words):
                return i
        return None

    model_i = find_col(("model",))
    creator_i = find_col(("creator",))
    if creator_i is None:
        creator_i = find_col(("provider",))
    if creator_i is None:
        creator_i = find_col(("company",))

    if mode == "models":
        score_i = find_col(("intelligence",))
        cost_i = find_col(("cost", "task"))
    else:
        score_i = find_col(("coding", "agent", "index"))
        if score_i is None:
            score_i = find_col(("index",))
        cost_i = find_col(("cost", "task"))

    if model_i is None or score_i is None:
        return []

    out = []
    for cells in rows:
        if len(cells) <= max(model_i, score_i, cost_i or 0, creator_i or 0):
            continue
        model = cells[model_i].strip()
        score = _num(cells[score_i])
        if not model or score is None:
            continue
        item = {
            "model": model,
            "creator": cells[creator_i].strip() if creator_i is not None else "",
            "score": score,
            "cost": _num(cells[cost_i]) if cost_i is not None else None,
        }
        out.append(item)
    return out

def extract_tables_from_dom(page, mode: str, log: Callable[[str], None]) -> list[dict[str, Any]]:
    js = r"""
    () => {
      const result = [];
      const candidates = [...document.querySelectorAll('table')];
      for (const table of candidates) {
        const trs = [...table.querySelectorAll('tr')];
        if (!trs.length) continue;
        const headers = [...trs[0].querySelectorAll('th,td')].map(x => x.innerText.trim());
        const rows = trs.slice(1).map(tr => [...tr.querySelectorAll('th,td')].map(x => x.innerText.trim()));
        result.push({headers, rows});
      }

      // ARIA-grid fallback
      const grids = [...document.querySelectorAll('[role="table"],[role="grid"]')];
      for (const grid of grids) {
        const rowEls = [...grid.querySelectorAll('[role="row"]')];
        if (!rowEls.length) continue;
        const getCells = r => [...r.querySelectorAll('[role="columnheader"],[role="cell"],[role="gridcell"]')]
          .map(x => x.innerText.trim());
        const headers = getCells(rowEls[0]);
        const rows = rowEls.slice(1).map(getCells);
        result.push({headers, rows});
      }
      return result;
    }
    """
    tables = page.evaluate(js)
    best: list[dict[str, Any]] = []
    for t in tables:
        parsed = parse_table_rows(t.get("headers", []), t.get("rows", []), mode)
        if len(parsed) > len(best):
            best = parsed
    log(f"DOM table extraction ({mode}): {len(best)} rows")
    return best

def extract_from_json(blobs: list[tuple[str, Any]], mode: str, log: Callable[[str], None]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    score_aliases = INT_ALIASES if mode == "models" else CODING_ALIASES
    cost_aliases = MODEL_COST_ALIASES if mode == "models" else CODING_COST_ALIASES

    for url, blob in blobs:
        for d in _walk_dicts(blob):
            _, model = _key_lookup(d, MODEL_ALIASES)
            _, score = _key_lookup(d, score_aliases)
            if model is None or score is None:
                continue
            sv = _num(score)
            if sv is None:
                continue
            _, creator = _key_lookup(d, CREATOR_ALIASES)
            _, cost = _key_lookup(d, cost_aliases)
            model_s = str(model).strip()
            if len(model_s) < 2 or len(model_s) > 160:
                continue
            candidates.append({
                "model": model_s,
                "creator": str(creator or "").strip(),
                "score": sv,
                "cost": _num(cost),
                "_source": url,
            })

    # Deduplicate. Prefer entries with cost + creator.
    dedup: dict[str, dict[str, Any]] = {}
    for x in candidates:
        k = normalize_model_name(x["model"])
        if not k:
            continue
        old = dedup.get(k)
        quality = (x["cost"] is not None) + bool(x["creator"])
        oldq = -1 if old is None else (old["cost"] is not None) + bool(old["creator"])
        if old is None or quality > oldq:
            dedup[k] = x
    result = list(dedup.values())
    log(f"JSON extraction ({mode}): {len(result)} unique model-like rows")
    return result

def merge_sources(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for source in (secondary, primary):  # primary wins
        for x in source:
            k = normalize_model_name(x["model"])
            if not k:
                continue
            if k not in out:
                out[k] = dict(x)
            else:
                for field in ("model", "creator", "score", "cost"):
                    if x.get(field) not in (None, ""):
                        out[k][field] = x[field]
    return list(out.values())

def collect_script_json(page, blobs: list[tuple[str, Any]], log: Callable[[str], None]) -> None:
    scripts = page.locator("script")
    count = scripts.count()
    added = 0
    for i in range(min(count, 300)):
        try:
            txt = scripts.nth(i).text_content() or ""
        except Exception:
            continue
        txt = txt.strip()
        if not txt or len(txt) > 12_000_000:
            continue
        if txt.startswith("{") or txt.startswith("["):
            try:
                blobs.append((f"inline-script-{i}", json.loads(txt)))
                added += 1
            except Exception:
                pass
    log(f"Parsed {added} inline JSON script blobs")

def save_debug(page, debug_dir: Path, prefix: str, blobs: list[tuple[str, Any]], log: Callable[[str], None]) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    try:
        (debug_dir / f"{prefix}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:
        log(f"Could not save debug HTML: {e}")
    try:
        serializable = [{"url": u, "data": d} for u, d in blobs[-80:]]
        (debug_dir / f"{prefix}-json.json").write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        log(f"Could not save debug JSON: {e}")

def attach_response_collector(page, blobs: list[tuple[str, Any]], log: Callable[[str], None]) -> None:
    def on_response(resp):
        try:
            ctype = (resp.headers or {}).get("content-type", "").lower()
            url = resp.url
            if "json" not in ctype and not any(k in url.lower() for k in ("api", "benchmark", "leaderboard", "model")):
                return
            body = resp.body()
            if len(body) > 16_000_000:
                return
            txt = body.decode("utf-8", errors="ignore").strip()
            if not txt or txt[0] not in "[{":
                return
            data = json.loads(txt)
            blobs.append((url, data))
        except Exception:
            pass
    page.on("response", on_response)

def _visible_count(locator) -> int:
    try:
        return locator.count()
    except Exception:
        return 0

def select_all_coding_models(page, log: Callable[[str], None]) -> tuple[int | None, int | None]:
    # Find the visible "14 of 68 models" style control.
    current = total = None
    count_loc = page.get_by_text(COUNT_RE).first
    try:
        if count_loc.count():
            txt = count_loc.inner_text(timeout=2000)
            m = COUNT_RE.search(txt)
            if m:
                current, total = int(m.group(1)), int(m.group(2))
                log(f"Coding selector initially reports {current} of {total} models")
            # Click the smallest practical clickable ancestor.
            clicked = False
            for selector in ("button", '[role="button"]'):
                try:
                    anc = count_loc.locator(f"xpath=ancestor::{selector.replace('[role=\"button\"]','*[@role=\"button\"]')}[1]")
                    if anc.count():
                        anc.click(timeout=2500)
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                count_loc.click(timeout=2500)
        else:
            log("Could not find the 'N of N models' selector text")
            return current, total
    except Exception as e:
        log(f"Could not open coding model selector: {e}")
        return current, total

    page.wait_for_timeout(500)

    # Preferred: explicit Select All / All Models action.
    for pattern in (
        re.compile(r"select\s+all", re.I),
        re.compile(r"all\s+models", re.I),
        re.compile(r"select\s+all\s+models", re.I),
    ):
        try:
            loc = page.get_by_text(pattern).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2500)
                log(f"Clicked coding selector action: {loc.inner_text(timeout=1000)!r}")
                page.wait_for_timeout(800)
                break
        except Exception:
            pass
    else:
        # Fallback: check every visible checkbox inside the most likely popover/dialog.
        containers = [
            page.locator('[role="dialog"]:visible'),
            page.locator('[role="menu"]:visible'),
            page.locator('[data-radix-popper-content-wrapper]:visible'),
            page.locator('body'),
        ]
        checked = 0
        for cont in containers:
            try:
                if not cont.count():
                    continue
                boxes = cont.first.locator('input[type="checkbox"]:visible')
                if boxes.count() < 2:
                    continue
                for i in range(boxes.count()):
                    box = boxes.nth(i)
                    try:
                        if not box.is_checked():
                            box.check(timeout=1200, force=True)
                            checked += 1
                    except Exception:
                        try:
                            box.click(timeout=1200, force=True)
                            checked += 1
                        except Exception:
                            pass
                if checked:
                    log(f"Checked {checked} model checkboxes in coding selector")
                    break
            except Exception:
                pass

    # Close popover if possible.
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(1200)

    # Re-read count.
    try:
        loc = page.get_by_text(COUNT_RE).first
        if loc.count():
            txt = loc.inner_text(timeout=2000)
            m = COUNT_RE.search(txt)
            if m:
                current2, total2 = int(m.group(1)), int(m.group(2))
                log(f"Coding selector after expansion reports {current2} of {total2} models")
                return current2, total2
    except Exception:
        pass
    return current, total

def force_lazy_sections(page, log: Callable[[str], None]) -> None:
    for text in (
        "Artificial Analysis Coding Agent Index",
        "Cost per Task",
        "Artificial Analysis Coding Agent Index vs. Cost per Task",
    ):
        try:
            loc = page.get_by_text(text, exact=True).last
            if loc.count():
                loc.scroll_into_view_if_needed(timeout=3000)
                page.wait_for_timeout(700)
        except Exception:
            pass
    # Also walk down the page to trigger lazy charts/network.
    try:
        height = page.evaluate("document.body.scrollHeight")
        for y in range(0, int(height), 900):
            page.evaluate(f"window.scrollTo(0,{y})")
            page.wait_for_timeout(80)
        page.evaluate("window.scrollTo(0,0)")
    except Exception:
        pass
    log("Triggered coding page lazy-loaded sections")

@dataclass
class ScrapeResult:
    models: list[dict[str, Any]]
    coding: list[dict[str, Any]]
    merged: list[dict[str, Any]]
    meta: dict[str, Any]
    logs: list[str]

def match_coding(int_model: str, coding_by_norm: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    k = normalize_model_name(int_model)
    if k in coding_by_norm:
        return coding_by_norm[k]

    # Conservative fuzzy fallback: only one near-containment candidate and similar length.
    candidates = []
    for ck, val in coding_by_norm.items():
        if k in ck or ck in k:
            ratio = min(len(k), len(ck)) / max(len(k), len(ck))
            if ratio >= 0.82:
                candidates.append(val)
    return candidates[0] if len(candidates) == 1 else None

def scrape_all(data_dir: Path, headless: bool = True, threshold: float = 40) -> ScrapeResult:
    from playwright.sync_api import sync_playwright

    logs: list[str] = []
    def log(msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        logs.append(f"[{stamp}] {msg}")

    debug_dir = data_dir / "debug"
    data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = None
        launch_errors = []

        def try_launch_browser():
            nonlocal browser
            for kwargs in (
                {"channel": "msedge", "headless": headless},
                {"channel": "chrome", "headless": headless},
                {"headless": headless},
            ):
                try:
                    browser = p.chromium.launch(**kwargs)
                    log(f"Launched Chromium browser ({kwargs.get('channel','Playwright Chromium')})")
                    return True
                except Exception as e:
                    launch_errors.append(str(e))
            return False

        if not try_launch_browser():
            # Codespaces/containers can have the Python Playwright package but not
            # its browser payload. Repair that automatically instead of making
            # the user drop into a terminal.
            log("No usable Chromium executable found; installing Playwright Chromium automatically...")
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                if proc.stdout.strip():
                    log(proc.stdout.strip()[-4000:])
                if proc.stderr.strip():
                    log(proc.stderr.strip()[-4000:])
                log(f"Playwright Chromium installer exit code: {proc.returncode}")
            except Exception as e:
                log(f"Automatic Chromium install failed: {e}")

            launch_errors.clear()
            try_launch_browser()

        if browser is None:
            raise RuntimeError(
                "Could not launch Playwright Chromium even after automatic repair.\n"
                + "\n".join(launch_errors[-3:])
            )

        context = browser.new_context(
            viewport={"width": 1600, "height": 1100},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
        )

        # ---- Models leaderboard ----
        model_page = context.new_page()
        model_blobs: list[tuple[str, Any]] = []
        attach_response_collector(model_page, model_blobs, log)
        log(f"Loading model leaderboard: {MODEL_URL}")
        model_page.goto(MODEL_URL, wait_until="domcontentloaded", timeout=90_000)
        model_page.wait_for_timeout(2500)
        try:
            model_page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        collect_script_json(model_page, model_blobs, log)
        model_dom = extract_tables_from_dom(model_page, "models", log)
        model_json = extract_from_json(model_blobs, "models", log)
        models = merge_sources(model_dom, model_json)
        models = [x for x in models if x.get("score") is not None and x["score"] >= threshold]
        models.sort(key=lambda x: (-x["score"], x["model"].lower()))
        save_debug(model_page, debug_dir, "models", model_blobs, log)
        log(f"Model leaderboard after INT >= {threshold:g}: {len(models)} rows")

        # ---- Coding agent page ----
        coding_page = context.new_page()
        coding_blobs: list[tuple[str, Any]] = []
        attach_response_collector(coding_page, coding_blobs, log)
        log(f"Loading coding-agent page: {CODING_URL}")
        coding_page.goto(CODING_URL, wait_until="domcontentloaded", timeout=90_000)
        coding_page.wait_for_timeout(2500)
        try:
            coding_page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass

        selected, total = select_all_coding_models(coding_page, log)
        force_lazy_sections(coding_page, log)
        collect_script_json(coding_page, coding_blobs, log)

        coding_dom = extract_tables_from_dom(coding_page, "coding", log)
        coding_json = extract_from_json(coding_blobs, "coding", log)
        coding = merge_sources(coding_dom, coding_json)
        coding.sort(key=lambda x: (-x["score"], x["model"].lower()))
        save_debug(coding_page, debug_dir, "coding", coding_blobs, log)
        log(f"Coding-agent extraction: {len(coding)} unique rows")

        browser.close()

    # Merge by model variant.
    coding_by_norm = {normalize_model_name(x["model"]): x for x in coding}
    merged: list[dict[str, Any]] = []
    matched = 0
    for m in models:
        c = match_coding(m["model"], coding_by_norm)
        if c:
            matched += 1
        int_cost = m.get("cost")
        coding_cost = c.get("cost") if c else None
        int_eff = (m["score"] / int_cost) if int_cost not in (None, 0) else None
        coding_eff = (c["score"] / coding_cost) if c and coding_cost not in (None, 0) else None
        merged.append({
            "model": m["model"],
            "creator": m.get("creator") or (c.get("creator") if c else "") or "",
            "int": m["score"],
            "int_cost": int_cost,
            "int_eff": int_eff,
            "coding": c.get("score") if c else None,
            "coding_cost": coding_cost,
            "coding_eff": coding_eff,
            "coding_match": bool(c),
        })

    # Sort by INT efficiency, with N/A at bottom.
    merged.sort(
        key=lambda x: (
            x["int_eff"] is None,
            -(x["int_eff"] or -1),
            -x["int"],
            x["model"].lower(),
        )
    )
    for i, x in enumerate(merged, 1):
        x["rank"] = i if x["int_eff"] is not None else None

    meta = {
        "model_url": MODEL_URL,
        "coding_url": CODING_URL,
        "threshold": threshold,
        "model_rows": len(models),
        "coding_rows": len(coding),
        "coding_matched_to_int_rows": matched,
        "coding_selector_selected": selected,
        "coding_selector_total": total,
        "refreshed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    log(
        f"Done: {len(models)} INT rows, {len(coding)} coding rows, "
        f"{matched} exact/conservative matches"
    )
    return ScrapeResult(models=models, coding=coding, merged=merged, meta=meta, logs=logs)