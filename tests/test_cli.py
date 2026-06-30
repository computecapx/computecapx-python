import contextlib
import io
import os
import tempfile
import types
import unittest
from unittest.mock import patch

from computecapx.client import ComputeCapClient
from computecapx.cli import cmd_status


class CliStatusTests(unittest.TestCase):
    def test_client_reads_api_key_from_dotenv_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            with open(env_path, "w", encoding="utf-8") as handle:
                handle.write("COMPUTECAPX_API_KEY=dotenv-key\nCOMPUTECAPX_PROJECT_ID=proj-123\n")

            previous_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                client = ComputeCapClient(api_key=None)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(client.api_key, "dotenv-key")

    def test_cmd_status_runs_without_name_error(self):
        args = types.SimpleNamespace(url=None)

        with patch("computecapx.cli.EnvironmentDetector.detect_environment", return_value={
            "provider": "local",
            "resource_id": "test-resource",
        }), patch("computecapx.cli.ComputeCapClient") as client_cls, patch("computecapx.cli.requests.get", return_value=types.SimpleNamespace(status_code=200)):
            client_cls.return_value = object()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cmd_status(args)

        result = output.getvalue()
        self.assertIn("ComputeCapX Diagnostic Report", result)
        self.assertIn("Connectivity: OPERATIONAL", result)


if __name__ == "__main__":
    unittest.main()
