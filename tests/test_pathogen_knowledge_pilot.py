"""Regression checks for source attribution and the pilot's search fallbacks."""
import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/data/build_pathogen_knowledge_first50.py"
if not MODULE_PATH.exists():
    MODULE_PATH = Path(__file__).with_name("build_pathogen_knowledge_first50.py")
spec = importlib.util.spec_from_file_location("pathogen_pilot", MODULE_PATH)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
audit_path=MODULE_PATH.with_name('pathogen_pilot_audit.py')
audit_spec=importlib.util.spec_from_file_location('pathogen_audit',audit_path)
audit=importlib.util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(audit)


class PilotChecks(unittest.TestCase):
    def test_brackets_underscores_and_spaces_are_preserved_as_search_options(self):
        self.assertEqual(pilot.name_variants("[Clostridium]_scindens"),
                         ["[Clostridium]_scindens", "Clostridium scindens", "Clostridium_scindens"])

    def test_missing_species_keeps_genus_available(self):
        species, genus, species_names, genus_names = pilot.species_and_genus(
            {"Name": {"种-拉丁名": "", "属-拉丁名": "Acanthamoeba"}})
        self.assertEqual(species, "")
        self.assertEqual(species_names, [])
        self.assertEqual(genus_names, ["Acanthamoeba"])

    def test_ambiguous_taxonomy_is_not_silently_first_hit(self):
        class HTTP:
            def get(self, *args):
                return '{"esearchresult":{"idlist":["1","2"]}}'
        self.assertEqual(pilot.taxonomy_search(HTTP(), ["Ambiguous name"], "test")[0], "")

    def test_fulltext_pmid_mapping_and_reference_exclusion(self):
        xml = '<pmc-articleset><article><front><article-meta><article-id pub-id-type="pmid">123</article-id><title-group><article-title>Title</article-title></title-group></article-meta></front><body><p>Body evidence.</p></body><back><ref-list><ref><p>Reference-only mention.</p></ref></ref-list></back></article></pmc-articleset>'
        result = pilot.parse_pmc_xml(xml)
        self.assertIn("Body evidence.", result["123"])
        self.assertNotIn("Reference-only", result["123"])

    def test_untraceable_quote_is_quarantined(self):
        profile = {"五维": {"致病性": {"事实": [
            {"内容": "supported", "证据ID": "S1", "原文证据": "Exact quote."},
            {"内容": "unsupported", "证据ID": "S1", "原文证据": "Invented quote."}]}}}
        pilot.enrich_profile(profile, [{"source_id": "S1", "text": "Exact quote."}])
        self.assertEqual(len(profile["五维"]["致病性"]["事实"]), 1)
        self.assertEqual(len(profile["待核查事实"]), 1)

    def test_one_shared_limiter_and_cache_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "raw").mkdir()
            http = pilot.ThrottledHTTP(root, 2.5)
            class Response:
                status_code = 200
                content = b'{"ok":true}'
                text = '{"ok":true}'
                def raise_for_status(self):
                    pass
            calls = []
            http.session.get = lambda *a, **kw: (calls.append(1) or Response())
            first = http.get("https://example.invalid", {"a": "1"}, "first")
            second = http.get("https://example.invalid", {"a": "1"}, "cached")
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)
            self.assertEqual(http.pause, 2.5)

    def test_case_treatment_is_not_lost_when_only_intro_names_species(self):
        intro = ('Target species is described here. ' * 55)
        treatment = 'The patient received antibiotics and recovered. Follow-up found no recurrence.'
        source = intro + '\n\n' + ('Environmental background. ' * 60) + '\n\n' + treatment
        selected = pilot.select_text(source, 'Target species', 'Target', budget=800)
        self.assertIn(treatment, selected)
        for passage in selected.split('\n\n'):
            self.assertIn(passage, source)

    def test_source_audit_preserves_description_when_same_paper_has_treatment(self):
        quote='Murine typhus and flea-borne spotted fever are undifferentiated febrile illnesses caused by Rickettsia typhi and Rickettsia felis, respectively.'
        data={'病原名称':{'种-拉丁名':'Rickettsia_felis'},'五维':{d:{'事实':[]} for d in pilot.DIMENSIONS}}
        data['五维']['物种描述']['事实']=[{'内容':'original','原文证据':quote,'来源':{'pmid':'33574726'}}]
        audit.apply_audit(data,Path('.'))
        first=data['五维']['物种描述']['事实'][0]['内容']
        audit.apply_audit(data,Path('.'))
        self.assertEqual(first,data['五维']['物种描述']['事实'][0]['内容'])
        self.assertNotIn('doxycycline',first)
        self.assertEqual(data['五维']['物种描述']['事实'][0]['证据层级'],'species_direct')


if __name__ == "__main__":
    unittest.main()
