"""Tests for the enhanced `dataforge validate` and new `dataforge doctor` commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from dataforge.cli import cli
from dataforge.validation.tfsec_runner import TfsecRunner, TfsecReport, TfsecFinding
from dataforge.validation.infracost_runner import InfracostRunner, InfracostReport, InfracostResourceCost


# ── TfsecRunner ──────────────────────────────────────────────────────────────

class TestTfsecRunner:

    def test_returns_not_installed_when_tfsec_missing(self):
        with patch("shutil.which", return_value=None):
            report = TfsecRunner().run(Path("."))
        assert not report.installed
        assert report.ok  # not installed → no failures

    def test_parses_critical_finding(self):
        payload = json.dumps({
            "results": [{
                "rule_id": "AVD-AZU-0001",
                "description": "Storage account not using CMK",
                "severity": "CRITICAL",
                "location": {"filename": "main.tf", "start_line": 10},
            }]
        })
        runner = TfsecRunner()
        report = runner._parse(payload)
        assert report.critical == 1
        assert not report.ok
        assert report.findings[0].rule_id == "AVD-AZU-0001"

    def test_parses_high_finding(self):
        payload = json.dumps({
            "results": [{
                "rule_id": "AVD-AZU-0002",
                "description": "Some high severity issue",
                "severity": "HIGH",
                "location": {"filename": "variables.tf", "start_line": 5},
            }]
        })
        report = TfsecRunner()._parse(payload)
        assert report.high == 1
        assert not report.ok

    def test_medium_and_low_do_not_fail(self):
        payload = json.dumps({
            "results": [
                {"rule_id": "AVD-AZU-0003", "description": "medium", "severity": "MEDIUM", "location": {}},
                {"rule_id": "AVD-AZU-0004", "description": "low",    "severity": "LOW",    "location": {}},
            ]
        })
        report = TfsecRunner()._parse(payload)
        assert report.ok
        assert report.medium == 1
        assert report.low    == 1

    def test_empty_results_is_ok(self):
        payload = json.dumps({"results": []})
        report = TfsecRunner()._parse(payload)
        assert report.ok
        assert report.total == 0

    def test_invalid_json_returns_empty_report(self):
        report = TfsecRunner()._parse("not json")
        assert report.total == 0

    def test_total_property(self):
        report = TfsecReport(critical=1, high=2, medium=3, low=4)
        assert report.total == 10

    def test_finding_model_defaults(self):
        f = TfsecFinding(rule_id="X", description="desc")
        assert f.severity == "UNKNOWN"
        assert f.filename == ""
        assert f.start_line == 0


# ── InfracostRunner ──────────────────────────────────────────────────────────

class TestInfracostRunner:

    def test_returns_not_installed_when_infracost_missing(self):
        with patch("shutil.which", return_value=None):
            report = InfracostRunner().run(Path("."))
        assert not report.installed

    def test_parses_total_cost(self):
        payload = json.dumps({
            "totalMonthlyCost": "45.67",
            "totalHourlyCost":  "0.063",
            "currency": "USD",
            "projects": [{
                "breakdown": {
                    "resources": [
                        {"name": "azurerm_storage_account.main", "monthlyCost": "10.00", "monthlyQuantity": "1", "unit": "months"},
                        {"name": "azurerm_databricks_workspace.ws", "monthlyCost": "35.67", "monthlyQuantity": "1", "unit": "months"},
                    ]
                }
            }]
        })
        report = InfracostRunner()._parse(payload)
        assert report.total_monthly_cost == pytest.approx(45.67)
        assert report.currency == "USD"
        assert len(report.resources) == 2
        # Sorted by cost descending
        assert report.resources[0].monthly_cost == pytest.approx(35.67)

    def test_returns_error_on_nonzero_exit(self):
        with patch("shutil.which", return_value="/usr/bin/infracost"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="Error: API key not configured",
                )
                report = InfracostRunner().run(Path("."))
        assert "API key" in report.error

    def test_empty_projects_gives_zero_cost(self):
        payload = json.dumps({
            "totalMonthlyCost": "0",
            "totalHourlyCost": "0",
            "currency": "USD",
            "projects": [],
        })
        report = InfracostRunner()._parse(payload)
        assert report.total_monthly_cost == 0.0
        assert len(report.resources) == 0

    def test_resource_cost_model(self):
        r = InfracostResourceCost(name="storage", monthly_cost=5.50)
        assert r.name == "storage"
        assert r.monthly_cost == pytest.approx(5.50)


# ── dataforge validate command ───────────────────────────────────────────────

class TestValidateCommand:

    def _checkov_ok(self):
        from dataforge.validation.checkov_runner import CheckovReport
        return CheckovReport(passed=5, failed=0)

    def _checkov_fail(self):
        from dataforge.validation.checkov_runner import CheckovReport, CheckovFinding
        return CheckovReport(
            passed=3, failed=2,
            failed_checks=[CheckovFinding(
                check_id="CKV_AZU_1",
                check_type="terraform",
                resource="azurerm_storage_account.main",
                file_path="main.tf",
                severity="HIGH",
            )],
        )

    def test_exits_0_when_all_pass(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "azurerm_resource_group" "rg" {}')
        with (
            patch("dataforge.cli.CheckovRunner.run", return_value=self._checkov_ok()),
            patch("dataforge.cli.TfsecRunner.run",   return_value=TfsecReport()),
            patch("dataforge.cli.InfracostRunner.run", return_value=InfracostReport()),
        ):
            r = CliRunner().invoke(cli, ["validate", str(tmp_path)])
        assert r.exit_code == 0

    def test_exits_1_when_checkov_fails(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "azurerm_resource_group" "rg" {}')
        with (
            patch("dataforge.cli.CheckovRunner.run", return_value=self._checkov_fail()),
            patch("dataforge.cli.TfsecRunner.run",   return_value=TfsecReport()),
            patch("dataforge.cli.InfracostRunner.run", return_value=InfracostReport()),
        ):
            r = CliRunner().invoke(cli, ["validate", str(tmp_path)])
        assert r.exit_code == 1

    def test_exits_1_when_tfsec_fails(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "azurerm_resource_group" "rg" {}')
        bad_tfsec = TfsecReport(critical=1, findings=[
            TfsecFinding(rule_id="AVD-X-001", description="bad", severity="CRITICAL")
        ])
        with (
            patch("dataforge.cli.CheckovRunner.run", return_value=self._checkov_ok()),
            patch("dataforge.cli.TfsecRunner.run",   return_value=bad_tfsec),
            patch("dataforge.cli.InfracostRunner.run", return_value=InfracostReport()),
        ):
            r = CliRunner().invoke(cli, ["validate", str(tmp_path)])
        assert r.exit_code == 1

    def test_skip_tfsec_flag_skips_tfsec(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "azurerm_resource_group" "rg" {}')
        tfsec_mock = MagicMock()
        with (
            patch("dataforge.cli.CheckovRunner.run", return_value=self._checkov_ok()),
            patch("dataforge.cli.TfsecRunner.run",   tfsec_mock),
            patch("dataforge.cli.InfracostRunner.run", return_value=InfracostReport()),
        ):
            r = CliRunner().invoke(cli, ["validate", str(tmp_path), "--skip-tfsec"])
        tfsec_mock.assert_not_called()
        assert r.exit_code == 0

    def test_skip_infracost_flag_skips_infracost(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "azurerm_resource_group" "rg" {}')
        cost_mock = MagicMock()
        with (
            patch("dataforge.cli.CheckovRunner.run", return_value=self._checkov_ok()),
            patch("dataforge.cli.TfsecRunner.run",   return_value=TfsecReport()),
            patch("dataforge.cli.InfracostRunner.run", cost_mock),
        ):
            r = CliRunner().invoke(cli, ["validate", str(tmp_path), "--skip-infracost"])
        cost_mock.assert_not_called()
        assert r.exit_code == 0

    def test_tfsec_not_installed_does_not_fail(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "azurerm_resource_group" "rg" {}')
        with (
            patch("dataforge.cli.CheckovRunner.run", return_value=self._checkov_ok()),
            patch("dataforge.cli.TfsecRunner.run",   return_value=TfsecReport(installed=False)),
            patch("dataforge.cli.InfracostRunner.run", return_value=InfracostReport()),
        ):
            r = CliRunner().invoke(cli, ["validate", str(tmp_path)])
        assert r.exit_code == 0

    def test_output_contains_checkov(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "azurerm_resource_group" "rg" {}')
        with (
            patch("dataforge.cli.CheckovRunner.run", return_value=self._checkov_ok()),
            patch("dataforge.cli.TfsecRunner.run",   return_value=TfsecReport()),
            patch("dataforge.cli.InfracostRunner.run", return_value=InfracostReport()),
        ):
            r = CliRunner().invoke(cli, ["validate", str(tmp_path)])
        assert "Checkov" in r.output or "checkov" in r.output.lower()

    def test_validate_help_mentions_tfsec(self):
        r = CliRunner().invoke(cli, ["validate", "--help"])
        assert "tfsec" in r.output

    def test_validate_help_mentions_infracost(self):
        r = CliRunner().invoke(cli, ["validate", "--help"])
        assert "infracost" in r.output


# ── dataforge doctor command ─────────────────────────────────────────────────

class TestDoctorCommand:

    def _mock_all_present(self):
        return patch("shutil.which", return_value="/usr/bin/tool")

    def test_doctor_exits_0_when_all_present(self):
        fake_settings = MagicMock()
        fake_settings.llm_provider = "ollama"  # no key required for Ollama

        def _run(cmd, **_):
            if "account" in cmd:
                return MagicMock(returncode=0, stdout='{"user":{"name":"ci@test.com"},"name":"Sub"}', stderr="")
            if "version" in cmd:
                return MagicMock(returncode=0, stdout='{"terraform_version":"1.7.0"}', stderr="")
            return MagicMock(returncode=0, stdout="ansible 2.16", stderr="")

        with (
            patch("shutil.which", return_value="/usr/bin/tool"),
            patch("subprocess.run", side_effect=_run),
            patch("dataforge.cli.get_settings", return_value=fake_settings),
        ):
            r = CliRunner().invoke(cli, ["doctor"])
        assert r.exit_code == 0

    def test_doctor_exits_1_when_tools_missing(self):
        with patch("shutil.which", return_value=None):
            r = CliRunner().invoke(cli, ["doctor"])
        assert r.exit_code == 1

    def test_doctor_output_contains_terraform(self):
        with (
            patch("shutil.which", return_value=None),
        ):
            r = CliRunner().invoke(cli, ["doctor"])
        assert "terraform" in r.output.lower()

    def test_doctor_output_contains_checkov(self):
        with patch("shutil.which", return_value=None):
            r = CliRunner().invoke(cli, ["doctor"])
        assert "checkov" in r.output.lower()

    def test_doctor_output_contains_tfsec(self):
        with patch("shutil.which", return_value=None):
            r = CliRunner().invoke(cli, ["doctor"])
        assert "tfsec" in r.output.lower()

    def test_doctor_output_contains_infracost(self):
        with patch("shutil.which", return_value=None):
            r = CliRunner().invoke(cli, ["doctor"])
        assert "infracost" in r.output.lower()

    def test_doctor_shows_summary(self):
        with patch("shutil.which", return_value=None):
            r = CliRunner().invoke(cli, ["doctor"])
        assert "Summary" in r.output

    def test_doctor_help_lists_checks(self):
        r = CliRunner().invoke(cli, ["doctor", "--help"])
        assert "terraform" in r.output.lower()
        assert "azure-cli" in r.output.lower()
