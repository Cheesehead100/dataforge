"""Integration tests for IntentParser — uses a mock LlmAdapter.

The LlmAdapter interface has one method we care about here: extract_json().
By mocking the adapter instead of the raw Anthropic client, these tests:
  - Stay provider-agnostic (work the same for Anthropic, OpenAI, Groq, etc.)
  - Are simpler to set up (no need to mock deeply nested SDK response objects)
  - Focus on IntentParser's own logic: retry, validation, override injection
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from dataforge.constants import NodeType
from dataforge.llm.adapter import LlmAdapter
from dataforge.models.flow_graph import FlowGraph
from dataforge.parsing.intent_parser import IntentParser, ParseError


# ── Canned test data ──────────────────────────────────────────────────────────

# A valid FlowGraph dict that the mock adapter will return.
CANNED_FLOW_GRAPH = {
    "nodes": [
        {"id": "raw_adls", "type": "adls", "name": "raw-storage", "properties": {}},
        {"id": "adf_pipeline", "type": "adf", "name": "adf-sales", "properties": {}},
        {"id": "dbw_transform", "type": "databricks", "name": "dbw-transform", "properties": {}},
    ],
    "edges": [
        {"source": "adf_pipeline", "target": "raw_adls", "operation": "read"},
        {"source": "adf_pipeline", "target": "dbw_transform", "operation": "trigger"},
    ],
    "metadata": {
        "original_prompt": "Read from ADLS, trigger Databricks",
        "location": "eastus",
        "resource_group": "rg-test",
        "environment": "dev",
        "application_name": "test",
    },
}


def _mock_adapter(return_value: dict) -> LlmAdapter:
    """Build a mock LlmAdapter that returns `return_value` from extract_json().

    The MagicMock satisfies the LlmAdapter duck type because it has
    all the right method names. We only configure extract_json here
    because IntentParser doesn't use complete().
    """
    adapter = MagicMock(spec=LlmAdapter)
    adapter.extract_json.return_value = return_value
    return adapter


def _raising_adapter(exc: Exception) -> LlmAdapter:
    """Build a mock adapter whose extract_json() raises the given exception."""
    adapter = MagicMock(spec=LlmAdapter)
    adapter.extract_json.side_effect = exc
    return adapter


# ── Success cases ─────────────────────────────────────────────────────────────

class TestIntentParserSuccess:

    def test_valid_response_produces_flow_graph(self):
        adapter = _mock_adapter(CANNED_FLOW_GRAPH)
        graph = IntentParser(adapter).parse("Read from ADLS, trigger Databricks")

        assert isinstance(graph, FlowGraph)
        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2

    def test_metadata_overrides_applied(self):
        adapter = _mock_adapter(CANNED_FLOW_GRAPH)
        graph = IntentParser(adapter).parse(
            "test",
            metadata_overrides={"environment": "prod", "location": "westeurope"},
        )
        assert graph.metadata.environment == "prod"
        assert graph.metadata.location == "westeurope"

    def test_original_prompt_injected_when_missing(self):
        # Simulate the LLM forgetting to include original_prompt in metadata.
        data = {**CANNED_FLOW_GRAPH, "metadata": {
            k: v for k, v in CANNED_FLOW_GRAPH["metadata"].items()
            if k != "original_prompt"
        }}
        adapter = _mock_adapter(data)
        graph = IntentParser(adapter).parse("my prompt")
        assert graph.metadata.original_prompt == "my prompt"

    def test_node_types_parsed_correctly(self):
        adapter = _mock_adapter(CANNED_FLOW_GRAPH)
        graph = IntentParser(adapter).parse("test")
        types = {n.type for n in graph.nodes}
        assert NodeType.ADF in types
        assert NodeType.ADLS in types
        assert NodeType.DATABRICKS in types

    def test_metadata_already_present_is_preserved(self):
        adapter = _mock_adapter(CANNED_FLOW_GRAPH)
        graph = IntentParser(adapter).parse("test")
        assert graph.metadata.original_prompt == "Read from ADLS, trigger Databricks"


# ── Failure cases ─────────────────────────────────────────────────────────────

class TestIntentParserFailure:

    def test_adapter_exception_raises_parse_error(self):
        # If the adapter itself throws (e.g. network error, model refused to call tool),
        # IntentParser retries and eventually raises ParseError.
        adapter = _raising_adapter(ValueError("did not call the tool"))
        with pytest.raises(ParseError, match="Failed to parse"):
            IntentParser(adapter).parse("test")

    def test_invalid_schema_raises_parse_error(self):
        # LLM returned something that doesn't match the FlowGraph schema.
        adapter = _mock_adapter({"nodes": "this should be a list", "edges": [], "metadata": {}})
        with pytest.raises(ParseError):
            IntentParser(adapter).parse("test")

    def test_cyclic_graph_raises_parse_error(self):
        # A → B → A is a cycle; the graph validator should reject it.
        cyclic = {
            "nodes": [
                {"id": "a", "type": "adls", "name": "a", "properties": {}},
                {"id": "b", "type": "databricks", "name": "b", "properties": {}},
            ],
            "edges": [
                {"source": "a", "target": "b", "operation": "read"},
                {"source": "b", "target": "a", "operation": "write"},
            ],
            "metadata": {
                "original_prompt": "test",
                "location": "eastus",
                "resource_group": "rg",
                "environment": "dev",
                "application_name": "app",
            },
        }
        adapter = _mock_adapter(cyclic)
        with pytest.raises(ParseError):
            IntentParser(adapter).parse("cyclic pipeline")

    def test_retry_sends_error_feedback_to_adapter(self):
        # Verify that after a failure, the adapter is called again (retry logic).
        # First call returns bad data; second call returns good data.
        adapter = MagicMock(spec=LlmAdapter)
        adapter.extract_json.side_effect = [
            {"nodes": "bad", "edges": [], "metadata": {}},  # first attempt fails validation
            CANNED_FLOW_GRAPH,                               # second attempt succeeds
        ]
        graph = IntentParser(adapter).parse("test with retry")
        assert isinstance(graph, FlowGraph)
        # Adapter was called twice (initial + 1 retry).
        assert adapter.extract_json.call_count == 2
