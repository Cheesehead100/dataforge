"""E2E tests for the 'generate' CLI command with mocked LLM adapter.

These tests invoke the real Click CLI via CliRunner so they exercise the full
CLI → adapter → generator → writer → output path. The only thing mocked is
the LLM call (build_adapter) so no API key is needed to run them.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from pydantic import SecretStr

from dataforge.cli import cli
from dataforge.config import Settings
from dataforge.llm.adapter import LlmAdapter


def _test_settings() -> Settings:
    return Settings(anthropic_api_key=SecretStr("sk-ant-test-key"))


CANNED_GRAPH = {
    "nodes": [
        {"id": "raw_adls", "type": "adls", "name": "raw-storage", "properties": {}},
        {"id": "adf_pipeline", "type": "adf", "name": "adf-sales", "properties": {}},
        {"id": "dbw_transform", "type": "databricks", "name": "dbw-transform", "properties": {}},
        {"id": "fabric_lh", "type": "fabric_lakehouse", "name": "fabric-analytics", "properties": {}},
    ],
    "edges": [
        {"source": "adf_pipeline", "target": "raw_adls", "operation": "read"},
        {"source": "adf_pipeline", "target": "dbw_transform", "operation": "trigger"},
        {"source": "dbw_transform", "target": "raw_adls", "operation": "read"},
        {"source": "dbw_transform", "target": "fabric_lh", "operation": "write"},
    ],
    "metadata": {
        "original_prompt": "ADLS to Databricks to Fabric",
        "location": "eastus",
        "resource_group": "rg-e2e",
        "environment": "dev",
        "application_name": "e2etest",
    },
}


def _build_mock_adapter() -> LlmAdapter:
    """Return a mock LlmAdapter that returns CANNED_GRAPH from extract_json()
    and a trivial HCL string from complete() (the polish pass)."""
    adapter = MagicMock(spec=LlmAdapter)
    adapter.extract_json.return_value = CANNED_GRAPH
    adapter.complete.return_value = "# polished HCL"
    return adapter


@pytest.fixture
def runner():
    return CliRunner()


def _common_patches():
    """Patch get_settings and build_adapter — used by all NL-path tests."""
    return [
        patch("dataforge.cli.get_settings", return_value=_test_settings()),
        patch("dataforge.cli.build_adapter", return_value=_build_mock_adapter()),
    ]


class TestGenerateCommand:

    def test_generates_files_with_mocked_llm(self, runner, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.build_adapter", return_value=_build_mock_adapter()):
            result = runner.invoke(
                cli,
                ["generate", "ADLS to Databricks to Fabric",
                 "--output", str(out), "--no-validate"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert len(list(out.glob("*.tf"))) >= 1

    def test_rbac_file_always_written(self, runner, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.build_adapter", return_value=_build_mock_adapter()):
            runner.invoke(
                cli,
                ["generate", "ADF reads ADLS, triggers Databricks",
                 "--output", str(out), "--no-validate"],
                catch_exceptions=False,
            )
        assert (out / "rbac.tf").exists()

    def test_no_llm_polish_flag_skips_complete(self, runner, tmp_path):
        out = tmp_path / "output"
        mock_adapter = _build_mock_adapter()
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.build_adapter", return_value=mock_adapter):
            runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--no-validate", "--no-llm-polish"],
                catch_exceptions=False,
            )
        # --no-llm-polish means the adapter's complete() (polish pass) is never called.
        mock_adapter.complete.assert_not_called()

    def test_dry_run_writes_no_files(self, runner, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.build_adapter", return_value=_build_mock_adapter()):
            result = runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--dry-run"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert not out.exists()

    def test_overwrite_flag_replaces_existing_files(self, runner, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        (out / "existing.tf").write_text("old content")
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.build_adapter", return_value=_build_mock_adapter()):
            result = runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--no-validate", "--overwrite"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

    def test_no_overwrite_flag_exits_nonzero_on_existing_dir(self, runner, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        (out / "existing.tf").write_text("old")
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.build_adapter", return_value=_build_mock_adapter()):
            result = runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--no-validate"],
            )
        assert result.exit_code != 0


class TestGenerateFromYaml:
    INTENT_YAML = """\
