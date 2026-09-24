from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import uptodate_browser_search as utd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search pathogens on UpToDate and cache the first article for each pathogen."
    )
    parser.add_argument("--input", required=True, help="Pathogen JSONL file.")
    parser.add_argument("--output-dir", required=True, help="Batch output directory.")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--pause-seconds", type=float, default=2.0)
    parser.add_argument("--detail-max-chars", type=int, default=500000)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ").strip()).casefold()


def query_candidates(record: dict[str, Any]) -> list[dict[str, str]]:
    names = record.get("Name") or {}
    fields = (
        ("species_latin", "种-拉丁名"),
        ("species_chinese", "种-中文名"),
        ("genus_latin", "属-拉丁名"),
        ("genus_chinese", "属-中文名"),
    )
    candidates: list[dict[str, str]] = []
    for key, field in fields:
        raw = str(names.get(field) or "").strip()
        if not raw:
            continue
        variants = [raw]
        if "_" in raw:
            variants.append(raw.replace("_", " ").strip())
        seen_variants: set[str] = set()
        for query in variants:
            if not query or query in seen_variants:
                continue
            seen_variants.add(query)
            candidates.append({"field": key, "query": query})
    return candidates


def safe_path_component(value: str) -> str:
    return utd.safe_filename(value.replace("|", "_")).replace(" ", "_")


