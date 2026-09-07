from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin

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


def fetch_html_http(url: str, log: Callable[[str], None], timeout: int = 60) -> str:
    import requests
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    log(f"HTTP fetch {url}: {r.status_code}, {len(r.text):,} chars")
    return r.text


def parse_html_tables(html_text: str, mode: str, log: Callable[[str], None]) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    combined: list[dict[str, Any]] = []

    for table in soup.find_all("table"):
        matrix = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells:
                matrix.append(cells)

        table_best: list[dict[str, Any]] = []
        # AA's leaderboard has a grouped header row followed by the actual
        # column names. Try several possible header rows.
        for header_i in range(min(8, len(matrix))):
            parsed = parse_table_rows(matrix[header_i], matrix[header_i + 1 :], mode)
            if len(parsed) > len(table_best):
                table_best = parsed
        combined.extend(table_best)

    # Merge all matching table sections. This also handles AA splitting
    # responsive/virtualized table content into more than one <table>.
    dedup: dict[str, dict[str, Any]] = {}
    for row in combined:
        key = normalize_model_name(row.get("model", ""))
        if not key:
            continue
        old = dedup.get(key)
        quality = int(row.get("score") is not None) + int(row.get("cost") is not None) + int(bool(row.get("creator")))
        old_quality = -1 if old is None else int(old.get("score") is not None) + int(old.get("cost") is not None) + int(bool(old.get("creator")))
        if old is None or quality > old_quality:
            dedup[key] = row

    result = list(dedup.values())
    log(f"HTTP HTML table extraction ({mode}): {len(result)} unique rows")
    return result


