import unittest
from unittest.mock import Mock, patch

from ingestion.websearch import search_web


class WebSearchTests(unittest.TestCase):
    @patch("ingestion.websearch.setting")
    @patch("ingestion.websearch.requests.get")
    def test_brave_results_are_normalized(self, request_get, setting) -> None:
        setting.side_effect = lambda name: "secret" if name == "BRAVE_SEARCH_API_KEY" else ""
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "web": {
                "results": [
                    {
                        "url": "https://example.edu/lab",
                        "title": "Example Lab",
                        "description": "We are recruiting PhD students.",
                    }
                ]
            }
        }
        request_get.return_value = response

        self.assertEqual(
            search_web("example professor hiring", 3),
            [
                {
                    "href": "https://example.edu/lab",
                    "title": "Example Lab",
                    "body": "We are recruiting PhD students.",
                }
            ],
        )
        self.assertEqual(
            request_get.call_args.kwargs["headers"]["X-Subscription-Token"],
            "secret",
        )

    @patch("ingestion.websearch._fallback_search")
    @patch("ingestion.websearch.setting", return_value="")
    def test_local_mode_uses_fallback(self, _setting, fallback) -> None:
        fallback.return_value = [{"href": "https://example.edu"}]
        self.assertEqual(search_web("robotics"), [{"href": "https://example.edu"}])
        fallback.assert_called_once_with("robotics", 3)


if __name__ == "__main__":
    unittest.main()
