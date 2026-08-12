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
        for route in numeric_routes:
            self.assertNotIn(route, combined)

    def test_custom_navigation_stays_in_the_current_tab(self) -> None:
        source = (PROJECT_DIR / "ui.py").read_text(encoding="utf-8")
        self.assertIn('<nav class="sr-nav"', source)
        self.assertEqual(source.count('target="_self"'), 5)
        self.assertNotIn('target="_blank"', source)

    def test_brand_returns_home_and_unused_sidebar_control_is_hidden(self) -> None:
        source = (PROJECT_DIR / "ui.py").read_text(encoding="utf-8")
        self.assertIn(
            '<a class="sr-brand" href="/" target="_self" aria-label="ScholarRadar home">',
            source,
        )
        self.assertIn(
            '[data-testid="stExpandSidebarButton"] { display: none !important; }',
            source,
        )


if __name__ == "__main__":
    unittest.main()