def parse_coding_variant_tables(html_text: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    out: list[dict[str, Any]] = []

    for table in soup.find_all("table"):
        matrix = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells:
                matrix.append(cells)

        for header_i in range(min(5, len(matrix))):
            headers = matrix[header_i]
            norm = [_nk(h) for h in headers]

            def col_exact_or_contains(*needles: str) -> int | None:
                for i, h in enumerate(norm):
                    if all(n in h for n in needles):
                        return i
                return None

            agent_i = col_exact_or_contains("agent")
            model_i = col_exact_or_contains("model")
            score_i = None
            for i, h in enumerate(norm):
                if h == "index" or "codingagentindex" in h:
                    score_i = i
                    break
            cost_i = col_exact_or_contains("cost", "task")

            if model_i is None or score_i is None or cost_i is None:
                continue

            parsed_here = []
            for cells in matrix[header_i + 1 :]:
                need = max(model_i, score_i, cost_i, agent_i or 0)
                if len(cells) <= need:
                    continue
                model = cells[model_i].strip()
                score = _num(cells[score_i])
                cost = _num(cells[cost_i])
                if not model or score is None:
                    continue
                parsed_here.append({
                    "model": model,
                    "creator": "",
                    "score": score,
                    "cost": cost,
                    "agent": cells[agent_i].strip() if agent_i is not None else "",
                })

            if len(parsed_here) > len(out):
                out = parsed_here

    return out


def _comparison_links_from_html(html_text: str) -> set[str]:
    from bs4 import BeautifulSoup

    urls: set[str] = set()
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if "/agents/coding-agents/comparisons/" not in href:
            continue
        url = urljoin(CODING_URL, href).split("#", 1)[0].split("?", 1)[0]
        # Ignore the bare comparison-picker page; we need pair pages that
        # contain the server-rendered Model Variants table.
        if url.rstrip("/") != (CODING_URL + "/comparisons").rstrip("/"):
            urls.add(url)
    return urls


def discover_coding_comparison_urls(main_html: str, log: Callable[[str], None]) -> list[str]:
    from bs4 import BeautifulSoup

    urls: set[str] = set()
    urls.update(_comparison_links_from_html(main_html))

    # Important: the Coding benchmark page itself usually links only to the
    # comparison picker. The picker is where AA exposes the popular pair
    # pages that contain the actual server-rendered Model Variants tables.
    try:
        comparison_index = fetch_html_http(CODING_URL + "/comparisons", log, timeout=45)
        urls.update(_comparison_links_from_html(comparison_index))
    except Exception as e:
        log(f"Could not read AA coding comparison index: {e}")

    # AA also publishes pair pages through its sitemap. Inspect all nested
    # sitemaps instead of only the first few because their ordering changes.
    try:
        sitemap = fetch_html_http("https://artificialanalysis.ai/sitemap.xml", log, timeout=45)
        sm = BeautifulSoup(sitemap, "xml")
        locs = [x.get_text(strip=True) for x in sm.find_all("loc")]
        nested = [x for x in locs if x.endswith(".xml")]
        for u in locs:
            if "/agents/coding-agents/comparisons/" in u:
                urls.add(u.split("#", 1)[0].split("?", 1)[0])
        for sm_url in nested[:60]:
            try:
                child = fetch_html_http(sm_url, lambda _msg: None, timeout=45)
                csm = BeautifulSoup(child, "xml")
                for loc in csm.find_all("loc"):
                    u = loc.get_text(strip=True)
                    if "/agents/coding-agents/comparisons/" in u:
                        urls.add(u.split("#", 1)[0].split("?", 1)[0])
            except Exception:
                pass
    except Exception as e:
        log(f"Could not inspect AA sitemap for coding comparisons: {e}")

    result = sorted(
        u for u in urls
        if u.rstrip("/") != (CODING_URL + "/comparisons").rstrip("/")
    )
    log(f"Discovered {len(result)} AA coding comparison pair pages")
    return result


def crawl_coding_comparisons(main_html: str, log: Callable[[str], None]) -> list[dict[str, Any]]:
    seed_urls = discover_coding_comparison_urls(main_html, log)
    if not seed_urls:
        log("No coding comparison pair pages were discovered")
        return []

    target_total = None
    m = COUNT_RE.search(main_html)
    if m:
        target_total = int(m.group(2))
        log(f"AA Coding page reports {target_total} selectable models")

    queue = list(seed_urls)
    queued = set(queue)
    visited: set[str] = set()
    all_rows: list[dict[str, Any]] = []

    # Crawl comparison pair pages in small batches. Every pair page contains
    # a plain HTML "Model Variants" table, so this does not depend on AA's
    # chart JavaScript or selector UI.
    while queue and len(visited) < 100:
        batch: list[str] = []
        while queue and len(batch) < 6:
            u = queue.pop(0)
            if u not in visited:
                batch.append(u)

        if not batch:
            break

        def get_one(url: str):
            html = fetch_html_http(url, lambda _msg: None, timeout=45)
            rows = parse_coding_variant_tables(html)
            links = _comparison_links_from_html(html)
            return url, rows, links

        with ThreadPoolExecutor(max_workers=min(6, len(batch))) as ex:
            future_map = {ex.submit(get_one, u): u for u in batch}
            for fut in as_completed(future_map):
                url = future_map[fut]
                visited.add(url)
                try:
                    _, rows, links = fut.result()
                    all_rows.extend(rows)
                    for link in links:
                        if link not in visited and link not in queued and len(queued) < 140:
                            queued.add(link)
                            queue.append(link)
                except Exception as e:
                    log(f"Coding comparison fetch failed for {url}: {e}")

        # Count unique evaluated agent+model variants as we go. Do not collapse
        # the same underlying model when AA evaluated it through another harness.
        unique_now = {
            (
                _nk(str(r.get("agent") or "")),
                normalize_model_name(r.get("model", "")),
            )
            for r in all_rows
            if normalize_model_name(r.get("model", ""))
        }
        log(
            f"Coding comparison crawl: {len(visited)} pages, "
            f"{len(unique_now)} unique agent/model variants"
        )

        # Once we have at least the total AA says is selectable, one extra
        # discovery batch is unnecessary. This keeps refreshes reasonably fast.
        if target_total and len(unique_now) >= target_total:
            break

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in all_rows:
        model_key = normalize_model_name(row.get("model", ""))
        agent_key = _nk(str(row.get("agent") or ""))
        if not model_key:
            continue
        key = (agent_key, model_key)
        old = best.get(key)
        if old is None:
            best[key] = row
            continue
        # Duplicate pair pages repeat the same harness/model row. Prefer a row
        # with a real cost, then the higher current Index.
        new_quality = (
            row.get("cost") is not None,
            row.get("score") if row.get("score") is not None else -1,
        )
        old_quality = (
            old.get("cost") is not None,
            old.get("score") if old.get("score") is not None else -1,
        )
        if new_quality > old_quality:
            best[key] = row

    result = list(best.values())
    result.sort(
        key=lambda x: (
            -(x.get("score") or -1),
            str(x.get("agent") or "").lower(),
            str(x.get("model") or "").lower(),
        )
    )
    log(
        f"Coding comparison tables: {len(result)} unique agent/model variants "
        f"from {len(all_rows)} repeated table rows"
    )
    return result


def _nk(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def clean_creator(value: Any) -> str:
    text = re.sub(r"^\s*Image:\s*", "", str(value or "").strip(), flags=re.I)
    # AA's accessible HTML can expose logo alt text + visible text as
    # "AnthropicAnthropic". Collapse an exact repeated half.
    if len(text) >= 4 and len(text) % 2 == 0:
        half = len(text) // 2
        if text[:half].strip().lower() == text[half:].strip().lower():
            text = text[:half].strip()
    return text

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
    # AA's Coding pages often omit the "Claude" family prefix while the
    # main LLM leaderboard includes it.
    s = re.sub(r"^\s*claude\s+", "", s)
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
            "creator": clean_creator(cells[creator_i]) if creator_i is not None else "",
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
        const rows = [...table.querySelectorAll('tr')]
          .map(tr => [...tr.querySelectorAll('th,td')].map(x => x.innerText.trim()))
          .filter(r => r.length);
        if (rows.length) result.push({rows});
      }

      const grids = [...document.querySelectorAll('[role="table"],[role="grid"]')];
      for (const grid of grids) {
        const getCells = r => [...r.querySelectorAll('[role="columnheader"],[role="cell"],[role="gridcell"]')]
          .map(x => x.innerText.trim());
        const rows = [...grid.querySelectorAll('[role="row"]')].map(getCells).filter(r => r.length);
        if (rows.length) result.push({rows});
      }
      return result;
    }
    """
    tables = page.evaluate(js)
    combined: list[dict[str, Any]] = []
    for t in tables:
        matrix = t.get("rows", [])
        table_best: list[dict[str, Any]] = []
        # AA uses grouped header rows, so the real column names are not
        # guaranteed to be row zero.
        for header_i in range(min(8, len(matrix))):
            parsed = parse_table_rows(matrix[header_i], matrix[header_i + 1 :], mode)
            if len(parsed) > len(table_best):
                table_best = parsed
        combined.extend(table_best)

    dedup: dict[str, dict[str, Any]] = {}
    for row in combined:
        key = normalize_model_name(row.get("model", ""))
        if not key:
            continue
        old = dedup.get(key)
        quality = int(row.get("score") is not None) + int(row.get("cost") is not None) + int(bool(row.get("creator")))
        old_quality = -1 if old is None else int(old.get("score") is not None) + int(old.get("cost") is not None) + int(bool(old.get("creator")))
        if old is None or quality > old_quality:
            dedup[key] = row

    result = list(dedup.values())
    log(f"DOM table extraction ({mode}): {len(result)} unique rows")
    return result

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
                "creator": clean_creator(creator),
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

def extract_coding_from_json_loose(blobs: list[tuple[str, Any]], log: Callable[[str], None]) -> list[dict[str, Any]]:
    """Extract coding rows from AA JSON without depending on one exact schema."""
    candidates: list[dict[str, Any]] = []

    def scalar_map(d: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for k, v in d.items():
            nk = _nk(k)
            if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                flat[nk] = v
            elif isinstance(v, dict):
                for sk, sv in v.items():
                    if isinstance(sv, (str, int, float)) and not isinstance(sv, bool):
                        flat[nk + _nk(sk)] = sv
        return flat

    def first_text(flat: dict[str, Any], preferred: tuple[str, ...], contains: tuple[str, ...]) -> str:
        for key in preferred:
            v = flat.get(key)
            if isinstance(v, str) and 1 < len(v.strip()) < 180:
                return v.strip()
        for k, v in flat.items():
            if isinstance(v, str) and all(word in k for word in contains) and 1 < len(v.strip()) < 180:
                return v.strip()
        return ""

    for url, blob in blobs:
        for d in _walk_dicts(blob):
            flat = scalar_map(d)
            if not flat:
                continue

            score = None
            # Strong coding-index names first.
            for k, v in flat.items():
                if "codingagentindex" in k or "codingindex" in k:
                    n = _num(v)
                    if n is not None and 0 <= n <= 100:
                        score = n
                        break

            # Some AA payloads shorten the metric to index/score inside a
            # coding benchmark object. Only accept that when the record also
            # carries obvious coding benchmark context.
            if score is None:
                coding_context = any(
                    token in k
                    for k in flat
                    for token in ("codingagent", "deepswe", "terminalbench", "sweatlas")
                )
                if coding_context:
                    for k, v in flat.items():
                        if k in {"index", "score", "indexscore", "overallscore"} or k.endswith("index"):
                            n = _num(v)
                            if n is not None and 0 <= n <= 100:
                                score = n
                                break

            if score is None:
                continue

            model = first_text(
                flat,
                ("model", "modelname", "modeldisplayname", "modelvariant", "variantname"),
                ("model", "name"),
            )
            if not model:
                continue

            agent = first_text(
                flat,
                ("agent", "agentname", "harness", "harnessname"),
                ("agent", "name"),
            )
            creator = first_text(
                flat,
                ("creator", "creatorname", "provider", "providername", "company", "organization"),
                ("provider", "name"),
            )

            cost = None
            for k, v in flat.items():
                if "costpertask" in k or ("cost" in k and "task" in k):
                    n = _num(v)
                    if n is not None and n >= 0:
                        cost = n
                        break

            candidates.append({
                "model": model,
                "creator": clean_creator(creator),
                "score": score,
                "cost": cost,
                "agent": agent,
                "_source": url,
            })

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidates:
        key = (_nk(row.get("agent", "")), normalize_model_name(row.get("model", "")))
        if not key[1]:
            continue
        old = best.get(key)
        quality = int(row.get("cost") is not None) + int(bool(row.get("creator"))) + int(bool(row.get("agent")))
        old_quality = -1 if old is None else int(old.get("cost") is not None) + int(bool(old.get("creator"))) + int(bool(old.get("agent")))
        if old is None or quality > old_quality:
            best[key] = row

    result = list(best.values())
    log(f"Loose Coding JSON extraction: {len(result)} agent/model rows")
    return result


def merge_coding_sources(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge coding rows without collapsing different harnesses."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for source in sources:
        for x in source:
            model_key = normalize_model_name(x.get("model", ""))
            if not model_key:
                continue
            agent_key = _nk(x.get("agent", ""))
            key = (agent_key, model_key)
            if key not in out:
                out[key] = dict(x)
            else:
                for field in ("model", "creator", "score", "cost", "agent"):
                    if x.get(field) not in (None, ""):
                        out[key][field] = x[field]
    return list(out.values())


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
    current = total = None

    # AA renders several "14 of 68 models" labels. Prefer the actual button.
    trigger = page.locator("button").filter(has_text=COUNT_RE).first
    try:
        if trigger.count():
            txt = trigger.inner_text(timeout=2500)
        else:
            label = page.get_by_text(COUNT_RE).first
            if not label.count():
                log("Coding model selector was not found")
                return current, total
            txt = label.inner_text(timeout=2500)
            trigger = label.locator("xpath=ancestor::button[1]")
            if not trigger.count():
                trigger = label

        m = COUNT_RE.search(txt)
        if m:
            current, total = int(m.group(1)), int(m.group(2))
            log(f"Coding selector initially reports {current} of {total} models")

        trigger.click(timeout=3500)
        page.wait_for_timeout(500)
    except Exception as e:
        log(f"Could not open Coding model selector: {e}")
        return current, total

    clicked_select_all = False
    for locator in (
        page.get_by_role("button", name=re.compile(r"select\s+all", re.I)),
        page.get_by_text(re.compile(r"^select\s+all(?:\s+models)?$", re.I)),
        page.get_by_text(re.compile(r"^all\s+models$", re.I)),
    ):
        try:
            for i in range(min(locator.count(), 6)):
                item = locator.nth(i)
                if item.is_visible():
                    item.click(timeout=2500, force=True)
                    clicked_select_all = True
                    log("Clicked Coding selector Select All")
                    break
            if clicked_select_all:
                break
        except Exception:
            pass

    if not clicked_select_all:
        # Handle native and custom Radix-style checkboxes.
        boxes = page.locator(
            'input[type="checkbox"]:visible,[role="checkbox"]:visible,button[role="checkbox"]:visible'
        )
        changed = 0
        try:
            for i in range(min(boxes.count(), 120)):
                box = boxes.nth(i)
                try:
                    checked = box.get_attribute("aria-checked") == "true"
                    if box.evaluate("el => el instanceof HTMLInputElement"):
                        checked = box.is_checked()
                    if checked:
                        continue
                    box.click(timeout=1200, force=True)
                    changed += 1
                except Exception:
                    pass
        except Exception:
            pass
        log(f"Coding selector enabled {changed} checkbox items")

    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    # Give AA time to update chart state and fire its data requests.
    page.wait_for_timeout(1800)
    try:
        page.wait_for_load_state("networkidle", timeout=7000)
    except Exception:
        pass

    try:
        labels = page.get_by_text(COUNT_RE)
        for i in range(min(labels.count(), 12)):
            txt = labels.nth(i).inner_text(timeout=1000)
            m = COUNT_RE.search(txt)
            if not m:
                continue
            c2, t2 = int(m.group(1)), int(m.group(2))
            if total is None or t2 >= total:
                current, total = c2, t2
        log(f"Coding selector after expansion reports {current} of {total} models")
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

def force_model_leaderboard_full_render(page, log: Callable[[str], None]) -> None:
    """Trigger AA's full leaderboard DOM/network data before extraction."""
    try:
        # Click only explicit expansion actions; never generic navigation.
        for _ in range(4):
            clicked = 0
            for pattern in (
                re.compile(r"^show\s+more(?:\s+models)?$", re.I),
                re.compile(r"^load\s+more(?:\s+models)?$", re.I),
                re.compile(r"^show\s+all(?:\s+models)?$", re.I),
                re.compile(r"^view\s+more\s+models$", re.I),
            ):
                try:
                    locs = page.get_by_text(pattern)
                    for i in range(min(locs.count(), 8)):
                        loc = locs.nth(i)
                        if loc.is_visible():
                            loc.click(timeout=1200)
                            clicked += 1
                            page.wait_for_timeout(250)
                except Exception:
                    pass
            if not clicked:
                break

        # Walk the document to trigger lazy rows and data requests.
        height = int(page.evaluate("document.body.scrollHeight"))
        for y in range(0, max(height, 1), 700):
            page.evaluate("(y) => window.scrollTo(0, y)", y)
            page.wait_for_timeout(55)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)

        # Some responsive tables use their own scrolling container.
        page.evaluate(
            """
            () => {
              const els = [...document.querySelectorAll('*')];
              for (const el of els) {
                const cs = getComputedStyle(el);
                const scrollable = el.scrollHeight > el.clientHeight + 100 &&
                  (cs.overflowY === 'auto' || cs.overflowY === 'scroll');
                if (scrollable) el.scrollTop = el.scrollHeight;
              }
            }
            """
        )
        page.wait_for_timeout(700)
        page.evaluate("window.scrollTo(0, 0)")
        rows = page.locator("table tr").count()
        log(f"Triggered full model leaderboard render; DOM table rows now: {rows}")
    except Exception as e:
        log(f"Model leaderboard full-render pass failed: {e}")


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

