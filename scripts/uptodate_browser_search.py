from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_URL = "https://www.uptodate.cn/contents/search"
DEFAULT_PROFILE_DIR = Path.home() / ".uptodate_playwright_profile"


def main() -> None:
    parser = argparse.ArgumentParser(description="Search UpToDate China with a persistent Playwright browser session.")
    parser.add_argument("--query", help="Single search query, for example 'Shigella dysenteriae'.")
    parser.add_argument("--queries-file", help="UTF-8 text file containing one query per line.")
    parser.add_argument("--output", help="Optional output JSONL path. Omit this when only Markdown cache files are needed.")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="Persistent browser profile directory.")
    parser.add_argument("--cdp-url", help="Connect to an already running Chrome by CDP, for example http://127.0.0.1:9222.")
    parser.add_argument("--url", default=DEFAULT_URL, help="UpToDate search/home URL.")
    parser.add_argument("--manual-login", action="store_true", help="Pause after opening the browser so you can log in manually.")
    parser.add_argument("--keep-open", action="store_true", help="Keep the browser open until Enter is pressed.")
    parser.add_argument("--screenshot-dir", help="Optional directory to save screenshots after each search.")
    parser.add_argument("--max-results", type=int, default=10, help="Max search result cards to keep per query.")
    parser.add_argument("--click-more", type=int, default=0, help="Click 'show more results' at most N times.")
    parser.add_argument("--cache-dir", help="Optional directory for article-level Markdown evidence cache.")
    parser.add_argument("--open-details", action="store_true", help="Open top result pages and cache article evidence as Markdown.")
    parser.add_argument("--detail-top-k", type=int, default=3, help="Number of top search results to open per query.")
    parser.add_argument("--detail-max-chars", type=int, default=200000, help="Max extracted article body characters per cached Markdown file.")
    parser.add_argument("--refresh-cache", action="store_true", help="Refresh Markdown cache files even when they already exist.")
    args = parser.parse_args()

    queries = load_queries(args)
    if not queries:
        raise SystemExit("No query provided. Use --query or --queries-file.")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Playwright is not installed. Run: python -m pip install playwright && python -m playwright install chromium") from exc

    output_path = Path(args.output) if args.output else None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_dir = Path(args.screenshot_dir) if args.screenshot_dir else None
    if screenshot_dir:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        if args.cdp_url:
            browser = p.chromium.connect_over_cdp(args.cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context(viewport={"width": 1500, "height": 950})
            close_browser = False
        else:
            context = p.chromium.launch_persistent_context(
                user_data_dir=args.profile_dir,
                headless=False,
                viewport={"width": 1500, "height": 950},
                slow_mo=100,
            )
            browser = None
            close_browser = True
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")

        if args.manual_login:
            wait_for_manual_login(page)

        writer = output_path.open("w", encoding="utf-8", newline="\n") if output_path else None
        try:
            for index, query in enumerate(queries, start=1):
                print(f"[uptodate] {index}/{len(queries)} searching: {query}", flush=True)
                try:
                    result = search_once(page, args.url, query, args.max_results, args.click_more, PlaywrightTimeoutError)
                    if args.open_details and cache_dir:
                        cache_search_result_articles(
                            context=context,
                            search_result=result,
                            cache_dir=cache_dir,
                            top_k=args.detail_top_k,
                            max_chars=args.detail_max_chars,
                            refresh=args.refresh_cache,
                            timeout_error=PlaywrightTimeoutError,
                        )
                except Exception as exc:
                    result = {"query": query, "ok": False, "error": str(exc), "results": []}
                if writer:
                    writer.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
                    writer.flush()

                if screenshot_dir:
                    safe_name = safe_filename(f"{index:04d}_{query}") + ".png"
                    page.screenshot(path=str(screenshot_dir / safe_name), full_page=True)
        finally:
            if writer:
                writer.close()

        if args.keep_open:
            print("Search finished. Browser is kept open. Press Enter to close it.", flush=True)
            input()

        if close_browser:
            context.close()
        elif browser:
            # Only disconnect Playwright from the external Chrome. The browser
            # process stays alive, so UpToDate's browser-session login can remain.
            browser.close()

    summary = {"queries": len(queries)}
    if output_path:
        summary["output"] = str(output_path)
    if cache_dir:
        summary["cache_dir"] = str(cache_dir)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def load_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    if args.query:
        queries.append(args.query.strip())
    if args.queries_file:
        path = Path(args.queries_file)
        queries.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [query for query in queries if query]


def wait_for_manual_login(page: Any) -> None:
    while True:
        print("Browser opened. Log in manually, complete SMS verification, then press Enter here.", flush=True)
        input()
        page.wait_for_timeout(1000)
        if not is_login_page(page):
            return
        print(
            f"Still on login/verification page: {page.url}. Finish login in the browser, then press Enter again.",
            flush=True,
        )


def search_once(page: Any, url: str, query: str, max_results: int, click_more: int, timeout_error: type[Exception]) -> dict[str, Any]:
    # After manual SMS login, reloading /contents/search may trigger a validation
    # redirect. Reuse the current authenticated home/search page whenever possible.
    if "uptodate.cn" not in page.url:
        page.goto(url, wait_until="domcontentloaded")
    if is_login_page(page):
        raise RuntimeError(f"Still on UpToDate login/verification page before search: {page.url}")
    search_box = find_search_box(page, timeout_error)
    search_box.fill(query)
    submit_search(page, search_box)
    wait_for_search_settle(page, timeout_error)
    click_show_more_results(page, click_more)
    results = collect_search_results(page, query=query, max_results=max_results)
    return {
        "query": query,
        "ok": True,
        "url": page.url,
        "title": page.title(),
        "results": results,
    }


def is_login_page(page: Any) -> bool:
    url = page.url.lower()
    title = page.title().lower()
    login_markers = ("login", "validate-code", "sign in")
    return any(marker in url or marker in title for marker in login_markers)


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


def submit_search(page: Any, search_box: Any) -> None:
    search_box.press("Enter")
    page.wait_for_timeout(800)
    # The UpToDate home page has a large search icon immediately to the right of
    # the input. If Enter is ignored by the frontend, click that icon by position.
    box = search_box.bounding_box()
    if box:
        page.mouse.click(box["x"] + box["width"] + 28, box["y"] + box["height"] / 2)


def wait_for_search_settle(page: Any, timeout_error: type[Exception]) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except timeout_error:
        page.wait_for_timeout(3000)


def click_show_more_results(page: Any, max_clicks: int) -> None:
    for _ in range(max(0, max_clicks)):
        clicked = page.locator(
            "text=/显示更多结果|显示更多|Show more results|Show more/i"
        ).evaluate_all(
            """
            els => {
              const visible = el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 10 && rect.height > 10;
              };
              const candidates = els.filter(visible);
              if (!candidates.length) return false;
              candidates[0].click();
              return true;
            }
            """
        )
        if not clicked:
            break
        page.wait_for_timeout(1800)


def collect_search_results(page: Any, query: str, max_results: int) -> list[dict[str, Any]]:
    candidates = page.locator("body").evaluate(
        """
        (body, query) => {
          const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
          const visible = el => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 20 && rect.height > 20;
          };
          const hrefRank = href => {
            try {
              const url = new URL(href);
              const rank = url.searchParams.get('display_rank') || '';
              return rank ? Number(rank) : null;
            } catch {
              return null;
            }
          };
          const isPrimarySearchResult = href => {
            try {
              const url = new URL(href);
              if (!url.pathname.includes('/contents/')) return false;
              if (url.pathname.includes('/table-of-contents/')) return false;
              if (url.hash) return false;
              if (url.searchParams.has('sectionRank') || url.searchParams.has('anchor')) return false;
              return url.searchParams.get('source') === 'search_result' || url.searchParams.has('display_rank') || url.searchParams.has('selectedTitle');
            } catch {
              return false;
            }
          };
          const articleSlug = href => {
            try {
              const url = new URL(href);
              return url.pathname.replace(/^\\/contents\\/[^/]+\\//, '').replace(/^\\/contents\\//, '');
            } catch {
              return href;
            }
          };

          const anchors = Array.from(body.querySelectorAll("a[href*='/contents/']"));
          const rows = [];
          for (const anchor of anchors) {
            const href = anchor.href || '';
            if (!isPrimarySearchResult(href)) continue;
            const anchorText = clean(anchor.innerText || anchor.textContent);
            const anchorRect = anchor.getBoundingClientRect();
            if (!anchorText || anchorRect.width <= 20 || anchorRect.height <= 10) continue;
            if (!visible(anchor)) continue;
            if (/^(计算器|UpToDate Pathways|诊疗实践更新|重要更新|患者教育|药物信息|专科下主题)$/.test(anchorText)) continue;
            const title = anchorText;
            const rank = hrefRank(href);
            rows.push({
              title,
              snippet: anchorText,
              links: [],
              url: href,
              displayRank: rank,
              y: anchorRect.y,
              anchorY: anchorRect.y,
              height: anchorRect.height,
              textLength: anchorText.length,
              slug: articleSlug(href),
            });
          }
          return rows;
        }
        """,
        query,
    )

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(candidates, key=lambda row: (rank_sort_key(row.get("displayRank")), float(row.get("y") or 0))):
        snippet = str(item.get("snippet") or "").strip()
        title = str(item.get("title") or "").strip()
        if not snippet:
            continue
        url = str(item.get("url") or "").strip()
        key = canonical_result_key(url) or squash(f"{title} {snippet[:240]}")
        if key in seen:
            continue
        if is_navigation_block(snippet):
            continue
        seen.add(key)
        results.append(
            {
                "display_rank": item.get("displayRank"),
                "title": title,
                "url": url,
                "snippet": snippet,
                "links": item.get("links") or [],
            }
        )
        if len(results) >= max_results:
            break
    return results


def cache_search_result_articles(
    context: Any,
    search_result: dict[str, Any],
    cache_dir: Path,
    top_k: int,
    max_chars: int,
    refresh: bool,
    timeout_error: type[Exception],
) -> None:
    query = str(search_result.get("query") or "")
    results = unique_article_results(list(search_result.get("results") or []))[: max(0, top_k)]
    for index, item in enumerate(results, start=1):
        if not item.get("url"):
            continue
        detail_page = context.new_page()
        try:
            print(f"[uptodate] caching detail {index}/{len(results)}: {item.get('title') or item.get('url')}", flush=True)
            detail_page.goto(str(item["url"]), wait_until="domcontentloaded", timeout=30000)
            wait_for_search_settle(detail_page, timeout_error)
            detail = extract_article_detail(detail_page, query=query, max_chars=max_chars)
            cache_path = cache_path_for_detail(cache_dir, item, detail)
            item["cache_path"] = str(cache_path)
            if cache_path.exists() and not refresh:
                item["cache_status"] = "hit"
                continue
            write_article_cache_md(cache_path, query=query, result=item, detail=detail)
            item["cache_status"] = "written"
        except Exception as exc:
            item["cache_status"] = "failed"
            item["cache_error"] = str(exc)
        finally:
            detail_page.close()


def unique_article_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in results:
        url = str(item.get("url") or "")
        if not url:
            continue
        key = canonical_article_url(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def cache_path_for_result(cache_dir: Path, result: dict[str, Any]) -> Path:
    url = str(result.get("url") or "")
    path_part = urlparse(canonical_article_url(url)).path.strip("/").split("/")[-1]
    title = str(result.get("title") or "")
    name_source = path_part or title or url or "uptodate_article"
    return cache_dir / f"{safe_filename(name_source)}.md"


def cache_path_for_detail(cache_dir: Path, result: dict[str, Any], detail: dict[str, Any]) -> Path:
    title = clean_article_title(str(detail.get("page_title") or result.get("title") or "").strip())
    if title:
        return cache_dir / f"{safe_filename(title)}.md"
    return cache_path_for_result(cache_dir, result)


def clean_article_title(title: str) -> str:
    for suffix in (" - UpToDate", "- UpToDate", " | UpToDate"):
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()
    return title.strip()


def canonical_article_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def canonical_result_key(url: str) -> str:
    if not url:
        return ""
    return canonical_article_url(url)


def extract_article_detail(page: Any, query: str, max_chars: int) -> dict[str, Any]:
    return page.locator("body").evaluate(
        """
        (body, payload) => {
          const maxChars = payload.maxChars || 200000;
          const visible = el => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 20 && rect.height > 10;
          };
          const articleSelectors = [
            'article',
            'main',
            '[role="main"]',
            '#topicContent',
            '#articleContent',
            '.topicContent',
            '.articleContent',
            '.utd-article',
            '.document-content',
            '.content'
          ];

          const titleNode = body.querySelector('h1');
          const pageTitle = (titleNode ? titleNode.innerText || titleNode.textContent : document.title || '').trim();

          let root = body;
          for (const selector of articleSelectors) {
            const candidate = body.querySelector(selector);
            if (!candidate || !visible(candidate)) continue;
            const text = candidate.innerText || candidate.textContent || '';
            const rootText = root.innerText || root.textContent || '';
            if (text.length > rootText.length * 0.2) {
              root = candidate;
              break;
            }
          }

          const skippedPattern = /专题提纲|图表|相关专题|Select Language|请选择语言|AUTHORS?:|SECTION EDITORS?:|DEPUTY EDITORS?:|翻译:|Contributor Disclosures|所有专题都会依据|文献评审有效期|专题最后更新日期|newer version|打印|分享|利益披露|版权|UpToDate Pathways|Calculators|Drug Information/i;
          const isBodyStart = text => {
            const normalized = text.trim();
            if (/^(引言|概述|简介|背景|流行病学|定义|分类|病因|微生物学|临床表现|诊断|治疗|预防|临床特征|总结与推荐)$/.test(normalized)) return true;
            return /^(INTRODUCTION|OVERVIEW|BACKGROUND|EPIDEMIOLOGY|DEFINITION|CLASSIFICATION|MICROBIOLOGY|CLINICAL|DIAGNOSIS|TREATMENT|PREVENTION|SUMMARY AND RECOMMENDATIONS)\\b/i.test(normalized);
          };
          const rawLines = (root.innerText || root.textContent || '')
            .replace(/\\r\\n/g, '\\n')
            .split('\\n')
            .map(line => line.trim())
            .filter(Boolean);
          let startIndex = rawLines.findIndex(line => isBodyStart(line));
          if (startIndex < 0) {
            startIndex = rawLines.findIndex(line => /^(引言|概述|流行病学|临床表现|诊断|治疗|预防)/.test(line));
          }
          const keptLines = [];
          let total = 0;
          for (let index = Math.max(0, startIndex); index < rawLines.length; index += 1) {
            const line = rawLines[index];
            if (index < startIndex) continue;
            if (skippedPattern.test(line) && line.length < 500) continue;
            const separator = keptLines.length ? '\\n' : '';
            const nextLen = separator.length + line.length;
            if (total + nextLen > maxChars) {
              const remaining = maxChars - total - separator.length;
              if (remaining > 20) keptLines.push(separator + line.slice(0, remaining - 3) + '...');
              break;
            }
            keptLines.push(separator + line);
            total += nextLen;
          }

          const article_text = startIndex >= 0 ? keptLines.join('') : '';

          return {
            page_title: pageTitle,
            browser_title: document.title,
            url: location.href,
            article_text,
            extracted_chars: article_text.length,
          };
        }
        """,
        {"query": query, "maxChars": max(500, max_chars)},
    )


def write_article_cache_md(cache_path: Path, query: str, result: dict[str, Any], detail: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    text = str(detail.get("article_text") or "").strip()
    cache_path.write_text(text + "\n", encoding="utf-8", newline="\n")


def rank_sort_key(value: Any) -> int:
    try:
        if value is None:
            return 10**9
        return int(value)
    except Exception:
        return 10**9


def is_navigation_block(text: str) -> bool:
    lowered = text.lower()
    navigation_terms = ("select language", "uptodate pathways", "calculators", "drug information")
    if any(term in lowered for term in navigation_terms) and len(text) < 180:
        return True
    return False


def squash(text: str) -> str:
    return " ".join(str(text).split())


def safe_filename(text: str) -> str:
    invalid = '<>:"/\\|?*\x00'
    cleaned = "".join("_" if char in invalid or ord(char) < 32 else char for char in text)
    return cleaned.strip(" .")[:120] or "search"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
