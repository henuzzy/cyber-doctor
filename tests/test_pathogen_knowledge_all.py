"""Checks for the concise delivery, scope retention and exact identity reuse."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/data/build_pathogen_knowledge_all.py"
SPEC = importlib.util.spec_from_file_location("all_collector", SCRIPT)
COLLECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECTOR)


class ConciseCollectionTests(unittest.TestCase):
    def row(self, name_id="id|1|BAC|S_1|", chinese="例菌"):
        return {"NameID": name_id, "类别": "细菌", "Name": {
            "种-中文名": chinese, "种-拉丁名": "Example_species",
            "属-拉丁名": "Example", "属-中文名": "例属"}}

    def test_delivery_has_no_metadata_and_one_line_per_rank(self):
        record, _ = COLLECTOR.convert_review(self.row(), {}, Path("."))
        text = COLLECTOR.delivery_text([record])
        self.assertEqual(list(json.loads(text)), ["病原列表"])
        self.assertIn('"种": {"中文名": "例菌", "拉丁名": "Example species"}', text)
        self.assertEqual(sum('"中文名"' in line for line in text.splitlines()), 6)
        self.assertEqual(sum(v["来源"] is None for v in record["五维知识"].values()), 5)

    def test_reuse_requires_matching_type_and_names(self):
        original = self.row()
        same = self.row("id|2|BAC|S_2|")
        typed = self.row("id|2|BAC|TP_2|")
        other_name = self.row(chinese="不同型别")
        self.assertEqual(COLLECTOR.identity_key(original), COLLECTOR.identity_key(same))
        self.assertNotEqual(COLLECTOR.identity_key(original), COLLECTOR.identity_key(typed))
        self.assertNotEqual(COLLECTOR.identity_key(original), COLLECTOR.identity_key(other_name))

    def test_missing_species_is_not_deduplicated(self):
        row = self.row()
        row["Name"]["种-拉丁名"] = ""
        self.assertIsNone(COLLECTOR.identity_key(row))

    def test_indirect_and_animal_scope_stays_in_summary(self):
        fact = {"内容": "观察到免疫相互作用。", "证据层级": "genus_context",
                "研究类型": "animal", "来源": {"url": "https://pubmed.ncbi.nlm.nih.gov/1/"}}
        result = COLLECTOR.summarize_fact(fact, "毒力因子", {})
        self.assertIn("属级或同群体参考", result["概述"])
        self.assertIn("动物研究", result["概述"])

    def test_unlocatable_quote_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(json.dumps({"text": "The original passage."}), encoding="utf-8")
            fact = {"内容": "不受支持的内容", "原文证据": "Not present.",
                    "证据层级": "species_direct", "来源": {
                        "source_path": str(path), "url": "https://pubmed.ncbi.nlm.nih.gov/1/"}}
            reviewed = {"五维": {"致病性": {"事实": [fact]}}}
            record, evidence = COLLECTOR.convert_review(self.row(), reviewed, Path(directory))
            self.assertIsNone(record["五维知识"]["致病性"]["来源"])
            self.assertFalse(evidence)

    def test_genus_fallback_taxid_is_not_species_taxid(self):
        reviewed = {"检索记录": {"taxonomy": {"tax_id": "123", "rank": "genus"}}}
        record, _ = COLLECTOR.convert_review(self.row(), reviewed, Path("."))
        self.assertIsNone(record["病原信息"]["TaxID"])


if __name__ == "__main__":
    unittest.main()
