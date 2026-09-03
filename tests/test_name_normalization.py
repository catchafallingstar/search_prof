import unittest

from ingestion import hiring_discovery, socialradar, verify_faculty
from ingestion.name_normalization import fold_name, name_tokens, search_name_aliases


class NameNormalizationTests(unittest.TestCase):
    def test_turkish_name_matches_ascii_faculty_profile(self):
        self.assertEqual(fold_name("Şafak Kayıkçı"), "Safak Kayikci")
        self.assertTrue(verify_faculty._identity_matches(
            "Şafak Kayıkçı",
            "Safak Kayikci — Assistant Professor of Teaching, Florida Atlantic University",
        ))
        self.assertTrue(verify_faculty._same_normalized_name(
            "Şafak Kayıkçı", "Safak Kayikci"
        ))

    def test_original_and_folded_search_aliases_are_preserved(self):
        self.assertEqual(
            search_name_aliases("Şafak Kayıkçı"),
            ["Şafak Kayıkçı", "Safak Kayikci"],
        )

    def test_common_non_decomposing_latin_letters(self):
        self.assertEqual(
            fold_name("Łukasz Søndergaard Đorđević"),
            "Lukasz Sondergaard Dordevic",
        )
        self.assertEqual(
            fold_name("François L'Œuf Straße"),
            "Francois LOEuf Strasse",
        )

    def test_non_latin_names_do_not_disappear(self):
        self.assertEqual(name_tokens("张伟"), ["张伟"])
        self.assertEqual(name_tokens("Ольга Иванова"), ["ольга", "иванова"])

    def test_other_identity_paths_share_normalization(self):
        self.assertTrue(socialradar._identity_matches(
            "Şafak Kayıkçı", "Safak Kayikci", "Florida Atlantic University"
        ))
        self.assertTrue(hiring_discovery._result_names_candidate(
            "Şafak Kayıkçı",
            {"title": "Safak Kayikci | Faculty", "href": "https://fau.edu/kayikci"},
        ))


if __name__ == "__main__":
    unittest.main()
