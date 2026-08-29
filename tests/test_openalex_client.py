import unittest
from unittest.mock import MagicMock, patch

from ingestion.openalex_client import OpenAlexUnavailable, openalex_get_json


class OpenAlexClientTests(unittest.TestCase):
    @patch("ingestion.openalex_client._record_failure")
    @patch("ingestion.openalex_client._reserve_shared_slot", return_value=0)
    @patch("ingestion.openalex_client.requests.get")
    def test_rate_limit_error_never_contains_api_credentials(
        self, request, _reserve, _record_failure
    ) -> None:
        response = MagicMock()
        response.status_code = 429
        response.headers = {}
        request.return_value = response
        secret = "do-not-log-this-key"
        with self.assertRaises(OpenAlexUnavailable) as captured:
            openalex_get_json(
                "https://api.openalex.org/works",
                params={"search": "robotics", "api_key": secret},
            )
        self.assertNotIn(secret, str(captured.exception))
        response.close.assert_called_once()

    @patch("ingestion.openalex_client._record_success")
    @patch("ingestion.openalex_client._reserve_shared_slot", return_value=0)
    @patch("ingestion.openalex_client.requests.get")
    def test_success_returns_json_payload(
        self, request, _reserve, record_success
    ) -> None:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"results": [{"id": "W1"}]}
        request.return_value = response
        result = openalex_get_json(
            "https://api.openalex.org/works",
            params={"search": "robotics"},
        )
        self.assertEqual(result["results"][0]["id"], "W1")
        record_success.assert_called_once()
        response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
