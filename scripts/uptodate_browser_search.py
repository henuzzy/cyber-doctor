from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://www.uptodate.cn/contents/search"
DEFAULT_PROFILE_DIR = Path.home() / ".uptodate_playwright_profile"


def main() -> None:
    parser = argparse.ArgumentParser(description="Search UpToDate China with a persistent Playwright browser session.")
    parser.add_argument("--query", help="Single search query, for example 'Shigella dysenteriae'.")
    parser.add_argument("--queries-file", help="UTF-8 text file containing one query per line.")
    parser.add_argument("--output", default="uptodate_search_results.jsonl", help="Output JSONL path.")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="Persistent browser profile directory.")
    parser.add_argument("--url", default=DEFAULT_URL, help="UpToDate search/home URL.")
    parser.add_argument("--manual-login", action="store_true", help="Pause after opening the browser so you can log in manually.")
    parser.add_argument("--keep-open", action="store_true", help="Keep the browser open until Enter is pressed.")
    parser.add_argument("--screenshot-dir", help="Optional directory to save screenshots after each search.")
    parser.add_argument("--max-results", type=int, default=20, help="Max result links to keep per query.")
    args = parser.parse_args()

    queries = load_queries(args)
    if not queries:
        raise SystemExit("No query provided. Use --query or --queries-file.")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Playwright is not installed. Run: python -m pip install playwright && python -m playwright install chromium") from exc

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_dir = Path(args.screenshot_dir) if args.screenshot_dir else None
    if screenshot_dir:
        screenshot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=args.profile_dir,
            headless=False,
            viewport={"width": 1500, "height": 950},
            slow_mo=100,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")

        if args.manual_login:
            print("Browser opened. Log in manually, complete SMS verification, then press Enter here.", flush=True)
            input()

        with output_path.open("w", encoding="utf-8", newline="\n") as writer:
            for index, query in enumerate(queries, start=1):
                print(f"[uptodate] {index}/{len(queries)} searching: {query}", flush=True)
                try:
                    result = search_once(page, args.url, query, args.max_results, PlaywrightTimeoutError)
                except Exception as exc:
                    result = {"query": query, "ok": False, "error": str(exc), "results": []}
                writer.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
                writer.flush()

                if screenshot_dir:
                    safe_name = safe_filename(f"{index:04d}_{query}") + ".png"
                    page.screenshot(path=str(screenshot_dir / safe_name), full_page=True)

        if args.keep_open:
            print("Search finished. Browser is kept open. Press Enter to close it.", flush=True)
            input()

        context.close()

    print(json.dumps({"queries": len(queries), "output": str(output_path)}, ensure_ascii=False), flush=True)


def load_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    if args.query:
        queries.append(args.query.strip())
    if args.queries_file:
        path = Path(args.queries_file)
        queries.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [query for query in queries if query]


def search_once(page: Any, url: str, query: str, max_results: int, timeout_error: type[Exception]) -> dict[str, Any]:
    page.goto(url, wait_until="domcontentloaded")
    search_box = find_search_box(page, timeout_error)
    search_box.fill(query)
    search_box.press("Enter")
    wait_for_search_settle(page, timeout_error)
    results = collect_result_links(page, max_results=max_results)
    return {
        "query": query,
        "ok": True,
        "url": page.url,
        "title": page.title(),
        "results": results,
    }


def find_search_box(page: Any, timeout_error: type[Exception]) -> Any:
    selectors = [
        "input[placeholder*='搜索 UpToDate']",
        "input[placeholder*='UpToDate']",
        "input[placeholder*='搜索']",
        "input[type='search']",
        "input[type='text']",
        "input:not([type])",
        "textarea",
        "[contenteditable='true']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=3000)
            return locator
        except timeout_error:
            continue
    raise RuntimeError("Could not locate the UpToDate search input. Run with --screenshot-dir and inspect the screenshot.")


def wait_for_search_settle(page: Any, timeout_error: type[Exception]) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except timeout_error:
        page.wait_for_timeout(3000)


def collect_result_links(page: Any, max_results: int) -> list[dict[str, str]]:
    links = page.locator("a").evaluate_all(
        """
        els => els.map(a => ({
          text: (a.innerText || a.textContent || '').trim(),
          href: a.href || ''
        }))
        """
    )
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in links:
        text = str(item.get("text") or "").strip()
        href = str(item.get("href") or "").strip()
        if not text or not href:
            continue
        if href in seen:
            continue
        if not looks_like_content_result(href, text):
            continue
        seen.add(href)
        results.append({"title": text, "url": href})
        if len(results) >= max_results:
            break
    return results


def looks_like_content_result(href: str, text: str) -> bool:
    if "/contents/" not in href:
        return False
    ignored = ("search", "login", "logout", "help", "about", "terms", "privacy")
    lowered = href.lower()
    return not any(token in lowered for token in ignored) and len(text) >= 2


def safe_filename(text: str) -> str:
    invalid = '<>:"/\\|?*\x00'
    cleaned = "".join("_" if char in invalid or ord(char) < 32 else char for char in text)
    return cleaned.strip(" .")[:120] or "search"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