def load_records(path: Path, limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        name_id = str(record.get("NameID") or "").strip()
        if not name_id or name_id in seen_ids:
            continue
        seen_ids.add(name_id)
        records.append(record)
        if limit > 0 and len(records) >= limit:
            break
    return records


def load_completed(results_path: Path) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    if not results_path.exists():
        return completed
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        name_id = str(item.get("NameID") or "")
        if name_id:
            completed[name_id] = item
    return completed


def write_summary(summary_path: Path, selected: list[dict[str, Any]], completed: dict[str, dict[str, Any]]) -> None:
    counter = Counter(str(item.get("status") or "unknown") for item in completed.values())
    payload = {
        "selected_pathogen_count": len(selected),
        "completed_count": len(completed),
        "remaining_count": max(0, len(selected) - len(completed)),
        "status_counts": dict(sorted(counter.items())),
        "success_count": counter["success"],
        "no_result_count": counter["no_result"],
        "search_error_count": counter["search_error"],
        "detail_error_count": counter["detail_error"],
        "detail_empty_count": counter["detail_empty"],
        "updated_at_unix": time.time(),
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def trim_article_text(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in {"参考文献", "REFERENCES", "References"}:
            break
        if stripped.startswith("使用 UpToDate 必须遵守订阅与许可证协议"):
            continue
        if re.fullmatch(r"专题\s*\d+\s*版本.*", stripped):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def write_result(results_handle: Any, record: dict[str, Any]) -> None:
    results_handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    results_handle.flush()


def process_one(
    page: Any,
    context: Any,
    record: dict[str, Any],
    index: int,
    output_dir: Path,
    detail_max_chars: int,
) -> dict[str, Any]:
    name_id = str(record.get("NameID") or "")
    names = record.get("Name") or {}
    result: dict[str, Any] = {
        "index": index,
        "NameID": name_id,
        "category": str(record.get("类别") or ""),
        "names": names,
        "status": "no_result",
        "queries_attempted": [],
    }
    candidates = query_candidates(record)
    if not candidates:
        result["status"] = "no_query_name"
        return result

    found: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            search = utd.search_once(
                page,
                utd.DEFAULT_URL,
                candidate["query"],
                max_results=10,
                click_more=0,
                timeout_error=PlaywrightTimeoutError,
            )
            rows = list(search.get("results") or [])
            result["queries_attempted"].append(
                {
                    "field": candidate["field"],
                    "query": candidate["query"],
                    "result_count": len(rows),
                }
            )
            if rows:
                found = {"candidate": candidate, "search": search, "article": rows[0]}
                break
        except Exception as exc:
            result["queries_attempted"].append(
                {"field": candidate["field"], "query": candidate["query"], "error": str(exc)}
            )

    if not found:
        errors = [item for item in result["queries_attempted"] if "error" in item]
        if errors and len(errors) == len(result["queries_attempted"]):
            result["status"] = "search_error"
        return result

    article = found["article"]
    result["matched_query"] = found["candidate"]
    result["first_article"] = {
        "title": str(article.get("title") or ""),
        "url": str(article.get("url") or ""),
        "display_rank": article.get("display_rank"),
    }
    detail_page = context.new_page()
    try:
        # UpToDate may keep background requests open indefinitely.  A DOM
        # load timeout must not discard a page whose article content is already
        # available, so use commit and wait for visible body text separately.
        try:
            detail_page.goto(str(article["url"]), wait_until="commit", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        try:
            detail_page.wait_for_function(
                """() => {
                    const text = document.body ? (document.body.innerText || '') : '';
                    return text.length > 500 && /引言|概述|Introduction|Overview|治疗|Treatment/i.test(text);
                }""",
                timeout=60000,
            )
        except PlaywrightTimeoutError:
            # Let extract_article_detail decide whether the page contains a
            # usable article; some topics use headings outside this heuristic.
            pass
        utd.wait_for_search_settle(detail_page, PlaywrightTimeoutError)
        detail = utd.extract_article_detail(
            detail_page,
            query=str(found["candidate"]["query"]),
            max_chars=detail_max_chars,
        )
        article_text = trim_article_text(str(detail.get("article_text") or ""))
        if not article_text:
            result["status"] = "detail_empty"
            return result
        article_dir = output_dir / "articles" / f"{index:04d}_{safe_path_component(name_id)}"
        article_dir.mkdir(parents=True, exist_ok=True)
        title = utd.clean_article_title(str(detail.get("page_title") or article.get("title") or ""))
        article_path = article_dir / f"{utd.safe_filename(title or 'uptodate_article')}.md"
        article_path.write_text(article_text + "\n", encoding="utf-8", newline="\n")
        result["status"] = "success"
        result["first_article"].update(
            {
                "page_title": title,
                "markdown_path": str(article_path),
                "extracted_chars": len(article_text),
            }
        )
        return result
    except Exception as exc:
        result["status"] = "detail_error"
        result["detail_error"] = str(exc)
        return result
    finally:
        detail_page.close()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    summary_path = output_dir / "summary.json"
    progress_path = output_dir / "progress.log"
    selected = load_records(input_path, args.limit)
    completed = load_completed(results_path) if args.resume else {}
    if not args.resume and results_path.exists():
        results_path.unlink()
    write_summary(summary_path, selected, completed)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        if utd.is_login_page(page):
            raise RuntimeError(f"UpToDate is not logged in: {page.url}")

        with results_path.open("a", encoding="utf-8", newline="\n") as handle, progress_path.open(
            "a", encoding="utf-8", newline="\n"
        ) as log_handle:
            for index, record in enumerate(selected, start=1):
                name_id = str(record.get("NameID") or "")
                if name_id in completed:
                    continue
                name = record.get("Name") or {}
                label = str(name.get("种-拉丁名") or name.get("种-中文名") or name.get("属-拉丁名") or name_id)
                print(f"[uptodate-batch] {index}/{len(selected)} {label}", flush=True)
                item = process_one(page, context, record, index, output_dir, args.detail_max_chars)
                completed[name_id] = item
                write_result(handle, item)
                write_summary(summary_path, selected, completed)
                log_handle.write(f"{index}\t{name_id}\t{item['status']}\n")
                log_handle.flush()
                if args.pause_seconds > 0 and index < len(selected):
                    page.wait_for_timeout(args.pause_seconds * 1000)
        browser.close()

    write_summary(summary_path, selected, completed)
    print(json.dumps(json.loads(summary_path.read_text(encoding="utf-8")), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