product: test-product
environment: dev
source:
  type: sqlserver
target:
  type: fabric
sla: hourly
"""

    EXPLICIT_YAML = """\
apiVersion: dataforge/v1
kind: DataProduct
metadata:
  name: explicit-product
pipeline:
  nodes:
    - id: raw
      type: adls
    - id: transform
      type: databricks
    - id: sink
      type: fabric_lakehouse
  edges:
    - from: raw
      to: transform
      operation: read
    - from: transform
      to: sink
      operation: write
"""

    def test_intent_form_generates_tf_files(self, runner, tmp_path):
        yaml_file = tmp_path / "data-product.yaml"
        yaml_file.write_text(self.INTENT_YAML, encoding="utf-8")
        out = tmp_path / "output"
        result = runner.invoke(
            cli,
            ["generate", "--from", str(yaml_file), "--output", str(out), "--no-validate"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (out / "rbac.tf").exists()
        assert len(list(out.glob("*.tf"))) >= 3

    def test_explicit_form_generates_tf_files(self, runner, tmp_path):
        yaml_file = tmp_path / "data-product.yaml"
        yaml_file.write_text(self.EXPLICIT_YAML, encoding="utf-8")
        out = tmp_path / "output"
        result = runner.invoke(
            cli,
            ["generate", "--from", str(yaml_file), "--output", str(out), "--no-validate"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (out / "rbac.tf").exists()

    def test_dry_run_with_yaml(self, runner, tmp_path):
        yaml_file = tmp_path / "data-product.yaml"
        yaml_file.write_text(self.INTENT_YAML, encoding="utf-8")
        out = tmp_path / "output"
        result = runner.invoke(
            cli,
            ["generate", "--from", str(yaml_file), "--output", str(out), "--dry-run"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert not out.exists()

    def test_both_description_and_from_exits_nonzero(self, runner, tmp_path):
        yaml_file = tmp_path / "data-product.yaml"
        yaml_file.write_text(self.INTENT_YAML, encoding="utf-8")
        result = runner.invoke(
            cli,
            ["generate", "some description", "--from", str(yaml_file)],
        )
        assert result.exit_code != 0

    def test_neither_description_nor_from_exits_nonzero(self, runner, tmp_path):
        result = runner.invoke(cli, ["generate"])
        assert result.exit_code != 0

    def test_invalid_yaml_exits_nonzero(self, runner, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("product: broken\nenvironment: dev\n", encoding="utf-8")
        out = tmp_path / "output"
        result = runner.invoke(
            cli,
            ["generate", "--from", str(yaml_file), "--output", str(out)],
        )
        assert result.exit_code != 0

    def test_json_output_uses_correct_variables(self, runner, tmp_path):
        # Regression for review finding #4: --json-output was referencing undefined `result`
        # in the YAML path (should use all_files / all_warnings instead).
        yaml_file = tmp_path / "data-product.yaml"
        yaml_file.write_text(self.INTENT_YAML, encoding="utf-8")
        out = tmp_path / "output"
        result = runner.invoke(
            cli,
            ["generate", "--from", str(yaml_file), "--output", str(out),
             "--no-validate", "--json-output"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # The JSON block is printed after the normal output; find it.
        lines = result.output.splitlines()
        json_start = next((i for i, l in enumerate(lines) if l.strip() == "{"), None)
        assert json_start is not None, "No JSON block found in output"
        json_str = "\n".join(lines[json_start:])
        parsed = json.loads(json_str)
        assert "files" in parsed
        assert "warnings" in parsed


class TestVersionCommand:
    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output
