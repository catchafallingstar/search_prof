import unittest
from unittest.mock import patch

from ingestion.paper_affiliations import (
    _institution_matches,
    extract_paper_affiliation,
)


class PaperAffiliationTests(unittest.TestCase):
    def test_suny_abbreviation_matches_full_openalex_affiliation(self) -> None:
        self.assertTrue(_institution_matches(
            "SUNY New Paltz",
            "Department of Computer Science, State University of New York, "
            "New Paltz, NY, 12561, USA",
        ))

    @patch("ingestion.paper_affiliations._download_pdf")
    def test_openalex_affiliation_avoids_pdf_download(self, download) -> None:
        result = extract_paper_affiliation(
            {"name": "Keqin Li", "institution_name": "SUNY New Paltz"},
            {
                "openalex_id": "https://openalex.org/W4405040715",
                "raw_affiliation_text": (
                    "Department of Computer Science, State University of New York, "
                    "New Paltz, NY, 12561, USA"
                ),
            },
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["method"], "openalex_raw_affiliation")
        download.assert_not_called()

    @patch("ingestion.paper_affiliations._ocr_first_pages")
    @patch("ingestion.paper_affiliations._extract_first_pages", return_value="")
    @patch("ingestion.paper_affiliations._download_pdf")
    @patch("ingestion.paper_affiliations._resolve_open_pdf_url")
    def test_flat_pdf_uses_ocr_before_not_found(
        self, resolve, download, _extract, ocr
    ) -> None:
        resolve.return_value = "https://example.edu/paper.pdf"
        download.return_value = (b"%PDF test", "https://example.edu/paper.pdf")
        ocr.return_value = (
            "Keqin Li. Department of Computer Science, State University of "
            "New York, New Paltz, NY, USA."
        )
        result = extract_paper_affiliation(
            {"name": "Keqin Li", "institution_name": "SUNY New Paltz"},
            {"openalex_id": "https://openalex.org/W1", "raw_affiliation_text": ""},
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["method"], "open_pdf_ocr")


if __name__ == "__main__":
    unittest.main()
