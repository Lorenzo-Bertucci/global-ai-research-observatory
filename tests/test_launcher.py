import os
import unittest
from unittest import mock
from run_dashboard import PreflightError, database_url, main, preflight


class LauncherTests(unittest.TestCase):
    def test_default_database_requires_no_environment_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(database_url(), "postgresql:///global_ai_observatory")

    def test_dependency_failure_is_actionable(self):
        with mock.patch(
            "run_dashboard.importlib.import_module", side_effect=ImportError
        ):
            with self.assertRaisesRegex(PreflightError, "requirements.txt"):
                preflight("irrelevant")

    def test_check_only_does_not_launch(self):
        with (
            mock.patch("sys.argv", ["run_dashboard.py", "--check-only"]),
            mock.patch(
                "run_dashboard.preflight",
                return_value={"publications": 1, "years": list(range(2018, 2026))},
            ),
            mock.patch("run_dashboard.subprocess.call") as launch,
        ):
            self.assertEqual(main(), 0)
            launch.assert_not_called()

    def test_failed_preflight_does_not_launch(self):
        with (
            mock.patch("sys.argv", ["run_dashboard.py"]),
            mock.patch(
                "run_dashboard.preflight",
                side_effect=PreflightError("fixture failure"),
            ),
            mock.patch("run_dashboard.subprocess.call") as launch,
        ):
            self.assertEqual(main(), 1)
            launch.assert_not_called()
