"""E2E test for the 'generate' CLI command with mocked LLM clients."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from dataforge.cli import cli


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
def mock_anthropic_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    return tmp_path


class TestGenerateCommand:
    def test_generates_files_with_mocked_llm(self, runner, mock_anthropic_env, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
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

    def test_rbac_file_always_written(self, runner, mock_anthropic_env, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
            runner.invoke(
                cli,
                ["generate", "ADF reads ADLS, triggers Databricks",
                 "--output", str(out), "--no-validate"],
                catch_exceptions=False,
            )
        assert (out / "rbac.tf").exists()

    def test_no_llm_polish_flag_skips_sonnet(self, runner, mock_anthropic_env, tmp_path):
        out = tmp_path / "output"
        mock_client = _build_mock_anthropic()
        with patch("dataforge.cli.anthropic.Anthropic", return_value=mock_client):
            runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--no-validate", "--no-llm-polish"],
                catch_exceptions=False,
            )
        # Sonnet should NOT be called with --no-llm-polish
        calls = mock_client.messages.create.call_args_list
        sonnet_calls = [c for c in calls if "sonnet" in c.kwargs.get("model", "")]
        assert len(sonnet_calls) == 0

    def test_dry_run_writes_no_files(self, runner, mock_anthropic_env, tmp_path):
        out = tmp_path / "output"
        with patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
            result = runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--dry-run"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert not out.exists()

    def test_overwrite_flag_replaces_existing_files(self, runner, mock_anthropic_env, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        (out / "existing.tf").write_text("old content")

        with patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
            result = runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--no-validate", "--overwrite"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

    def test_no_overwrite_flag_exits_nonzero_on_existing_dir(self, runner, mock_anthropic_env, tmp_path):
        out = tmp_path / "output"
        out.mkdir()
        (out / "existing.tf").write_text("old")

        with patch("dataforge.cli.anthropic.Anthropic", return_value=_build_mock_anthropic()):
            result = runner.invoke(
                cli,
                ["generate", "ADF reads ADLS",
                 "--output", str(out), "--no-validate"],
            )
        assert result.exit_code != 0


class TestVersionCommand:
    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output
