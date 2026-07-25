import unittest
import inspect

# Import your worker files
from ingestion import fetch_prof
from ingestion import check_grants
from ingestion import parse_hiring_signals

class TestFunctionSignatures(unittest.TestCase):
    
    def assert_no_session_args(self, func):
        """Helper to scan a function's arguments for forbidden names."""
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        
        self.assertNotIn(
            "session_id", params, 
            f"❌ ERROR: '{func.__name__}' in {func.__module__} still requires 'session_id'!"
        )
        self.assertNotIn(
            "lang_code", params, 
            f"❌ ERROR: '{func.__name__}' in {func.__module__} still requires 'lang_code'!"
        )

    def test_fetch_prof_signatures(self):
        self.assert_no_session_args(fetch_prof.fetch_professors_by_keywords)

    def test_check_grants_signatures(self):
        self.assert_no_session_args(check_grants.check_and_save_nsf_grants)
        self.assert_no_session_args(check_grants.get_funding_hash)

    def test_parse_hiring_signals_signatures(self):
        self.assert_no_session_args(parse_hiring_signals.scan_hiring_signals)
        self.assert_no_session_args(parse_hiring_signals.process_single_professor)
        self.assert_no_session_args(parse_hiring_signals.save_signal_to_db)
        
    def test_worker_wrapper_is_correct(self):
        """worker_wrapper MUST still have session_id and lang_code to bridge threads."""
        sig = inspect.signature(parse_hiring_signals.worker_wrapper)
        params = list(sig.parameters.keys())
        
        self.assertIn("session_id", params, "🚨 worker_wrapper MUST retain session_id!")
        self.assertIn("lang_code", params, "🚨 worker_wrapper MUST retain lang_code!")

if __name__ == "__main__":
    unittest.main(verbosity=2)