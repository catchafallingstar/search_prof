import unittest

from ingestion.homepagefinder import is_public_http_url


class PublicUrlTests(unittest.TestCase):
    def test_accepts_public_https_url(self) -> None:
        self.assertTrue(is_public_http_url("https://www.example.edu/lab"))

    def test_rejects_private_and_local_urls(self) -> None:
        self.assertFalse(is_public_http_url("http://127.0.0.1/admin"))
        self.assertFalse(is_public_http_url("http://10.0.0.5/private"))
        self.assertFalse(is_public_http_url("http://localhost:8080"))

    def test_rejects_non_homepage_sources(self) -> None:
        self.assertFalse(is_public_http_url("https://scholar.google.com/citations?id=123"))
        self.assertFalse(
            is_public_http_url("https://papers.ssrn.com/sol3/cf_dev/AbsByAuth.cfm?per_id=1")
        )
        self.assertFalse(is_public_http_url("https://example.edu/cv.pdf"))


if __name__ == "__main__":
    unittest.main()
