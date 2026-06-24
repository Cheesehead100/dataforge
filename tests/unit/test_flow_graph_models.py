"""Tests for FlowGraph Pydantic validation rules."""

import pytest
from pydantic import ValidationError

from dataforge.constants import NodeType, OperationType
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode


@pytest.fixture
def meta():
    return FlowMetadata(original_prompt="test")


class TestFlowNode:
    def test_valid_node(self):
        n = FlowNode(id="raw_adls", type=NodeType.ADLS, name="raw")
        assert n.id == "raw_adls"

    def test_id_must_start_with_letter(self, meta):
        with pytest.raises(ValidationError):
            FlowNode(id="1bad", type=NodeType.ADLS, name="x")

    def test_id_no_uppercase(self):
        with pytest.raises(ValidationError):
            FlowNode(id="BadID", type=NodeType.ADLS, name="x")

    def test_id_no_hyphens(self):
        with pytest.raises(ValidationError):
            FlowNode(id="bad-id", type=NodeType.ADLS, name="x")

    def test_frozen_node_cannot_be_mutated(self):
        n = FlowNode(id="adls", type=NodeType.ADLS, name="x")
        with pytest.raises(Exception):
            n.name = "changed"  # type: ignore


class TestFlowEdge:
    def test_self_loop_rejected(self):
        with pytest.raises(ValidationError):
            FlowEdge(source="a", target="a", operation=OperationType.READ)

    def test_valid_edge(self):
        e = FlowEdge(source="adf", target="adls", operation=OperationType.READ)
        assert e.source == "adf"


class TestFlowGraph:
    def test_duplicate_node_ids_rejected(self, meta):
        with pytest.raises(ValidationError):
            FlowGraph(
                nodes=[
                    FlowNode(id="dup", type=NodeType.ADF, name="a"),
                    FlowNode(id="dup", type=NodeType.ADLS, name="b"),
                ],
                edges=[FlowEdge(source="dup", target="dup", operation=OperationType.READ)],
                metadata=meta,
            )

    def test_edge_referencing_unknown_source_rejected(self, meta):
        with pytest.raises(ValidationError):
            FlowGraph(
                nodes=[FlowNode(id="adls", type=NodeType.ADLS, name="x")],
                edges=[FlowEdge(source="ghost", target="adls", operation=OperationType.READ)],
                metadata=meta,
            )

    def test_edge_referencing_unknown_target_rejected(self, meta):
        with pytest.raises(ValidationError):
            FlowGraph(
                nodes=[FlowNode(id="adf", type=NodeType.ADF, name="x")],
                edges=[FlowEdge(source="adf", target="ghost", operation=OperationType.READ)],
                metadata=meta,
            )

    def test_empty_nodes_rejected(self, meta):
        with pytest.raises(ValidationError):
            FlowGraph(nodes=[], edges=[], metadata=meta)

    def test_valid_graph_with_multiple_edges(self, meta):
        g = FlowGraph(
            nodes=[
                FlowNode(id="adf", type=NodeType.ADF, name="a"),
                FlowNode(id="adls", type=NodeType.ADLS, name="b"),
                FlowNode(id="dbw", type=NodeType.DATABRICKS, name="c"),
            ],
            edges=[
                FlowEdge(source="adf", target="adls", operation=OperationType.READ),
                FlowEdge(source="adf", target="dbw", operation=OperationType.TRIGGER),
            ],
            metadata=meta,
        )
        assert len(g.nodes) == 3
        assert len(g.edges) == 2

    def test_node_lookup_helper(self, minimal_graph):
        n = minimal_graph.node("adf")
        assert n.type == NodeType.ADF

    def test_node_lookup_missing_raises_key_error(self, minimal_graph):
        with pytest.raises(KeyError):
            minimal_graph.node("ghost")

    def test_nodes_of_type_filter(self, adls_to_databricks_to_fabric_graph):
        adf_nodes = adls_to_databricks_to_fabric_graph.nodes_of_type(NodeType.ADF)
        assert len(adf_nodes) == 1

    def test_edges_from_filter(self, adls_to_databricks_to_fabric_graph):
        edges = adls_to_databricks_to_fabric_graph.edges_from("adf_pipeline")
        assert len(edges) == 2
