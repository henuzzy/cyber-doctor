"""Collect three verified PMC full-text articles per pathogen, with resumable state.

Only XML containing a substantive article body counts as full text. Abstract-only
responses are recorded as rejected candidates, never as completed downloads.
All requests are serial and followed by a configurable pause (minimum 2 seconds).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
MARKER = {"kind": "pubmed-fulltext-collector", "version": 1}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalized(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s-]", " ", value.replace("_", " ")).casefold().split())


def folder_name(name_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name_id).strip("_")[:100]
    return (slug or "pathogen") + "_" + hashlib.sha256(name_id.encode()).hexdigest()[:8]


def local_tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def element_text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def first_element(node: ET.Element, tag: str) -> ET.Element | None:
    return next((child for child in node.iter() if local_tag(child) == tag), None)


def parse_fulltext(data: bytes, min_body_chars: int = 500) -> tuple[ET.Element, ET.Element]:
    root = ET.fromstring(data)
    article = root if local_tag(root) == "article" else first_element(root, "article")
    if article is None:
        raise ValueError("response contains no article XML")
    if article.get("article-type") in {"correction", "retraction", "addendum"}:
        raise ValueError("correction/retraction/addendum is not a research article")
    body = next((node for node in article if local_tag(node) == "body"), None)
    if body is None:
        raise ValueError("abstract_only: no article body")
    if len(element_text(body)) < min_body_chars:
        raise ValueError("body_too_short: incomplete or unverified full text")
    if not any(local_tag(node) == "p" and element_text(node) for node in body.iter()):
        raise ValueError("body contains no prose paragraphs")
    return article, body


def check_species(article: ET.Element, body: ET.Element, names: list[str]) -> bool:
    pieces = [element_text(body)]
    for tag in ("article-title", "abstract"):
        node = first_element(article, tag)
        if node is not None:
            pieces.append(element_text(node))
    haystack = " " + normalized(" ".join(pieces)) + " "
    return any(" " + normalized(name) + " " in haystack for name in names if name)


def check_pmid(article: ET.Element, pmid: str) -> None:
    for node in article.iter():
        if local_tag(node) == "article-id" and node.get("pub-id-type") == "pmid":
            if element_text(node) != pmid:
                raise ValueError("full-text PMID does not match PubMed record")


def render_xml(node: ET.Element, level: int = 2) -> str:
    """Preserve body prose, headings, lists, tables, captions and references."""
    tag = local_tag(node)
    if tag == "sec":
        return "\n\n".join(render_xml(child, level + 1) for child in node)
    if tag == "title":
        return "#" * min(level, 6) + " " + element_text(node)
    if tag in {"p", "ref", "disp-quote", "disp-formula"}:
        return element_text(node)
    if tag == "list":
        return "\n".join("- " + element_text(child) for child in node if local_tag(child) == "list-item")
    if tag == "table":
        rows = []
        for row in node.iter():
            if local_tag(row) == "tr":
                rows.append(" | ".join(element_text(cell) for cell in row if local_tag(cell) in {"th", "td"}))
        return "\n".join(rows)
    if tag in {"fig", "table-wrap"}:
        return "\n\n".join(render_xml(child, level) for child in node)
    if tag in {"label", "caption"}:
        return element_text(node)
    return "\n\n".join(part for child in node if (part := render_xml(child, level)))


def article_markdown(article: ET.Element, body: ET.Element, metadata: dict) -> str:
    lines = [
        "# " + metadata["title"], "",
        "- PMID: " + metadata["pmid"],
        "- PMCID: " + metadata["pmcid"],
        "- DOI: " + metadata.get("doi", ""),
        "- Full-text source: " + metadata["fulltext_url"],
        "- Retrieved: " + metadata["retrieved_at"],
        "- Content type: verified full text (JATS XML)", "",
    ]
    abstract = first_element(article, "abstract")
    if abstract is not None:
        lines += ["## Abstract", "", element_text(abstract), ""]
    lines += ["## Full text", "", render_xml(body), ""]
    back = next((node for node in article if local_tag(node) == "back"), None)
    if back is not None:
        lines += ["## References and back matter", "", render_xml(back), ""]
    return "\n".join(lines)


class StopRequested(Exception):
    pass


class HTTPFailure(Exception):
    pass


class ServicePaused(HTTPFailure):
    """Stop the run after repeated throttling instead of hitting the next pathogen."""


class RateLimitedHTTP:
    def __init__(self, root: Path, pause: float = 2, attempts: int = 3):
        if pause < 2:
            raise ValueError("pause must be at least 2 seconds")
        self.root, self.pause, self.attempts = root, pause, attempts
        self.count = 0

    def check_stop(self) -> None:
        if (self.root / "stop.request").exists():
            raise StopRequested()

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))

    def get(self, url: str, params: dict | None = None) -> bytes:
        if params:
            url += "?" + urllib.parse.urlencode(params)
        host = urllib.parse.urlparse(url).netloc
        for attempt in range(self.attempts):
            self.check_stop()
            retry_delay = 0.0
            error: Exception | None = None
            request = urllib.request.Request(url, headers={"User-Agent": "cyber-doctor-fulltext/1.0"})
            started = now()
            status: int | str = "network_error"
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    status = response.status
                    data = response.read(25_000_001)
                    if len(data) > 25_000_000:
                        raise HTTPFailure("response exceeds 25 MB")
                return data
            except urllib.error.HTTPError as exc:
                status = exc.code
                error = HTTPFailure(f"HTTP {exc.code} from {host}")
                if exc.code in {403, 429, 500, 502, 503, 504}:
                    header = exc.headers.get("Retry-After", "")
                    try:
                        server_delay = float(header) if header.isdigit() else max(0, parsedate_to_datetime(header).timestamp() - time.time())
                    except (ValueError, TypeError):
                        server_delay = 0
                    retry_delay = max(60 * 2**attempt, server_delay)
                    if exc.code == 403:
                        raise HTTPFailure(f"HTTP 403 from {host}; access denied") from exc
                else:
                    raise error from exc
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
                # A 200 response can still end before its body has fully arrived.
                status = "network_error"
                error = HTTPFailure(f"network failure from {host}: {type(exc).__name__}")
                retry_delay = 15 * 2**attempt
            finally:
                with (self.root / "requests.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps({"started_at": started, "completed_at": now(), "host": host,
                                          "status": status, "pause_after_seconds": self.pause}) + "\n")
                self.count += 1
                self.wait(self.pause)
            if attempt + 1 == self.attempts:
                if status in {429, 503}:
                    raise ServicePaused(f"Repeated HTTP {status} from {host}; run paused")
                raise error or HTTPFailure("request failed")
            self.wait(retry_delay)
        raise AssertionError("unreachable")

    def eutils(self, endpoint: str, params: dict) -> bytes:
        params = {**params, "tool": "cyber_doctor_fulltext"}
        if os.environ.get("NCBI_EMAIL"):
            params["email"] = os.environ["NCBI_EMAIL"]
        if os.environ.get("NCBI_API_KEY"):
            params["api_key"] = os.environ["NCBI_API_KEY"]
        try:
            return self.get(EUTILS + endpoint, params)
        except HTTPFailure as exc:
            if params.get("db") == "pubmed" and "HTTP 403" in str(exc):
                raise ServicePaused("PubMed access denied (HTTP 403); run paused") from exc
            raise


class Store:
    def __init__(self, root: Path, create: bool = False):
        self.root = root.expanduser().resolve()
        marker = self.root / "collector.json"
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            if not marker.exists():
                if any(self.root.iterdir()):
                    raise ValueError("output directory is not empty and has no collector marker")
                atomic_json(marker, MARKER)
        if not marker.exists() or read_json(marker) != MARKER:
            raise ValueError("not a pubmed-fulltext collector directory")
        for name in ("pathogens", "cache", "trash"):
            self.child(name).mkdir(exist_ok=True)

    def child(self, relative: str | Path) -> Path:
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root) or path.resolve() == self.root:
            raise ValueError("path escapes collector output directory")
        return path

    @contextlib.contextmanager
    def lock(self):
        handle = self.child("collector.lock").open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ValueError("collector is running; use stop and wait before audit/reset/reject") from exc
        try:
            yield
        finally:
            handle.close()

    def folder(self, name_id: str) -> Path:
        return self.child(Path("pathogens") / folder_name(name_id))

    def load(self, name_id: str) -> dict | None:
        path = self.folder(name_id) / "manifest.json"
        return read_json(path) if path.exists() else None

    def save(self, state: dict) -> None:
        state["updated_at"] = now()
        atomic_json(self.child(self.folder(state["NameID"]).relative_to(self.root) / "manifest.json"), state)

    def archive(self, path: Path) -> str:
        relative = path.relative_to(self.root)
        path = self.child(relative)
        if not path.exists():
            return ""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        target = self.child(Path("trash") / stamp / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        return str(target.relative_to(self.root))

    def states(self) -> list[dict]:
        return [read_json(p) for p in sorted(self.child("pathogens").glob("*/manifest.json"))]

    def summary(self, selected: list[str] | None = None) -> dict:
        states = self.states()
        if selected is not None:
            states = [s for s in states if s["NameID"] in selected]
        counts = Counter(s.get("status", "pending") for s in states)
        n = len(selected) if selected is not None else len(states)
        result = {"updated_at": now(), "selected_pathogens": n, "recorded_pathogens": len(states),
                  "status_counts": dict(counts), "verified_fulltext_articles": sum(len(s.get("articles", [])) for s in states),
                  "complete_pathogens": counts["complete"], "remaining_pathogens": n - counts["complete"]}
        atomic_json(self.child("summary.json"), result)
        temporary = self.child("results.jsonl.tmp")
        temporary.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in states), encoding="utf-8")
        temporary.replace(self.child("results.jsonl"))
        return result


def input_rows(path: Path) -> list[dict]:
    records = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("NameID"), str) or not row["NameID"]:
            raise ValueError(f"missing NameID at input line {line_no}")
        records.setdefault(row["NameID"], row)
    return list(records.values())


def new_state(row: dict, target: int, old: dict | None = None) -> dict:
    names = row.get("Name") or {}
    species = " ".join(str(names.get("种-拉丁名") or "").replace("_", " ").split())
    fingerprint = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {**(old or {}), "NameID": row["NameID"], "names": names, "species_latin": species,
            "input_record_sha256": fingerprint, "target_articles": target,
            "status": "pending", "articles": (old or {}).get("articles", []),
            "rejected_pmids": (old or {}).get("rejected_pmids", {}), "candidates": []}


def audit_state(store: Store, state: dict, min_chars: int = 500) -> list[dict]:
    kept, invalid = [], []
    for record in state.get("articles", []):
        try:
            raw = store.child(record["xml_path"])
            md = store.child(record["markdown_path"])
            data = raw.read_bytes()
            article, body = parse_fulltext(data, min_chars)
            check_pmid(article, record["pmid"])
            if not check_species(article, body, [state["species_latin"]]):
                raise ValueError("target species absent from article title/abstract/body")
            if hashlib.sha256(data).hexdigest() != record.get("xml_sha256"):
                raise ValueError("article XML changed since validation")
            if not md.is_file() or "## Full text" not in md.read_text(encoding="utf-8"):
                raise ValueError("full-text Markdown missing")
            if hashlib.sha256(md.read_bytes()).hexdigest() != record.get("markdown_sha256"):
                raise ValueError("article Markdown changed since validation")
            if record.get("content_type") != "fulltext":
                raise ValueError("record is not marked full text")
            kept.append(record)
        except (OSError, ValueError, KeyError, ET.ParseError) as exc:
            invalid.append({"pmid": record.get("pmid", ""), "reason": str(exc)})
    state["articles"] = kept
    state["audit_invalid"] = invalid
    state["status"] = "complete" if len(kept) >= state["target_articles"] else "pending"
    return invalid


def parse_pubmed(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    records = []
    for article in root.findall("PubmedArticle"):
        citation = article.find("MedlineCitation")
        node = article.find("MedlineCitation/Article")
        if citation is None or node is None:
            continue
        ids = {x.get("IdType"): element_text(x) for x in article.findall("PubmedData/ArticleIdList/ArticleId")}
        records.append({"pmid": citation.findtext("PMID", ""), "pmcid": ids.get("pmc", ""),
                        "doi": ids.get("doi", ""), "title": element_text(node.find("ArticleTitle")),
                        "journal": node.findtext("Journal/Title", ""),
                        "year": node.findtext("Journal/JournalIssue/PubDate/Year", ""),
                        "publication_types": [element_text(x) for x in node.findall("PublicationTypeList/PublicationType")]})
    return records


def download_article(http: RateLimitedHTTP, store: Store, meta: dict, species: str, min_chars: int) -> tuple[bytes, str, ET.Element, ET.Element]:
    pmcid = meta["pmcid"]
    if not re.fullmatch(r"PMC\d+", pmcid):
        raise ValueError("no valid PMCID for open full text")
    cache = store.child(Path("cache") / (pmcid + ".xml"))
    if cache.is_file():
        try:
            data = cache.read_bytes()
            article, body = parse_fulltext(data, min_chars)
            check_pmid(article, meta["pmid"])
            if check_species(article, body, [species]):
                return data, "validated_cache", article, body
        except (ET.ParseError, ValueError):
            store.archive(cache)
    errors = []
    sources = [("NCBI_PMC", lambda: http.eutils("efetch.fcgi", {"db": "pmc", "id": pmcid[3:], "retmode": "xml"})),
               ("Europe_PMC", lambda: http.get(EUROPE_PMC + pmcid + "/fullTextXML"))]
    for source, fetch in sources:
        try:
            data = fetch()
            article, body = parse_fulltext(data, min_chars)
            if not check_species(article, body, [species]):
                raise ValueError("target species absent from title/abstract/body")
            check_pmid(article, meta["pmid"])
            temporary = cache.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(cache)
            return data, source, article, body
        except ServicePaused:
            raise
        except (HTTPFailure, ValueError, ET.ParseError) as exc:
            errors.append(source + ": " + str(exc))
    raise ValueError("; ".join(errors))


def save_article(store: Store, state: dict, meta: dict, data: bytes, source: str, article: ET.Element, body: ET.Element) -> dict:
    directory = store.child(store.folder(state["NameID"]).relative_to(store.root) / "articles" / ("PMID_" + meta["pmid"]))
    if directory.exists():
        store.archive(directory)
    directory.mkdir(parents=True)
    metadata = {**meta, "NameID": state["NameID"], "species_latin": state["species_latin"],
                "content_type": "fulltext", "download_source": source,
                "fulltext_url": "https://pmc.ncbi.nlm.nih.gov/articles/" + meta["pmcid"] + "/",
                "retrieved_at": now(), "body_chars": len(element_text(body)),
                "xml_sha256": hashlib.sha256(data).hexdigest(),
                "license": element_text(first_element(article, "license")),
                "xml_path": str((directory / "source.xml").relative_to(store.root)),
                "markdown_path": str((directory / "article.md").relative_to(store.root))}
    (directory / "source.xml").write_bytes(data)
    markdown = article_markdown(article, body, metadata)
    (directory / "article.md").write_text(markdown, encoding="utf-8")
    metadata["markdown_sha256"] = hashlib.sha256((directory / "article.md").read_bytes()).hexdigest()
    atomic_json(directory / "metadata.json", metadata)
    return metadata


def collect_one(http: RateLimitedHTTP, store: Store, state: dict, args: argparse.Namespace) -> None:
    species = state["species_latin"]
    if not species:
        state["status"] = "no_query_name"
        store.save(state)
        return
    state["status"] = "in_progress"
    state["started_at"] = now()
    query = '"' + species.replace('"', "") + '"[Title/Abstract] AND "pubmed pmc"[sb]'
    state["query"] = query
    store.save(state)
    existing = {r["pmid"] for r in state["articles"]}
    examined = 0
    total = None
    for start in range(0, args.candidate_limit, 25):
        http.check_stop()
        result = json.loads(http.eutils("esearch.fcgi", {"db": "pubmed", "term": query,
                            "retmode": "json", "retmax": min(25, args.candidate_limit - start),
                            "retstart": start, "sort": "relevance"}))
        search = result.get("esearchresult") or {}
        if search.get("errorlist") or result.get("error"):
            raise ValueError("PubMed search error: " + str(search.get("errorlist") or result.get("error")))
        ids = search.get("idlist", [])
        total = int(search.get("count", 0))
        state["pubmed_pmc_match_count"] = total
        if not ids:
            break
        records = parse_pubmed(http.eutils("efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}))
        by_id = {r["pmid"]: r for r in records}
        for pmid in ids:
            http.check_stop()
            examined += 1
            if pmid in existing or pmid in state["rejected_pmids"]:
                continue
            meta = by_id.get(pmid)
            if meta is None:
                state["candidates"].append({"pmid": pmid, "status": "metadata_missing"})
                continue
            try:
                data, source, article, body = download_article(http, store, meta, species, args.min_body_chars)
                record = save_article(store, state, meta, data, source, article, body)
                state["articles"].append(record)
                existing.add(pmid)
                state["candidates"].append({"pmid": pmid, "status": "fulltext_saved"})
                print(f"ARTICLE {state['NameID']} PMID={pmid} body_chars={record['body_chars']}", flush=True)
            except (ValueError, ET.ParseError) as exc:
                state["candidates"].append({"pmid": pmid, "status": "no_verified_fulltext", "reason": str(exc)})
            store.save(state)
            if len(state["articles"]) >= args.articles:
                state["status"] = "complete"
                state["finished_at"] = now()
                store.save(state)
                return
        if start + len(ids) >= total:
            break
    state["status"] = "partial" if state["articles"] else "no_fulltext"
    state["examined_candidates"] = examined
    state["candidate_pool_exhausted"] = total is not None and examined >= total
    state["finished_at"] = now()
    store.save(state)


def run_crawl(args: argparse.Namespace, store: Store) -> None:
    rows = input_rows(Path(args.input))
    if args.name_id:
        requested = set(args.name_id)
        rows = [r for r in rows if r["NameID"] in requested]
        missing = requested - {r["NameID"] for r in rows}
        if missing:
            raise ValueError("NameID not found in input: " + ", ".join(sorted(missing)))
    elif args.limit:
        rows = rows[:args.limit]
    selected = [r["NameID"] for r in rows]
    http = RateLimitedHTTP(store.root, args.pause)
    run = {"status": "running", "started_at": now(), "pid": os.getpid(), "host": socket.gethostname(),
           "input_sha256": hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
           "selected_name_ids": selected, "pause_seconds": args.pause,
           "target_articles": args.articles, "candidate_limit": args.candidate_limit}
    atomic_json(store.child("run.json"), run)
    store.summary(selected)
    # A stop signal is never cleared implicitly: resumption is an explicit option.
    if args.resume_stopped and store.child("stop.request").exists():
        store.child("stop.request").unlink()
    current = None
    try:
        for index, row in enumerate(rows, 1):
            http.check_stop()
            old = store.load(row["NameID"])
            current = new_state(row, args.articles, old)
            audit_state(store, current, args.min_body_chars)
            # Changed pathogen naming must be rechecked against the current input.
            if current["status"] == "complete":
                store.save(current)
                print(f"SKIP {index}/{len(rows)} {row['NameID']} validated_fulltext={len(current['articles'])}", flush=True)
            else:
                print(f"START {index}/{len(rows)} {row['NameID']} {current['species_latin']}", flush=True)
                try:
                    collect_one(http, store, current, args)
                except ServicePaused:
                    raise
                except (HTTPFailure, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
                    current["status"] = "error"
                    current["error"] = str(exc)
                    store.save(current)
                    print(f"ERROR {row['NameID']} {exc}", flush=True)
                print(f"DONE {index}/{len(rows)} {row['NameID']} status={current['status']} fulltext={len(current['articles'])}", flush=True)
            store.summary(selected)
        run["status"] = "finished"
    except ServicePaused as exc:
        run["status"] = "paused_by_server"
        run["pause_reason"] = str(exc)
        if current and current.get("status") == "in_progress":
            current["status"] = "pending"
            store.save(current)
        atomic_json(store.child("stop.request"), {"requested_at": now(), "reason": str(exc)})
        print(str(exc), flush=True)
    except (StopRequested, KeyboardInterrupt):
        run["status"] = "stopped"
        if current and current.get("status") == "in_progress":
            current["status"] = "pending"
            store.save(current)
    except Exception:
        run["status"] = "failed"
        raise
    finally:
        if run["status"] == "finished":
            invalid = []
            for name_id in selected:
                state = store.load(name_id)
                if state:
                    previous_status = state["status"]
                    findings = audit_state(store, state, args.min_body_chars)
                    if not findings and previous_status not in {"complete", "pending", "in_progress"}:
                        state["status"] = previous_status
                    invalid.extend({"NameID": name_id, **item} for item in findings)
                    store.save(state)
            atomic_json(store.child("audit.json"), {"checked_at": now(), "invalid_articles": invalid})
        run["ended_at"] = now()
        run["http_requests"] = http.count
        atomic_json(store.child("run.json"), run)
        print(json.dumps(store.summary(selected), ensure_ascii=False), flush=True)


def manage(args: argparse.Namespace, store: Store) -> None:
    if args.command == "audit":
        checked = store.states()
        if args.name_id:
            checked = [s for s in checked if s["NameID"] in args.name_id]
        invalid = []
        for state in checked:
            for record in audit_state(store, state, args.min_body_chars):
                invalid.append({"NameID": state["NameID"], **record})
            store.save(state)
        atomic_json(store.child("audit.json"), {"checked_at": now(), "invalid_articles": invalid})
        print(json.dumps({"checked_pathogens": len(checked), "invalid_articles": invalid}, ensure_ascii=False))
    elif args.command in {"reset", "reject"}:
        if args.command == "reject" and not re.fullmatch(r"\d+", args.pmid):
            raise ValueError("PMID must contain only digits")
        for name_id in args.name_id:
            state = store.load(name_id)
            if state is None:
                raise ValueError("no stored pathogen: " + name_id)
            if args.command == "reset":
                if args.drop_cache:
                    for record in state.get("articles", []):
                        pmcid = record.get("pmcid", "")
                        if re.fullmatch(r"PMC\d+", pmcid):
                            store.archive(store.child(Path("cache") / (pmcid + ".xml")))
                archived = store.archive(store.folder(name_id))
                state.update(articles=[], candidates=[], status="pending", reset_at=now())
                if args.clear_rejections:
                    state["rejected_pmids"] = {}
            else:
                archived = store.archive(store.child(store.folder(name_id).relative_to(store.root) / "articles" / ("PMID_" + args.pmid)))
                state["articles"] = [r for r in state.get("articles", []) if r["pmid"] != args.pmid]
                state.setdefault("rejected_pmids", {})[args.pmid] = {"reason": args.reason, "rejected_at": now()}
                state.update(status="pending", candidates=[])
            store.save(state)
            print(json.dumps({"NameID": name_id, "status": "pending", "archived_to": archived}, ensure_ascii=False))
    selected = (read_json(store.child("run.json")) if store.child("run.json").exists() else {}).get("selected_name_ids")
    store.summary(selected)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subs = result.add_subparsers(dest="command", required=True)
    for name in ("crawl", "status", "audit", "reset", "reject", "stop"):
        sub = subs.add_parser(name)
        sub.add_argument("--output-dir", required=True)
        if name in {"crawl", "audit", "reset", "reject"}:
            sub.add_argument("--name-id", action="append", required=name in {"reset", "reject"})
        if name in {"crawl", "audit"}:
            sub.add_argument("--min-body-chars", type=int, default=500)
        if name == "crawl":
            sub.add_argument("--input", required=True)
            sub.add_argument("--limit", type=int, default=100, help="First N unique input pathogens; 0 means all.")
            sub.add_argument("--articles", type=int, default=3)
            sub.add_argument("--pause", type=float, default=2)
            sub.add_argument("--candidate-limit", type=int, default=100)
            sub.add_argument("--resume-stopped", action="store_true")
        elif name == "reset":
            sub.add_argument("--drop-cache", action="store_true", help="Archive associated XML cache too, to redownload.")
            sub.add_argument("--clear-rejections", action="store_true")
        elif name == "reject":
            sub.add_argument("--pmid", required=True)
            sub.add_argument("--reason", default="manually rejected after review")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "crawl" and (args.limit < 0 or args.articles < 1 or args.candidate_limit < 1 or args.pause < 2):
        raise ValueError("limit must be nonnegative; articles/candidates positive; pause at least 2")
    if hasattr(args, "min_body_chars") and args.min_body_chars < 1:
        raise ValueError("min-body-chars must be positive")
    store = Store(Path(args.output_dir), create=args.command == "crawl")
    if args.command == "stop":
        atomic_json(store.child("stop.request"), {"requested_at": now()})
        print("Stop requested; the current HTTP operation will finish before pausing.")
    elif args.command == "status":
        run = read_json(store.child("run.json")) if store.child("run.json").exists() else {}
        # Read atomic snapshots without taking the writer lock or modifying state.
        summary = read_json(store.child("summary.json")) if store.child("summary.json").exists() else {}
        print(json.dumps({"run": run, "summary": summary}, ensure_ascii=False, indent=2))
    else:
        with store.lock():
            if args.command == "crawl":
                run_crawl(args, store)
            else:
                manage(args, store)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
