from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from http.client import IncompleteRead
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/data/collect_pubmed_fulltext.py"
SPEC = importlib.util.spec_from_file_location("pubmed_fulltext_collector", SCRIPT)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)

NAME_ID = "id|00099539|BAC|S_1894172|"
SPECIES = "Mycobacterium paragordonae"
ROW = {"NameID": NAME_ID, "Name": {"种-拉丁名": "Mycobacterium_paragordonae", "种-中文名": "副戈登分枝杆菌"}}


def full_xml(pmid="101", species=SPECIES):
    prose = (species + " infection was confirmed in a clinical investigation. ") * 20
    return (f'<article article-type="case-report"><front><article-meta><article-id pub-id-type="pmid">{pmid}</article-id>'
            f'<title-group><article-title>{species} infection</article-title></title-group>'
            '<abstract><p>Abstract.</p></abstract></article-meta></front>'
            f'<body><sec><title>Case presentation</title><p>{prose}</p>'
            '<fig><label>Figure 1</label><caption><p>Imaging caption.</p></caption></fig>'
            '<table-wrap><caption>Findings</caption><table><tbody><tr><td>Culture</td><td>Positive</td></tr></tbody></table></table-wrap>'
            '</sec></body><back><ref-list><ref>Reference citation.</ref></ref-list></back></article>').encode()


def meta(pmid="101"):
    return {"pmid": pmid, "pmcid": "PMC" + pmid, "title": SPECIES + " infection", "doi": ""}