def scrape_all(data_dir: Path, headless: bool = True, threshold: float = 0, target: str = "both") -> ScrapeResult:
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

            # Codespaces/Linux: prefer the distro Chromium package. apt installs
            # Chromium together with all of its shared-library dependencies, so
            # this avoids broken Playwright browser-cache installs.
            system_chromium = Path("/usr/bin/chromium")
            if sys.platform.startswith("linux") and system_chromium.exists():
                try:
                    browser = p.chromium.launch(
                        headless=headless,
                        executable_path=str(system_chromium),
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    log("Launched system Chromium (/usr/bin/chromium)")
                    return True
                except Exception as e:
                    launch_errors.append(str(e))

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
            log("No usable Chromium launch; repairing browser installation automatically...")
            try:
                if sys.platform.startswith("linux"):
                    # Install distro Chromium. This also installs its complete
                    # shared-library dependency chain (ATK, NSS, GTK, etc.).
                    apt_proc = subprocess.run(
                        [
                            "sudo", "-n", "bash", "-lc",
                            "for f in /etc/apt/sources.list.d/*; do if [ -f \"$f\" ] && grep -qs 'dl.yarnpkg.com' \"$f\"; then mv \"$f\" \"$f.aa-disabled\"; fi; done; apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y chromium"
                        ],
                        capture_output=True,
                        text=True,
                        timeout=300,
                        check=False,
                    )
                    if apt_proc.stdout.strip():
                        log(apt_proc.stdout.strip()[-4000:])
                    if apt_proc.stderr.strip():
                        log(apt_proc.stderr.strip()[-4000:])
                    log(f"System Chromium installer exit code: {apt_proc.returncode}")

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
                log(f"Playwright Chromium fallback installer exit code: {proc.returncode}")
            except Exception as e:
                log(f"Automatic browser repair failed: {e}")

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

        target = target if target in {"int", "coding", "both"} else "both"
        models: list[dict[str, Any]] = []
        coding: list[dict[str, Any]] = []
        selected = total = None

        if target in {"int", "both"}:
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
            force_model_leaderboard_full_render(model_page, log)
            try:
                model_page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            collect_script_json(model_page, model_blobs, log)
            model_dom = extract_tables_from_dom(model_page, "models", log)
            model_json = extract_from_json(model_blobs, "models", log)

            model_http: list[dict[str, Any]] = []
            try:
                model_html = fetch_html_http(MODEL_URL, log)
                model_http = parse_html_tables(model_html, "models", log)
                (debug_dir / "models-http.html").write_text(model_html, encoding="utf-8")
            except Exception as e:
                log(f"HTTP model leaderboard fallback failed: {e}")

            models = merge_sources(model_http, merge_sources(model_dom, model_json))
            models = [x for x in models if x.get("score") is not None]
            models.sort(key=lambda x: (-x["score"], x["model"].lower()))
            save_debug(model_page, debug_dir, "models", model_blobs, log)
            priced_models = sum(1 for x in models if x.get("cost") is not None)
            log(f"Model leaderboard: {len(models)} scored rows, {priced_models} with Cost per Task")

        if target in {"coding", "both"}:
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
            try:
                coding_page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            collect_script_json(coding_page, coding_blobs, log)

            coding_dom = extract_tables_from_dom(coding_page, "coding", log)
            coding_json = extract_from_json(coding_blobs, "coding", log)
            coding_loose = extract_coding_from_json_loose(coding_blobs, log)

            # Exact Coding URL only. No INT page, comparison pages, or sitemap.
            coding_http: list[dict[str, Any]] = []
            try:
                coding_html = fetch_html_http(CODING_URL, log)
                coding_http = parse_html_tables(coding_html, "coding", log)
                (debug_dir / "coding-http.html").write_text(coding_html, encoding="utf-8")
            except Exception as e:
                log(f"HTTP Coding page parse failed: {e}")

            coding = merge_coding_sources(coding_dom, coding_json, coding_loose, coding_http)
            coding.sort(
                key=lambda x: (
                    -x["score"],
                    str(x.get("agent") or "").lower(),
                    x["model"].lower(),
                )
            )

            for c in coding:
                c["creator"] = clean_creator(c.get("creator"))

            save_debug(coding_page, debug_dir, "coding", coding_blobs, log)
            log(
                f"Coding exact-page sources: selector={selected}/{total}, "
                f"DOM={len(coding_dom)}, strictJSON={len(coding_json)}, "
                f"looseJSON={len(coding_loose)}, HTML={len(coding_http)}, "
                f"merged={len(coding)}"
            )

        browser.close()

    # The GUI uses the two datasets independently. Keep the legacy merged
    # field only when both sources were fetched in the same call.
    merged: list[dict[str, Any]] = []
    matched = 0
    if models and coding:
        coding_by_norm = {normalize_model_name(x["model"]): x for x in coding}
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
                "coding_agent": c.get("agent") if c else None,
            })

    meta = {
        "model_url": MODEL_URL,
        "coding_url": CODING_URL,
        "threshold": None,
        "model_rows": len(models),
        "model_rows_with_cost": sum(1 for x in models if x.get("cost") is not None),
        "coding_rows": len(coding),
        "coding_matched_to_int_rows": matched,
        "coding_selector_selected": selected,
        "coding_selector_total": total,
        "refresh_target": target,
        "refreshed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    log(
        f"Done: {len(models)} INT rows, {len(coding)} coding rows, "
        f"{matched} exact/conservative matches"
    )
    return ScrapeResult(models=models, coding=coding, merged=merged, meta=meta, logs=logs)