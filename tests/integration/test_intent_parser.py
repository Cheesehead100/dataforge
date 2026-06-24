"""Integration tests for IntentParser — mocked Anthropic client."""

from __future__ import annotations

import json

import pytest
from unittest.mock import MagicMock, patch

from dataforge.config import Settings
from dataforge.constants import NodeType, OperationType
from dataforge.models.flow_graph import FlowGraph
from dataforge.parsing.intent_parser import IntentParser, ParseError


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


@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="sk-ant-test",
        parse_model="claude-haiku-4-5-20251001",
    )


def _mock_client(tool_input: dict) -> MagicMock:
    """Build a mock Anthropic client that returns a canned tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = tool_input

    response = MagicMock()
    response.content = [tool_block]

    client = MagicMock()
    client.messages.create.return_value = response
    return client


class TestIntentParserSuccess:
    def test_valid_canned_response_produces_flow_graph(self, settings):
        client = _mock_client(CANNED_FLOW_GRAPH)
        parser = IntentParser(client, settings)
        graph = parser.parse("Read from ADLS, trigger Databricks")

        assert isinstance(graph, FlowGraph)
        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2

    def test_metadata_overrides_applied(self, settings):
        client = _mock_client(CANNED_FLOW_GRAPH)
        parser = IntentParser(client, settings)
        graph = parser.parse(
            "test",
            metadata_overrides={"environment": "prod", "location": "westeurope"},
        )
        assert graph.metadata.environment == "prod"
        assert graph.metadata.location == "westeurope"

    def test_original_prompt_injected_into_metadata(self, settings):
        data = dict(CANNED_FLOW_GRAPH)
        data["metadata"] = {k: v for k, v in CANNED_FLOW_GRAPH["metadata"].items()}
        del data["metadata"]["original_prompt"]

        client = _mock_client(data)
        parser = IntentParser(client, settings)
        graph = parser.parse("my prompt")
        assert graph.metadata.original_prompt == "my prompt"

    def test_node_types_parsed_correctly(self, settings):
        client = _mock_client(CANNED_FLOW_GRAPH)
        graph = IntentParser(client, settings).parse("test")
        types = {n.type for n in graph.nodes}
        assert NodeType.ADF in types
        assert NodeType.ADLS in types
        assert NodeType.DATABRICKS in types


class TestIntentParserFailure:
    def test_no_tool_call_raises_parse_error(self, settings):
        response = MagicMock()
        response.content = [MagicMock(type="text", text="I can't do that")]
        client = MagicMock()
        client.messages.create.return_value = response

        with pytest.raises(ParseError, match="tool"):
            IntentParser(client, settings).parse("test")

    def test_invalid_schema_raises_parse_error(self, settings):
        client = _mock_client({"nodes": "this should be a list", "edges": [], "metadata": {}})
        with pytest.raises(ParseError):
            IntentParser(client, settings).parse("test")

    def test_cyclic_graph_raises_parse_error(self, settings):
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
        client = _mock_client(cyclic)
        with pytest.raises(ParseError):
            IntentParser(client, settings).parse("cyclic pipeline")