class FakeHTTP:
    def __init__(self, ids, bodies=None):
        self.ids, self.bodies, self.calls = ids, bodies or {}, []

    def check_stop(self):
        pass

    def eutils(self, endpoint, params):
        self.calls.append((endpoint, params))
        if endpoint == "esearch.fcgi":
            return json.dumps({"esearchresult": {"count": str(len(self.ids)), "idlist": self.ids}}).encode()
        if params["db"] == "pmc":
            pmid = params["id"]
            return self.bodies.get(pmid, full_xml(pmid))
        records = []
        for pmid in self.ids:
            records.append(f'<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article><ArticleTitle>{SPECIES} infection</ArticleTitle>'
                           f'</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC{pmid}</ArticleId>'
                           '</ArticleIdList></PubmedData></PubmedArticle>')
        return ("<PubmedArticleSet>" + "".join(records) + "</PubmedArticleSet>").encode()

    def get(self, url):
        self.calls.append((url, {}))
        pmid = url.split("/PMC", 1)[1].split("/", 1)[0]
        return self.bodies.get(pmid, full_xml(pmid))


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "output"
        self.store = collector.Store(self.root, create=True)
        self.args = collector.parser().parse_args(["crawl", "--output-dir", str(self.root), "--input", "unused"])

    def save(self, state, pmid="101"):
        data = full_xml(pmid)
        article, body = collector.parse_fulltext(data)
        saved = collector.save_article(self.store, state, meta(pmid), data, "test", article, body)
        state["articles"].append(saved)
        self.store.save(state)
        return saved

    def quiet_main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return collector.main(list(args))

    def test_abstract_only_never_counts_as_fulltext(self):
        data = ("<article><front><abstract><p>" + "long abstract " * 300 + "</p></abstract></front></article>").encode()
        with self.assertRaisesRegex(ValueError, "abstract_only"):
            collector.parse_fulltext(data)
        state = collector.new_state(ROW, 3)
        http = FakeHTTP(["101"], {"101": data})
        collector.collect_one(http, self.store, state, self.args)
        self.assertEqual(state["status"], "no_fulltext")
        self.assertEqual(state["articles"], [])
        self.assertFalse((self.root / "cache/PMC101.xml").exists())

    def test_fulltext_preserves_body_tables_captions_and_references(self):
        state = collector.new_state(ROW, 3)
        saved = self.save(state)
        markdown = self.store.child(saved["markdown_path"]).read_text(encoding="utf-8")
        for text in ["## Full text", "Case presentation", "Imaging caption", "Culture | Positive", "Reference citation"]:
            self.assertIn(text, markdown)
        self.assertEqual(collector.audit_state(self.store, state), [])

    def test_completed_pathogen_skips_network_but_deleted_article_is_repaired(self):
        state = collector.new_state(ROW, 3)
        for pmid in ["101", "102", "103"]:
            self.save(state, pmid)
        source = Path(self.temp.name) / "input.jsonl"
        source.write_text(json.dumps(ROW), encoding="utf-8")
        with patch.object(collector.RateLimitedHTTP, "eutils", side_effect=AssertionError("should skip")):
            self.quiet_main("crawl", "--input", str(source), "--output-dir", str(self.root))
        state = self.store.load(NAME_ID)
        self.assertEqual(state["status"], "complete")
        self.store.child(state["articles"][0]["xml_path"]).unlink()
        invalid = collector.audit_state(self.store, state)
        self.assertEqual(len(invalid), 1)
        self.assertEqual(state["status"], "pending")
        self.assertEqual(len(state["articles"]), 2)
        http = FakeHTTP(["101", "102", "103", "104"])
        with contextlib.redirect_stdout(io.StringIO()):
            collector.collect_one(http, self.store, state, self.args)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(len(state["articles"]), 3)

    def test_modified_markdown_is_not_trusted(self):
        state = collector.new_state(ROW, 1)
        saved = self.save(state)
        self.store.child(saved["markdown_path"]).write_text("## Full text\nOnly an abstract.", encoding="utf-8")
        invalid = collector.audit_state(self.store, state)
        self.assertIn("Markdown changed", invalid[0]["reason"])
        self.assertEqual(state["status"], "pending")

    def test_reject_archives_article_and_selects_a_different_pmid(self):
        state = collector.new_state(ROW, 3)
        for pmid in ["101", "102", "103"]:
            self.save(state, pmid)
        self.quiet_main("reject", "--output-dir", str(self.root), "--name-id", NAME_ID, "--pmid", "101")
        state = self.store.load(NAME_ID)
        self.assertIn("101", state["rejected_pmids"])
        self.assertEqual(len(state["articles"]), 2)
        self.assertTrue(list((self.root / "trash").rglob("source.xml")))
        http = FakeHTTP(["101", "102", "103", "104"])
        with contextlib.redirect_stdout(io.StringIO()):
            collector.collect_one(http, self.store, state, self.args)
        self.assertEqual({r["pmid"] for r in state["articles"]}, {"102", "103", "104"})
        downloads = [p["id"] for _, p in http.calls if p.get("db") == "pmc"]
        self.assertEqual(downloads, ["104"])

    def test_reset_archives_pathogen_and_cache_preserving_rejections(self):
        state = collector.new_state(ROW, 3)
        saved = self.save(state)
        state["rejected_pmids"] = {"999": {"reason": "unrelated"}}
        self.store.save(state)
        (self.root / "cache/PMC101.xml").write_bytes(full_xml())
        self.quiet_main("reset", "--output-dir", str(self.root), "--name-id", NAME_ID, "--drop-cache")
        current = self.store.load(NAME_ID)
        self.assertEqual(current["status"], "pending")
        self.assertEqual(current["articles"], [])
        self.assertIn("999", current["rejected_pmids"])
        self.assertFalse(self.store.child(saved["xml_path"]).exists())
        self.assertFalse((self.root / "cache/PMC101.xml").exists())
        self.assertTrue(list((self.root / "trash").rglob("PMC101.xml")))

    def test_lock_prevents_reset_during_crawl(self):
        with self.store.lock():
            with self.assertRaisesRegex(ValueError, "collector is running"):
                self.quiet_main("reset", "--output-dir", str(self.root), "--name-id", NAME_ID)

    def test_output_paths_cannot_escape_and_existing_data_is_not_adopted(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.store.child("../../outside")
        existing = Path(self.temp.name) / "old_corpus"
        existing.mkdir()
        (existing / "valuable.md").write_text("original", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not empty"):
            collector.Store(existing, create=True)
        self.assertEqual((existing / "valuable.md").read_text(), "original")

    def test_species_mention_only_in_references_is_rejected(self):
        article, body = collector.parse_fulltext(full_xml(species="Another bacterium"))
        self.assertFalse(collector.check_species(article, body, [SPECIES]))

    def test_first_100_selection_keeps_input_order_and_deduplicates(self):
        source = Path(self.temp.name) / "input.jsonl"
        rows = [{**ROW, "NameID": f"id|{n:08d}|BAC|S_1|"} for n in range(105)]
        source.write_text("\n".join(json.dumps(r) for r in [rows[0], *rows]), encoding="utf-8")
        selected = []

        def collect(http, store, state, args):
            selected.append(state["NameID"])
            state["status"] = "no_fulltext"
            store.save(state)

        with patch.object(collector, "collect_one", side_effect=collect):
            self.quiet_main("crawl", "--input", str(source), "--output-dir", str(self.root))
        self.assertEqual(selected, [r["NameID"] for r in rows[:100]])

    def test_http_waits_two_seconds_after_success_and_failure(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                return b"ok"

        http = collector.RateLimitedHTTP(self.root, 2, attempts=2)
        waits = []
        error = urllib.error.HTTPError("https://example.org", 429, "limited", {"Retry-After": "90"}, None)
        with patch.object(collector.urllib.request, "urlopen", side_effect=[error, Response()]), patch.object(http, "wait", side_effect=waits.append):
            self.assertEqual(http.get("https://example.org"), b"ok")
        self.assertEqual(waits, [2, 90, 2])
        self.assertEqual(http.count, 2)
        logs = [json.loads(l) for l in (self.root / "requests.jsonl").read_text().splitlines()]
        self.assertEqual([r["status"] for r in logs], [429, 200])
        with self.assertRaises(ValueError):
            collector.RateLimitedHTTP(self.root, 0.4)

    def test_server_throttling_pauses_the_entire_run(self):
        source = Path(self.temp.name) / "input.jsonl"
        source.write_text(json.dumps(ROW), encoding="utf-8")
        with patch.object(collector, "collect_one", side_effect=collector.ServicePaused("rate limited")):
            self.quiet_main("crawl", "--input", str(source), "--output-dir", str(self.root))
        self.assertEqual(collector.read_json(self.root / "run.json")["status"], "paused_by_server")
        self.assertTrue((self.root / "stop.request").exists())

    def test_incomplete_response_retries_and_never_returns_partial_bytes(self):
        class Response:
            status = 200

            def __init__(self, interrupted):
                self.interrupted = interrupted

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                if self.interrupted:
                    raise IncompleteRead(b"partial XML")
                return b"complete XML"

        http = collector.RateLimitedHTTP(self.root, 2, attempts=2)
        waits = []
        with patch.object(collector.urllib.request, "urlopen", side_effect=[Response(True), Response(False)]), patch.object(http, "wait", side_effect=waits.append):
            self.assertEqual(http.get("https://example.org"), b"complete XML")
        self.assertEqual(waits, [2, 15, 2])
        logs = [json.loads(l) for l in (self.root / "requests.jsonl").read_text().splitlines()]
        self.assertEqual([r["status"] for r in logs], ["network_error", 200])
        with patch.object(collector.urllib.request, "urlopen", side_effect=[Response(True), Response(True)]), patch.object(http, "wait"):
            with self.assertRaisesRegex(collector.HTTPFailure, "IncompleteRead"):
                http.get("https://example.org")


if __name__ == "__main__":
    unittest.main()
