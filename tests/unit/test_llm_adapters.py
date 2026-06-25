"""Unit tests for the LLM adapter layer.

Tests cover:
  - build_adapter() factory — correct adapter for each provider, helpful errors
  - AnthropicAdapter — delegates to Anthropic SDK correctly
  - OpenAiAdapter — delegates to OpenAI SDK correctly (only if openai is installed)
  - IntentParser — uses adapter correctly, retry logic
  - HclGenerator — skips polish when adapter=None, calls adapter when set

All SDK calls are mocked so these tests run without any API keys.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from dataforge.config import Settings
from dataforge.llm.adapter import LlmAdapter, build_adapter
from dataforge.llm.anthropic_adapter import AnthropicAdapter
from dataforge.generation.hcl_generator import HclGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.flow_graph import FlowGraph, FlowMetadata, FlowNode, FlowEdge
from dataforge.models.rbac import RbacResult
from dataforge.constants import NodeType, OperationType


# ── Shared test helpers ────────────────────────────────────────────────────────

def _settings(**kwargs) -> Settings:
    """Build a Settings object with sensible test defaults."""
    defaults = {
        "anthropic_api_key": "sk-ant-test",
        "parse_model": "claude-haiku-4-5-20251001",
        "generate_model": "claude-sonnet-4-6",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def _mock_adapter(text_reply: str = "polished content") -> LlmAdapter:
    """Build a mock LlmAdapter for use in HclGenerator tests."""
    adapter = MagicMock(spec=LlmAdapter)
    adapter.complete.return_value = text_reply
    adapter.extract_json.return_value = {}
    return adapter


def _simple_graph() -> FlowGraph:
    return FlowGraph(
        nodes=[
            FlowNode(id="lake", type=NodeType.ADLS, name="Data Lake"),
            FlowNode(id="dbx", type=NodeType.DATABRICKS, name="Databricks"),
        ],
        edges=[FlowEdge(source="lake", target="dbx", operation=OperationType.READ)],
        metadata=FlowMetadata(
            location="eastus",
            resource_group="rg-test",
            environment="dev",
            application_name="test",
        ),
    )


_RBAC = RbacResult(assignments=[], unresolved=[], warnings=[])


# ── build_adapter() factory ───────────────────────────────────────────────────

class TestBuildAdapter:

    def test_anthropic_provider_returns_anthropic_adapter(self):
        settings = _settings(llm_provider="anthropic")
        with patch("dataforge.llm.anthropic_adapter.anthropic.Anthropic"):
            adapter = build_adapter(settings)
        assert isinstance(adapter, AnthropicAdapter)

    def test_anthropic_missing_key_raises_value_error(self):
        settings = _settings(anthropic_api_key=None, llm_provider="anthropic")
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            build_adapter(settings)

    def test_openai_missing_key_raises_value_error(self):
        settings = _settings(llm_provider="openai", openai_api_key=None)
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            build_adapter(settings)

    def test_unknown_provider_raises_value_error(self):
        settings = _settings(llm_provider="gemini")
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            build_adapter(settings)

    def test_groq_provider_uses_openai_adapter(self):
        # Groq uses the OpenAI-compatible API — same adapter, different base URL.
        openai = pytest.importorskip("openai", reason="openai not installed")
        settings = _settings(
            llm_provider="groq",
            openai_api_key="gsk_test",
            openai_base_url="https://api.groq.com/openai/v1",
        )
        from dataforge.llm.openai_adapter import OpenAiAdapter
        with patch("dataforge.llm.openai_adapter.OpenAI"):
            adapter = build_adapter(settings)
        assert isinstance(adapter, OpenAiAdapter)

    def test_ollama_provider_uses_openai_adapter(self):
        pytest.importorskip("openai", reason="openai not installed")
        settings = _settings(
            llm_provider="ollama",
            openai_api_key="ollama",
            openai_base_url="http://localhost:11434/v1",
        )
        from dataforge.llm.openai_adapter import OpenAiAdapter
        with patch("dataforge.llm.openai_adapter.OpenAI"):
            adapter = build_adapter(settings)
        assert isinstance(adapter, OpenAiAdapter)


# ── AnthropicAdapter ──────────────────────────────────────────────────────────

class TestAnthropicAdapter:
    """Tests that AnthropicAdapter calls the Anthropic SDK with the right arguments."""

    def _make_adapter(self) -> tuple[AnthropicAdapter, MagicMock]:
        """Returns the adapter and a handle to the mock Anthropic client."""
        mock_client = MagicMock()
        with patch("dataforge.llm.anthropic_adapter.anthropic.Anthropic", return_value=mock_client):
            adapter = AnthropicAdapter(_settings())
        return adapter, mock_client

    def test_complete_calls_messages_create(self):
        adapter, client = self._make_adapter()
        client.messages.create.return_value.content = [MagicMock(text="result")]
        result = adapter.complete("system", [{"role": "user", "content": "hi"}])
        assert client.messages.create.called
        assert result == "result"

    def test_complete_passes_system_as_kwarg(self):
        adapter, client = self._make_adapter()
        client.messages.create.return_value.content = [MagicMock(text="ok")]
        adapter.complete("my system", [])
        _, kwargs = client.messages.create.call_args
        assert kwargs["system"] == "my system"

    def test_extract_json_returns_tool_input(self):
        adapter, client = self._make_adapter()
        # Simulate the Anthropic response containing a tool_use block.
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {"nodes": [], "edges": [], "metadata": {}}
        client.messages.create.return_value.content = [tool_block]

        result = adapter.extract_json("sys", [], {"type": "object"}, "my_tool")
        assert result == {"nodes": [], "edges": [], "metadata": {}}

    def test_extract_json_raises_when_no_tool_call(self):
        adapter, client = self._make_adapter()
        # Model replied with text instead of calling the tool.
        text_block = MagicMock()
        text_block.type = "text"
        client.messages.create.return_value.content = [text_block]

        with pytest.raises(ValueError, match="did not call"):
            adapter.extract_json("sys", [], {}, "my_tool")

    def test_extract_json_uses_tool_choice_any(self):
        adapter, client = self._make_adapter()
        tool_block = MagicMock(type="tool_use", input={})
        client.messages.create.return_value.content = [tool_block]
        adapter.extract_json("sys", [], {}, "my_tool")

        _, kwargs = client.messages.create.call_args
        # "any" forces the model to call a tool — it cannot reply with text.
        assert kwargs["tool_choice"] == {"type": "any"}

    def test_extract_json_schema_passed_as_input_schema(self):
        adapter, client = self._make_adapter()
        tool_block = MagicMock(type="tool_use", input={})
        client.messages.create.return_value.content = [tool_block]
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        adapter.extract_json("sys", [], schema, "my_tool")

        _, kwargs = client.messages.create.call_args
        assert kwargs["tools"][0]["input_schema"] == schema


# ── OpenAiAdapter (only runs if openai is installed) ─────────────────────────

class TestOpenAiAdapter:

    @pytest.fixture(autouse=True)
    def require_openai(self):
        pytest.importorskip("openai", reason="openai not installed — skipping OpenAI adapter tests")

    def _make_adapter(self) -> tuple:
        from dataforge.llm.openai_adapter import OpenAiAdapter
        mock_client = MagicMock()
        with patch("dataforge.llm.openai_adapter.OpenAI", return_value=mock_client):
            adapter = OpenAiAdapter(_settings(
                llm_provider="openai",
                openai_api_key="sk-test",
            ))
        return adapter, mock_client

    def test_complete_prepends_system_message(self):
        adapter, client = self._make_adapter()
        client.chat.completions.create.return_value.choices[0].message.content = "reply"
        adapter.complete("my system", [{"role": "user", "content": "hi"}])

        _, kwargs = client.chat.completions.create.call_args
        # System message must be the first entry in messages.
        assert kwargs["messages"][0] == {"role": "system", "content": "my system"}

    def test_complete_returns_choice_content(self):
        adapter, client = self._make_adapter()
        client.chat.completions.create.return_value.choices[0].message.content = "my output"
        result = adapter.complete("sys", [])
        assert result == "my output"

    def test_extract_json_parses_function_arguments(self):
        adapter, client = self._make_adapter()
        # The OpenAI SDK returns function arguments as a JSON string.
        tool_call = MagicMock()
        tool_call.function.arguments = json.dumps({"key": "value"})
        client.chat.completions.create.return_value.choices[0].message.tool_calls = [tool_call]

        result = adapter.extract_json("sys", [], {}, "my_func")
        assert result == {"key": "value"}

    def test_extract_json_raises_when_no_tool_calls(self):
        adapter, client = self._make_adapter()
        client.chat.completions.create.return_value.choices[0].message.tool_calls = []

        with pytest.raises(ValueError, match="did not call"):
            adapter.extract_json("sys", [], {}, "my_func")

    def test_extract_json_raises_on_invalid_json(self):
        adapter, client = self._make_adapter()
        tool_call = MagicMock()
        tool_call.function.arguments = "not-valid-json{"
        client.chat.completions.create.return_value.choices[0].message.tool_calls = [tool_call]

        with pytest.raises(ValueError, match="invalid JSON"):
            adapter.extract_json("sys", [], {}, "my_func")

    def test_custom_base_url_passed_to_openai_client(self):
        from dataforge.llm.openai_adapter import OpenAiAdapter
        mock_openai_cls = MagicMock()
        settings = _settings(
            llm_provider="groq",
            openai_api_key="gsk_test",
            openai_base_url="https://api.groq.com/openai/v1",
        )
        with patch("dataforge.llm.openai_adapter.OpenAI", mock_openai_cls):
            OpenAiAdapter(settings)
        _, kwargs = mock_openai_cls.call_args
        assert kwargs["base_url"] == "https://api.groq.com/openai/v1"


# ── HclGenerator with adapter ─────────────────────────────────────────────────

class TestHclGeneratorAdapter:

    def test_no_adapter_skips_polish(self):
        # When adapter=None, the skeleton is returned as-is.
        renderer = MagicMock(spec=Renderer)
        renderer.render_all.return_value = []
        result = HclGenerator(renderer, None).generate(_simple_graph(), _RBAC, llm_polish=True)
        # No LLM calls happen — adapter is None.
        assert result.files == []

    def test_llm_polish_false_skips_adapter(self):
        adapter = _mock_adapter()
        renderer = MagicMock(spec=Renderer)
        renderer.render_all.return_value = []
        HclGenerator(renderer, adapter).generate(_simple_graph(), _RBAC, llm_polish=False)
        # llm_polish=False means adapter.complete() is never called.
        adapter.complete.assert_not_called()

    def test_llm_polish_calls_adapter_for_tf_files(self):
        from dataforge.models.terraform import TerraformFile
        adapter = _mock_adapter("polished tf content")
        renderer = MagicMock(spec=Renderer)
        renderer.render_all.return_value = [
            TerraformFile(filename="main.tf", content="original"),
        ]
        result = HclGenerator(renderer, adapter).generate(_simple_graph(), _RBAC, llm_polish=True)
        # adapter.complete() was called once for main.tf
        adapter.complete.assert_called_once()
        assert result.files[0].content == "polished tf content"

    def test_skips_rbac_tf_during_polish(self):
        from dataforge.models.terraform import TerraformFile
        adapter = _mock_adapter("polished")
        renderer = MagicMock(spec=Renderer)
        renderer.render_all.return_value = [
            TerraformFile(filename="rbac.tf", content="rbac-original"),
            TerraformFile(filename="main.tf", content="main-original"),
        ]
        result = HclGenerator(renderer, adapter).generate(_simple_graph(), _RBAC, llm_polish=True)
        # rbac.tf is excluded from polish — only main.tf is sent to the LLM.
        adapter.complete.assert_called_once()
        rbac_file = next(f for f in result.files if f.filename == "rbac.tf")
        assert rbac_file.content == "rbac-original"

    def test_skips_yml_files_during_polish(self):
        from dataforge.models.terraform import TerraformFile
        adapter = _mock_adapter("polished")
        renderer = MagicMock(spec=Renderer)
        renderer.render_all.return_value = [
            TerraformFile(filename=".github/workflows/ci.yml", content="ci-original"),
        ]
        HclGenerator(renderer, adapter).generate(_simple_graph(), _RBAC, llm_polish=True)
        # YAML files are never polished — they're not HCL.
        adapter.complete.assert_not_called()

    def test_polish_failure_keeps_skeleton(self):
        from dataforge.models.terraform import TerraformFile
        adapter = MagicMock(spec=LlmAdapter)
        adapter.complete.side_effect = RuntimeError("LLM error")
        renderer = MagicMock(spec=Renderer)
        renderer.render_all.return_value = [
            TerraformFile(filename="main.tf", content="skeleton"),
        ]
        # Should not raise — failure is logged and skeleton is kept.
        result = HclGenerator(renderer, adapter).generate(_simple_graph(), _RBAC, llm_polish=True)
        assert result.files[0].content == "skeleton"
