"""Start each Streamlit page in Streamlit's headless test runner."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


PROJECT_DIR = Path(__file__).resolve().parents[1]
PAGES = [
    PROJECT_DIR / "app.py",
    PROJECT_DIR / "pages" / "1_Post_an_opening.py",
    PROJECT_DIR / "pages" / "2_Verification.py",
    PROJECT_DIR / "pages" / "3_Admin_review.py",
    PROJECT_DIR / "pages" / "4_Admin_accounts.py",
    PROJECT_DIR / "pages" / "5_Radar_control.py",
    PROJECT_DIR / "pages" / "6_Data_and_policies.py",
]


def main() -> None:
    for page in PAGES:
        app = AppTest.from_file(str(page), default_timeout=15)
        app.run()
        if app.exception:
            messages = "; ".join(str(error.value) for error in app.exception)
            raise SystemExit(f"Streamlit smoke test failed for {page.name}: {messages}")
        print(f"Streamlit smoke test passed: {page.name}")


if __name__ == "__main__":
    main()
