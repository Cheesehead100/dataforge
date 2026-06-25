"""E2E test for the 'generate' CLI command with mocked LLM clients."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from pydantic import SecretStr

from dataforge.cli import cli
from dataforge.config import Settings


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


def _build_mock_anthropic():
    """Return a mock Anthropic client that returns the canned graph from Haiku
    and a trivial string from Sonnet."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = CANNED_GRAPH

    haiku_response = MagicMock()
    haiku_response.content = [tool_block]

    sonnet_text_block = MagicMock()
    sonnet_text_block.text = "# polished HCL"

    sonnet_response = MagicMock()
    sonnet_response.content = [sonnet_text_block]

    client = MagicMock()
    client.messages.create.side_effect = lambda **kwargs: (
        haiku_response if "haiku" in kwargs.get("model", "") else sonnet_response
    )
    return client


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_anthropic_env(tmp_path):
    """Patch get_settings to avoid requiring a real API key."""
    return tmp_path


def _patches():
    """Context managers that mock both the Anthropic client and get_settings."""
    return [
        patch("dataforge.cli.get_settings", return_value=_test_settings()),
        patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()),
    ]


class TestGenerateCommand:
    def test_generates_files_with_mocked_llm(self, runner, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
            result = runner.invoke(
                cli,
                ["generate", "ADLS to Databricks to Fabric",
                 "--output", str(out), "--no-validate"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        assert out.exists()
        written = list(out.glob("*.tf"))
        assert len(written) >= 1

    def test_rbac_file_always_written(self, runner, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
            runner.invoke(
                cli,
                ["generate", "ADF reads ADLS, triggers Databricks",
                 "--output", str(out), "--no-validate"],
                catch_exceptions=False,
            )
        assert (out / "rbac.tf").exists()

    def test_no_llm_polish_flag_skips_sonnet(self, runner, tmp_path):
        out = tmp_path / "output"
        mock_client = _build_mock_anthropic()
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.anthropic.Anthropic", return_value=mock_client):
            runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--no-validate", "--no-llm-polish"],
                catch_exceptions=False,
            )
        calls = mock_client.messages.create.call_args_list
        sonnet_calls = [c for c in calls if "sonnet" in c.kwargs.get("model", "")]
        assert len(sonnet_calls) == 0

    def test_dry_run_writes_no_files(self, runner, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.get_settings", return_value=_test_settings()), \
             patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
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
             patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
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
             patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
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


class TestVersionCommand:
    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output
