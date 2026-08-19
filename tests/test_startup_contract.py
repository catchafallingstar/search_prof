from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupContractTests(unittest.TestCase):
    def test_local_launcher_uses_project_interpreter(self) -> None:
        launcher = (ROOT / "scripts" / "start_local.sh").read_text(encoding="utf-8")

        self.assertIn(".venv/bin/python -m scripts.run_worker", launcher)
        self.assertIn(".venv/bin/python -m streamlit run app.py", launcher)
        self.assertIn("trap cleanup EXIT INT TERM", launcher)
        self.assertNotIn("exec streamlit run app.py", launcher)


if __name__ == "__main__":
    unittest.main()
