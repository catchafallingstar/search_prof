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
            "/5_Radar_control",
            "/6_Data_and_policies",
        )
        python_files = [PROJECT_DIR / "app.py", PROJECT_DIR / "ui.py"]
        python_files.extend((PROJECT_DIR / "pages").glob("*.py"))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in python_files)
        for route in numeric_routes:
            self.assertNotIn(f'"{route}"', combined)
            self.assertNotIn(f"'{route}'", combined)

    def test_internal_navigation_does_not_use_raw_url_links(self) -> None:
        python_files = [PROJECT_DIR / "app.py", PROJECT_DIR / "ui.py"]
        python_files.extend((PROJECT_DIR / "pages").glob("*.py"))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in python_files)
        for route in (
            "/Post_an_opening",
            "/Verification",
            "/Admin_accounts",
            "/Radar_control",
        ):
            self.assertNotIn(f'"{route}"', combined)
            self.assertNotIn(f"'{route}'", combined)

    def test_custom_navigation_uses_streamlit_page_switching(self) -> None:
        source = (PROJECT_DIR / "ui.py").read_text(encoding="utf-8")
        self.assertIn('with st.container(key="sr_nav"):', source)
        self.assertEqual(source.count("st.switch_page("), 5)
        self.assertIn('st.switch_page("pages/6_Data_and_policies.py")', source)
        self.assertIn('href="/Admin_review"', source)
        self.assertNotIn('target="_self"', source)
        self.assertNotIn('target="_blank"', source)

    def test_unused_sidebar_control_is_hidden(self) -> None:
        source = (PROJECT_DIR / "ui.py").read_text(encoding="utf-8")
        self.assertIn(
            '[data-testid="stExpandSidebarButton"] { display: none !important; }',
            source,
        )

    def test_native_sidebar_navigation_is_disabled(self) -> None:
        config = (PROJECT_DIR / ".streamlit" / "config.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn("showSidebarNavigation = false", config)


if __name__ == "__main__":
    unittest.main()
