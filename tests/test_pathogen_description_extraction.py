"""Protect source attribution and isolation in Description-only extraction."""
import importlib.util
import json
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "scripts/data/extract_pathogen_descriptions_qwen.py"
SPEC = importlib.util.spec_from_file_location("description_extractor", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DescriptionExtractionTests(unittest.TestCase):
    def setUp(self):
        self.row = {"NameID": "id|test|BAC|S_1|", "类别": "细菌", "Name": {
            "种-拉丁名": "Example_species", "种-中文名": "示例菌"},
            "Description": "该菌不运动，常见于土壤；有1例动物感染报道。"}

    def test_untraceable_and_cross_record_quotations_are_rejected(self):
        other = {**self.row, "NameID": "other", "Description": "该菌为球菌。"}
        value = {"items": [{"i": 0, "d": [["该菌为球菌。", "该菌为球菌"]]}, {"i": 1}]}
        with self.assertRaisesRegex(ValueError, "quotation not in Description"):
            MODULE.validate_response([self.row, other], value)

    def test_partial_batch_and_invented_statistics_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "not all input"):
            MODULE.validate_response([self.row], {"items": []})
        with self.assertRaisesRegex(ValueError, "number absent"):
            MODULE.validate_response([self.row], {"items": [{"i": 0, "p": [["有10例动物感染报道", "有1例动物感染报道"]]}]})

    def test_delivery_retains_attribution_and_omits_raw_description(self):
        facts = MODULE.validate_response([self.row], {"items": [{"i": 0,
            "d": [["不运动，常见于土壤", "该菌不运动，常见于土壤"]],
            "p": [["有1例动物感染报道", "有1例动物感染报道"]]}]})[0]
        record = MODULE.make_record(self.row, facts)
        text = MODULE.render_records([record])
        self.assertNotIn(self.row["Description"], text)
        self.assertNotIn('"Description":', text)
        self.assertNotIn("原文依据", text)
        self.assertIsNone(record["五维知识"]["耐药性"]["来源"])
        self.assertIn(self.row["NameID"], record["五维知识"]["致病性"]["来源"])
        self.assertIn('"种": {"中文名": "示例菌", "拉丁名": "Example species"}', text)
        self.assertEqual(len(json.loads(text)["病原列表"]), 1)


if __name__ == "__main__":
    unittest.main()
