import unittest
from unittest.mock import Mock, patch

from ingestion.websearch import (
    SearchProviderUnavailable,
    SearchUnavailable,
    _fallback_search,
    _merge_results,
    _results_match_query_anchor,
    _searxng_search,
    search_web,
)


class WebSearchTests(unittest.TestCase):
    @patch("ingestion.websearch.setting")
    @patch("ingestion.websearch.requests.get")
    @patch("ingestion.websearch._record_provider_success")
    @patch("ingestion.websearch._persistent_block_remaining", return_value=0)
    def test_brave_results_are_normalized(
        self, _remaining, _record_success, request_get, setting
    ) -> None:
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

    @patch("ingestion.websearch.DDGS")
    def test_ddgs_zero_results_do_not_block_the_provider(self, ddgs) -> None:
        ddgs.return_value.text.side_effect = RuntimeError("No results found.")
        self.assertEqual(_fallback_search('"Rare Name" "University"', 3), [])

    @patch("ingestion.websearch._fallback_search")
    @patch("ingestion.websearch.setting", return_value="")
    @patch("ingestion.websearch._record_provider_success")
    @patch("ingestion.websearch._persistent_block_remaining", return_value=0)
    def test_local_mode_uses_fallback(
        self, _remaining, _record_success, _setting, fallback
    ) -> None:
        fallback.return_value = [{"href": "https://example.edu"}]
        self.assertEqual(
            search_web("robotics"),
            [{"href": "https://example.edu", "title": "", "body": ""}],
        )
        fallback.assert_called_once_with("robotics", 3)

    @patch("ingestion.websearch._write_cache")
    @patch("ingestion.websearch._read_cache", return_value=None)
    @patch("ingestion.websearch._run_provider")
    @patch("ingestion.websearch.setting")
    def test_balanced_mode_falls_back_after_empty_result(
        self, setting, run_provider, _read_cache, _write_cache
    ) -> None:
        values = {
            "SEARCH_PROVIDERS": "searxng,ddgs",
            "SEARXNG_URL": "http://127.0.0.1:8080",
            "SEARCH_PROVIDER_STRATEGY": "balanced",
        }
        setting.side_effect = lambda name: values.get(name, "")
        run_provider.side_effect = [[], [{"href": "https://example.edu/faculty"}]]

        result = search_web("distinctive identity query", 3)

        self.assertEqual(result[0]["href"], "https://example.edu/faculty")
        self.assertEqual(run_provider.call_count, 2)

    @patch("ingestion.websearch._write_cache")
    @patch("ingestion.websearch._read_cache", return_value=None)
    @patch("ingestion.websearch._run_provider")
    @patch("ingestion.websearch.setting")
    def test_parallel_mode_combines_and_deduplicates_results(
        self, setting, run_provider, _read_cache, _write_cache
    ) -> None:
        values = {
            "SEARCH_PROVIDERS": "searxng,ddgs",
            "SEARXNG_URL": "http://127.0.0.1:8080",
            "SEARCH_PROVIDER_STRATEGY": "parallel",
        }
        setting.side_effect = lambda name: values.get(name, "")

        def results(provider, _query, _maximum):
            return [
                {"href": "https://example.edu/shared", "title": provider, "body": ""},
                {
                    "href": f"https://example.edu/{provider}",
                    "title": provider,
                    "body": "",
                },
            ]

        run_provider.side_effect = results
        result = search_web("parallel identity query", 3)

        self.assertEqual(len(result), 3)
        self.assertEqual(len({item["href"] for item in result}), 3)
        self.assertEqual(run_provider.call_count, 2)

    def test_result_merge_uses_one_copy_of_each_url(self) -> None:
        result = _merge_results(
            [
                [{"href": "https://example.edu/a", "title": "A", "body": ""}],
                [
                    {
                        "href": "https://example.edu/a",
                        "title": "Again",
                        "body": "",
                    },
                    {"href": "https://example.edu/b", "title": "B", "body": ""},
                ],
            ],
            10,
        )
        self.assertEqual(
            [item["href"] for item in result],
            ["https://example.edu/a", "https://example.edu/b"],
        )

    def test_irrelevant_results_do_not_match_quoted_identity(self) -> None:
        self.assertFalse(
            _results_match_query_anchor(
                '"Xiapu Luo" "Large Language Models for Software Engineering"',
                [{
                    "href": "https://roofingcalculator.example/",
                    "title": "Roofing Calculator",
                    "body": "Estimate roof materials",
                }],
            )
        )

    @patch("ingestion.websearch._write_cache")
    @patch("ingestion.websearch._read_cache", return_value=None)
    @patch("ingestion.websearch._run_provider")
    @patch("ingestion.websearch.setting")
    def test_fallback_skips_provider_with_poisoned_identity_results(
        self, setting, run_provider, _read_cache, _write_cache
    ) -> None:
        values = {
            "SEARCH_PROVIDERS": "searxng,ddgs",
            "SEARXNG_URL": "http://127.0.0.1:8080",
            "SEARCH_PROVIDER_STRATEGY": "fallback",
        }
        setting.side_effect = lambda name: values.get(name, "")
        run_provider.side_effect = [
            [{
                "href": "https://roofing.example/",
                "title": "Roofing Calculator",
                "body": "Estimate materials",
            }],
            [{
                "href": "https://www.polyu.edu.hk/comp/people/academic-staff/dr-xiapu-luo/",
                "title": "Xiapu Luo | PolyU",
                "body": "Xiapu Luo Professor",
            }],
        ]

        result = search_web('"Xiapu Luo" faculty professor', 3)

        self.assertEqual(result[0]["title"], "Xiapu Luo | PolyU")
        self.assertEqual(run_provider.call_count, 2)

    @patch("ingestion.websearch.requests.get")
    def test_searxng_zero_with_unresponsive_engines_is_an_outage(
        self, request_get
    ) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [],
            "unresponsive_engines": [
                ["duckduckgo", "timeout"],
                ["startpage", "CAPTCHA"],
            ],
        }
        request_get.return_value = response

        with self.assertRaisesRegex(RuntimeError, "upstream engines"):
            _searxng_search("faculty query", 3, "http://127.0.0.1:8080")

    @patch("ingestion.websearch._write_cache")
    @patch("ingestion.websearch._read_cache", return_value=None)
    @patch("ingestion.websearch._run_provider")
    @patch("ingestion.websearch.setting")
    def test_all_provider_failures_raise_search_unavailable(
        self, setting, run_provider, _read_cache, _write_cache
    ) -> None:
        values = {
            "SEARCH_PROVIDERS": "searxng,ddgs",
            "SEARXNG_URL": "http://127.0.0.1:8080",
            "SEARCH_PROVIDER_STRATEGY": "balanced",
        }
        setting.side_effect = lambda name: values.get(name, "")
        run_provider.side_effect = SearchProviderUnavailable(
            "provider blocked", retry_after_seconds=900
        )

        with self.assertRaises(SearchUnavailable) as context:
            search_web("unavailable identity query", 3)

        self.assertEqual(context.exception.retry_after_seconds, 900)

    @patch("ingestion.websearch._write_cache")
    @patch("ingestion.websearch._read_cache", return_value=None)
    @patch("ingestion.websearch._run_provider")
    @patch("ingestion.websearch.setting")
    def test_outage_retries_when_the_earliest_provider_can_recover(
        self, setting, run_provider, _read_cache, _write_cache
    ) -> None:
        values = {
            "SEARCH_PROVIDERS": "searxng,ddgs",
            "SEARXNG_URL": "http://127.0.0.1:8080",
            "SEARCH_PROVIDER_STRATEGY": "fallback",
        }
        setting.side_effect = lambda name: values.get(name, "")
        run_provider.side_effect = [
            SearchProviderUnavailable("searxng blocked", 60),
            SearchProviderUnavailable("ddgs blocked", 900),
        ]

        with self.assertRaises(SearchUnavailable) as context:
            search_web("unavailable identity query", 3)

        self.assertEqual(context.exception.retry_after_seconds, 60)


if __name__ == "__main__":
    unittest.main()
