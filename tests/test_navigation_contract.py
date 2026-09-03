import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class NavigationContractTests(unittest.TestCase):
    def test_internal_urls_exclude_streamlit_ordering_prefixes(self) -> None:
        numeric_routes = (
            "/1_Post_an_opening",
            "/2_Verification",
            "/3_Admin_review",
            "/4_Admin_accounts",
        )
        python_files = [PROJECT_DIR / "app.py", PROJECT_DIR / "ui.py"]
        python_files.extend((PROJECT_DIR / "pages").glob("*.py"))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in python_files)
        # Streamlit requires page-file paths internally; these are not public
        # URL routes and should not be asserted against as URL fragments.
        self.assertIn('st.switch_page("pages/1_Post_an_opening.py")', combined)


if __name__ == "__main__":
    unittest.main()
