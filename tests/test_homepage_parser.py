import unittest
from unittest.mock import Mock, patch

import requests

from ingestion.parse_hiring_signals import (
    _fetch_with_safe_redirects,
    fetch_and_parse_homepage,
)


class HomepageParserTests(unittest.TestCase):
    @patch("ingestion.parse_hiring_signals.time.sleep")
    @patch("ingestion.parse_hiring_signals.is_public_http_url", return_value=True)
    @patch("ingestion.parse_hiring_signals._fetch_with_safe_redirects")
    def test_non_html_source_is_accessible_without_parser_shape_error(
        self, fetch, _public_url, _sleep
    ) -> None:
        response = Mock()
        response.headers = {"content-type": "application/pdf"}
        response.raise_for_status.return_value = None
        fetch.return_value = response
        from ingestion.parse_hiring_signals import _fetch_and_parse_homepage_status

        self.assertEqual(
            _fetch_and_parse_homepage_status("https://example.edu/cv.pdf"),
            ([], True),
        )

    @patch("ingestion.parse_hiring_signals.time.sleep")
    @patch("ingestion.parse_hiring_signals.is_public_http_url", return_value=True)
    @patch("ingestion.parse_hiring_signals._fetch_with_safe_redirects")
    def test_preserves_unpunctuated_html_block_boundaries(
        self,
        fetch: Mock,
        _safe_url: Mock,
        _sleep: Mock,
    ) -> None:
        response = Mock()
        response.headers = {"content-type": "text/html; charset=utf-8"}
        response.text = (
            "<html><body><p>We are recruiting PhD students for fall 2027</p>"
            "<p>Read about our research projects</p></body></html>"
        )
        fetch.return_value = response

        self.assertEqual(
            fetch_and_parse_homepage("https://example.edu/lab"),
            ["We are recruiting PhD students for fall 2027"],
        )

    @patch("ingestion.parse_hiring_signals.requests.get")
    @patch("ingestion.parse_hiring_signals.is_public_http_url")
    def test_revalidates_redirect_before_second_request(
        self,
        safe_url: Mock,
        get: Mock,
    ) -> None:
        safe_url.side_effect = [True, False]
        redirect = Mock()
        redirect.is_redirect = True
        redirect.is_permanent_redirect = False
        redirect.headers = {"location": "http://127.0.0.1/private"}
        get.return_value = redirect

        with self.assertRaises(requests.RequestException):
            _fetch_with_safe_redirects("https://example.edu/lab")

        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
