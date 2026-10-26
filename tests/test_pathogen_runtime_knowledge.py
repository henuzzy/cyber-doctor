"""Runtime name matching and combined mNGS knowledge evidence tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cyber_doctor.model.rag.document import Document
from cyber_doctor.model.rag.five_dimension_retriever import FiveDimensionPathogenRetriever
from cyber_doctor.model.rag.structured_retriever import StructuredPathogenRetriever
from cyber_doctor.mngs.rag_judge import MNGSCase, build_judge_prompt, retrieve_mngs_evidence


class RuntimePathogenKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive_path = self.root / "pathogen_knowledge_all.json"
        profile = {
            "病原信息": {
                "NameID": "id|test|BAC|S_1|",
                "类别": "细菌",
                "名称与分类": {
                    "种": {"中文名": "示例菌", "拉丁名": "Example bacterium"},
                    "属": {"中文名": "示例属", "拉丁名": "Example"},
                    "科": {"中文名": "示例科", "拉丁名": "Exampleaceae"},
                    "目": {"中文名": "示例目", "拉丁名": "Exampleales"},
                    "纲": {"中文名": "示例纲", "拉丁名": "Exampleia"},
                    "门": {"中文名": "示例门", "拉丁名": "Exampleota"},
                },
                "别名": ["Alias bacterium"],
            },
            "五维知识": {
                dimension: {"概述": f"{dimension}摘要", "来源": ["PMID:123"]}
                for dimension in ("物种描述", "致病性", "毒力因子", "耐药性", "治疗与预后")
            },
        }
        self.archive_path.write_text(json.dumps({"病原列表": [profile]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_five_dimension_search_supports_species_genus_id_and_alias(self) -> None:
        retriever = FiveDimensionPathogenRetriever(self.archive_path)
        for query in ("示例菌", "Example bacterium", "Example_bacterium", "示例属", "Example", "id|test|BAC|S_1|", "Alias bacterium"):
            with self.subTest(query=query):
                self.assertTrue(retriever.search(query))
        self.assertTrue(retriever.is_exact_name("Example_bacterium"))
        self.assertFalse(retriever.is_exact_name("Example bacterium 是否有害"))
        document = retriever.retrieve(["Example_bacterium"])[0]
        self.assertIn("耐药性摘要", document.page_content)
        self.assertIn('["PMID:123"]', document.page_content)
        self.assertEqual("five_dimension", document.metadata["knowledge_kind"])

    def test_article_retriever_reads_new_and_legacy_json_and_names_scope(self) -> None:
        article_root = self.root / "articles"
        article_root.mkdir()
        new_record = {
            "pathogen": {"NameID": "id|test|BAC|S_1|", "种": {"拉丁名": "Example bacterium"}, "属": {"拉丁名": "Example"}, "别名": ["Alias bacterium"]},
            "source": {"type": "PubMed", "title": "Direct article", "pmid": "123", "doi": "10.1/example"},
            "scope": "species_direct",
            "evidence": [{"dimension": "致病性", "quote": "The exact passage."}],
        }
        (article_root / "new.json").write_text(json.dumps(new_record), encoding="utf-8")
        legacy = {"NameID": "legacy-id", "来源": "PubMed", "病原名称": {"种-中文名": "旧结构病原", "种-拉丁名": "Legacy species"}, "文章信息": {"标题": "Legacy article"}, "证据": []}
        (article_root / "legacy.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        retriever = StructuredPathogenRetriever(article_root)
        found = retriever.retrieve(["Example_bacterium", "Alias bacterium"])
        self.assertEqual(1, len(found))
        self.assertEqual("Direct article", found[0].metadata["title"])
        self.assertEqual("species_direct", found[0].metadata["evidence_scope"])
        self.assertIn('"pmid": "123"', found[0].page_content)
        self.assertEqual("Legacy article", retriever.retrieve(["旧结构病原"])[0].metadata["title"])

    def test_mngs_evidence_contains_both_archive_and_article_sources(self) -> None:
        case = MNGSCase(species_latin="Example_bacterium", species_chinese="示例菌", genus_latin="Example")
        article = Document("逐篇原文事实", {"source": "PubMed", "title": "Source article", "structured_record": {"scope": "species_direct", "病原名称": {"种-拉丁名": "Example bacterium"}, "文章信息": {"PMID": "123"}}})
        archive = Document("五维摘要及来源", {"source": "五维病原知识档案", "title": "示例菌", "knowledge_kind": "five_dimension", "support_scope": "目标物种档案", "five_dimension_record": {"病原信息": {"名称与分类": {"种": {"拉丁名": "Example bacterium"}}}}})
        with patch("cyber_doctor.model.rag.structured_retriever.retrieve", return_value=[article]), patch(
            "cyber_doctor.model.rag.five_dimension_retriever.retrieve", return_value=[archive]
        ), patch.dict("os.environ", {"MNGS_KNOWLEDGE_SOURCE": "structured"}):
            bundle = retrieve_mngs_evidence(case)
        self.assertEqual({"five_dimension", None}, {doc.metadata.get("knowledge_kind") for doc in bundle.docs})
        self.assertEqual("目标物种直接匹配", article.metadata["support_scope"])
        prompt = build_judge_prompt(case, bundle)
        self.assertIn("五维病原概要档案", prompt)
        self.assertIn("逐篇文章 JSON", prompt)
        self.assertIn("PMID 123", prompt)

    def test_exact_species_does_not_include_sibling_profiles(self) -> None:
        payload = json.loads(self.archive_path.read_text(encoding="utf-8"))
        import copy
        sibling = copy.deepcopy(payload["病原列表"][0])
        sibling["病原信息"]["NameID"] = "sibling"
        sibling["病原信息"]["名称与分类"]["种"] = {"中文名": "另一菌", "拉丁名": "Example other"}
        payload["病原列表"].append(sibling)
        self.archive_path.write_text(json.dumps(payload), encoding="utf-8")
        retriever = FiveDimensionPathogenRetriever(self.archive_path)
        self.assertEqual(1, len(retriever.search("Example bacterium 的五维档案")))
        self.assertEqual(2, len(retriever.search("Example")))
        self.assertEqual([], retriever.search("ExampleXYZ"))

    def test_legacy_name_match_does_not_imply_direct_evidence(self) -> None:
        case = MNGSCase(species_latin="Example bacterium")
        doc = Document("Background only", {"structured_record": {
            "病原名称": {"种-拉丁名": "Example bacterium"}}})
        with patch("cyber_doctor.model.rag.structured_retriever.retrieve", return_value=[doc]), patch(
            "cyber_doctor.model.rag.five_dimension_retriever.retrieve", return_value=[]):
            retrieve_mngs_evidence(case)
        self.assertNotEqual("目标物种直接匹配", doc.metadata["support_scope"])

    def test_broad_scope_and_both_sources_survive_prompt_formatting(self) -> None:
        case = MNGSCase(species_latin="Example bacterium")
        article = Document("具体原文", {"source": "PubMed", "structured_record": {
            "scope": "background_group", "病原名称": {"种-拉丁名": "Example bacterium"}}})
        archive = FiveDimensionPathogenRetriever(self.archive_path).retrieve(["Example bacterium"])
        with patch("cyber_doctor.model.rag.structured_retriever.retrieve", return_value=[article]), patch(
            "cyber_doctor.model.rag.five_dimension_retriever.retrieve", return_value=archive):
            bundle = retrieve_mngs_evidence(case)
        self.assertIn("治疗与预后摘要", bundle.context)
        self.assertIn("具体原文", bundle.context)
        self.assertIn("更大病原群背景证据", bundle.context)


if __name__ == "__main__":
    unittest.main()
