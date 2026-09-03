import io
import unittest

from scripts.run_radar import _Tee, _default_log_path


class RunRadarLoggingTests(unittest.TestCase):
    def test_tee_writes_console_and_report(self):
        console = io.StringIO()
        report = io.StringIO()
        tee = _Tee(console, report)
        tee.write("[ 15%] Finding recent relevant research\n")
        self.assertEqual(console.getvalue(), report.getvalue())

    def test_default_log_path_is_timestamped_and_slugged(self):
        path = _default_log_path("Materials Science")
        self.assertEqual(path.parent.name, "reports")
        self.assertTrue(path.name.endswith("-materials-science.md"))


if __name__ == "__main__":
    unittest.main()
