"""Tests for the generic per-article Qwen extraction command."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/data/extract_first20_structured.py"
SPEC = importlib.util.spec_from_file_location("article_extractor", SCRIPT)
EXTRACTOR = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = EXTRACTOR
SPEC.loader.exec_module(EXTRACTOR)


class GenericArticleExtractionTests(unittest.TestCase):
    def test_discovers_fulltext_and_joins_collector_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            article_dir = root / "pathogens" / "one" / "articles" / "PMID_42"
            article_dir.mkdir(parents=True)
            article = article_dir / "article.md"
            article.write_text("# Example study\n\n- PMID: 42\n- DOI: 10.1/example\n\n## Abstract\n\nExample bacterium caused disease.", encoding="utf-8")
            (article_dir / "metadata.json").write_text(json.dumps({"pmid": "42", "doi": "10.1/example", "title": "Example study", "NameID": "id|1|BAC|S_1|", "species_latin": "Example bacterium", "genus_latin": "Example", "publication_types": ["Journal Article"], "fulltext_url": "https://example.org/article"}), encoding="utf-8")
            found = EXTRACTOR.discover_articles(root)
            self.assertEqual(1, len(found))
            self.assertEqual("PubMed", found[0].source_type)
            record = EXTRACTOR.build_article_record(found[0], EXTRACTOR.normalize_model_result({"scope": "species_direct", "dimensions": {"致病性": [{"fact": "示例菌致病", "quote": "Example bacterium caused disease."}]}}), root)
            self.assertEqual("Example bacterium", record["pathogen"]["种"]["拉丁名"])
            self.assertEqual(["Journal Article"], record["source"]["article_type"])
            self.assertEqual("42", record["source"]["pmid"])
            self.assertEqual("species_direct", record["scope"])
            self.assertEqual("10.1/example", record["source"]["doi"])

    def test_refuses_quotes_that_cannot_be_located(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "article.md"
            path.write_text("Only source text.", encoding="utf-8")
            article = EXTRACTOR.LiteratureArticle(path, "article.md", "Only source text.", {}, "PubMed", "Title")
            structured = EXTRACTOR.normalize_model_result({"dimensions": {"致病性": [{"fact": "unsupported", "quote": "not in source"}]}})
            with self.assertRaisesRegex(ValueError, "quote is not locatable"):
                EXTRACTOR.build_article_record(article, structured, root)

    def test_requires_quote_and_keeps_per_fact_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            article = EXTRACTOR.LiteratureArticle(root / "a.md", "a.md", "Genus background.", {}, "PubMed", "Title")
            bad = EXTRACTOR.normalize_model_result({"dimensions": {"致病性": [{"fact": "unsupported"}]}})
            with self.assertRaisesRegex(ValueError, "nonempty original quote"):
                EXTRACTOR.build_article_record(article, bad, root)
            value = EXTRACTOR.normalize_model_result({"scope": "unknown", "dimensions": {"致病性": [
                {"fact": "Background", "quote": "Genus background.", "scope": "background_group"}]}})
            record = EXTRACTOR.build_article_record(article, value, root)
            self.assertEqual("background_group", record["evidence"][0]["scope"])
            self.assertEqual(0, record["evidence"][0]["source_start"])

    def test_default_run_processes_all_markdown_and_resumes_completed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "corpus"
            output = Path(temp) / "output"
            root.mkdir()
            (root / "one.md").write_text("# One\n\nA passage.", encoding="utf-8")
            (root / "two.MD").write_text("# Two\n\nAnother passage.", encoding="utf-8")
            args = EXTRACTOR.build_parser().parse_args(["--literature-root", str(root), "--output-dir", str(output)])
            response = {"scope": "unknown", "dimensions": {}}
            with patch.object(EXTRACTOR, "_request_json", return_value=EXTRACTOR.normalize_model_result(response)) as request:
                self.assertEqual(0, EXTRACTOR.run(args))
                self.assertEqual(2, request.call_count)
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(2, summary["completed_this_run"])
            self.assertEqual(2, len(list((output / "articles").rglob("*.json"))))
            with patch.object(EXTRACTOR, "_request_json") as request:
                self.assertEqual(0, EXTRACTOR.run(args))
                request.assert_not_called()
            self.assertEqual(2, json.loads((output / "summary.json").read_text(encoding="utf-8"))["skipped"])

    def test_failures_continue_and_retry_without_false_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "corpus"
            output = Path(temp) / "output"
            root.mkdir()
            (root / "one.md").write_text("A passage.", encoding="utf-8")
            (root / "two.md").write_text("Another passage.", encoding="utf-8")
            args = EXTRACTOR.build_parser().parse_args(["--literature-root", str(root), "--output-dir", str(output)])
            valid = EXTRACTOR.normalize_model_result({"dimensions": {}})
            with patch.object(EXTRACTOR, "_request_json", side_effect=[ValueError("bad JSON"), ValueError("bad JSON"), valid]):
                self.assertEqual(1, EXTRACTOR.run(args))
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(1, summary["failed"])
            self.assertFalse(summary["all_done"])
            with patch.object(EXTRACTOR, "_request_json", return_value=valid) as request:
                self.assertEqual(0, EXTRACTOR.run(args))
                self.assertEqual(1, request.call_count)


if __name__ == "__main__":
    unittest.main()
