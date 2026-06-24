"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode


@pytest.fixture
def simple_metadata() -> FlowMetadata:
    return FlowMetadata(
        original_prompt="test prompt",
        location="eastus",
        resource_group="rg-test",
        environment="dev",
        application_name="testapp",
    )


@pytest.fixture
def adls_to_databricks_to_fabric_graph(simple_metadata) -> FlowGraph:
    """Classic: ADLS read → Databricks transform → Fabric write (via ADF orchestration)."""
    return FlowGraph(
        nodes=[
            FlowNode(id="raw_adls", type=NodeType.ADLS, name="raw-storage"),
            FlowNode(id="adf_pipeline", type=NodeType.ADF, name="adf-sales"),
            FlowNode(id="dbw_transform", type=NodeType.DATABRICKS, name="dbw-transform"),
            FlowNode(id="fabric_lh", type=NodeType.FABRIC_LAKEHOUSE, name="fabric-analytics"),
        ],
        edges=[
            FlowEdge(source="adf_pipeline", target="raw_adls", operation=OperationType.READ),
            FlowEdge(source="adf_pipeline", target="dbw_transform", operation=OperationType.TRIGGER),
            FlowEdge(source="dbw_transform", target="raw_adls", operation=OperationType.READ),
            FlowEdge(source="dbw_transform", target="fabric_lh", operation=OperationType.WRITE),
        ],
        metadata=simple_metadata,
    )


@pytest.fixture
def minimal_graph(simple_metadata) -> FlowGraph:
    """Minimal: ADF reads from ADLS."""
    return FlowGraph(
        nodes=[
            FlowNode(id="source_adls", type=NodeType.ADLS, name="source"),
            FlowNode(id="adf", type=NodeType.ADF, name="adf-pipeline"),
        ],
        edges=[
            FlowEdge(source="adf", target="source_adls", operation=OperationType.READ),
        ],
        metadata=simple_metadata,
    )


@pytest.fixture
def graph_with_keyvault(simple_metadata) -> FlowGraph:
    return FlowGraph(
        nodes=[
            FlowNode(id="adls", type=NodeType.ADLS, name="storage"),
            FlowNode(id="adf", type=NodeType.ADF, name="adf"),
            FlowNode(id="kv", type=NodeType.KEY_VAULT, name="key-vault"),
        ],
        edges=[
            FlowEdge(source="adf", target="adls", operation=OperationType.READ),
            FlowEdge(source="adf", target="kv", operation=OperationType.SECRET_GET),
        ],
        metadata=simple_metadata,
    )
