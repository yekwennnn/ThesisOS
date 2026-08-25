from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from thesisos.api import create_app
from thesisos.providers import (
    AppSettings,
    FinanceProviderError,
    ProviderConfigurationError,
    WindCliFinanceProvider,
)


FAKE_WIND_CLI = Path(__file__).parent / "fixtures" / "fake_wind_cli.py"


def provider(*, timeout: float = 1.0, output_limit: int = 64 * 1024) -> WindCliFinanceProvider:
    return WindCliFinanceProvider(
        (sys.executable, str(FAKE_WIND_CLI)),
        timeout_seconds=timeout,
        max_stdout_bytes=output_limit,
    )


class WindCliFinanceProviderTest(unittest.TestCase):
    def test_resolves_through_fixed_wind_route_and_returns_only_compact_fields(self) -> None:
        result = provider().resolve_instrument("9988.hk")
        self.assertEqual(
            result,
            {
                "symbol": "9988.HK",
                "name": "Example Holdings",
                "exchange": "XHKG",
                "market": "Main Board",
                "industry": "Internet Retail",
                "status": "Listed",
                "provider": "wind",
                "as_of": result["as_of"],
            },
        )
        parsed = datetime.fromisoformat(result["as_of"].replace("Z", "+00:00"))
        self.assertIsNotNone(parsed.utcoffset())
        self.assertNotIn("content", result)
        self.assertNotIn("cli_meta", result)

    def test_nonzero_exit_preserves_only_safe_error_code(self) -> None:
        with self.assertRaises(FinanceProviderError) as caught:
            provider().resolve_instrument("FAIL")
        message = str(caught.exception)
        self.assertEqual(message, "finance provider request failed (AUTH_ERROR)")
        self.assertNotIn("SUPER_SECRET", message)
        self.assertNotIn(str(FAKE_WIND_CLI), message)

    def test_timeout_and_stdout_limit_fail_closed(self) -> None:
        with self.assertRaisesRegex(FinanceProviderError, "timed out"):
            provider(timeout=0.05).resolve_instrument("SLOW")
        with self.assertRaisesRegex(FinanceProviderError, "byte limit"):
            provider(output_limit=512).resolve_instrument("LARGE")

    def test_malformed_and_ambiguous_results_fail_closed(self) -> None:
        with self.assertRaisesRegex(FinanceProviderError, "strict JSON"):
            provider().resolve_instrument("BADJSON")
        with self.assertRaisesRegex(FinanceProviderError, "unambiguous"):
            provider().resolve_instrument("AMB")

    def test_app_settings_reads_wind_configuration_from_environment(self) -> None:
        environment = {
            "THESISOS_FINANCE_PROVIDER": "wind-cli",
            "THESISOS_WIND_CLI_ARGV": '["node","/srv/wind/cli.mjs"]',
            "THESISOS_WIND_TIMEOUT_SECONDS": "17.5",
            "THESISOS_WIND_MAX_STDOUT_BYTES": "8192",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = AppSettings.from_env()
        self.assertEqual(settings.finance_provider, "wind-cli")
        self.assertEqual(settings.wind_cli_argv, ("node", "/srv/wind/cli.mjs"))
        self.assertEqual(settings.wind_timeout_seconds, 17.5)
        self.assertEqual(settings.wind_max_stdout_bytes, 8192)

    def test_app_settings_rejects_non_array_cli_argv(self) -> None:
        with patch.dict(
            os.environ,
            {"THESISOS_WIND_CLI_ARGV": '"node /srv/wind/cli.mjs"'},
            clear=True,
        ):
            with self.assertRaisesRegex(
                ProviderConfigurationError, "THESISOS_WIND_CLI_ARGV"
            ):
                AppSettings.from_env()

    def test_create_app_constructs_selected_wind_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = AppSettings(
                workspace=Path(tmp),
                model_identifier="unconfigured",
                model_adapter_argv=(),
                finance_provider="wind-cli",
                wind_cli_argv=(sys.executable, str(FAKE_WIND_CLI)),
                wind_timeout_seconds=1.0,
                wind_max_stdout_bytes=64 * 1024,
            )
            with TestClient(create_app(settings)) as client:
                health = client.get("/health")
                response = client.get(
                    "/v1/finance/instruments/resolve",
                    params={"symbol": "9988.hk"},
                )
            self.assertEqual(health.status_code, 200)
            self.assertTrue(health.json()["providers"]["finance"]["configured"])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["instrument"]["provider"], "wind")

    def test_selected_wind_provider_requires_cli_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = AppSettings(
                workspace=Path(tmp),
                model_identifier="unconfigured",
                model_adapter_argv=(),
                finance_provider="wind-cli",
            )
            with self.assertRaisesRegex(
                ProviderConfigurationError, "THESISOS_WIND_CLI_ARGV"
            ):
                create_app(settings)


if __name__ == "__main__":
    unittest.main()
