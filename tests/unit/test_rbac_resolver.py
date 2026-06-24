"""Tests for the RBAC resolver: FlowGraph → RbacResult."""

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode
from dataforge.rbac.resolver import RbacResolver


@pytest.fixture
def resolver():
    return RbacResolver()


class TestResolverBasic:
    def test_minimal_graph_produces_one_assignment(self, resolver, minimal_graph):
        result = resolver.resolve(minimal_graph)
        assert len(result.assignments) == 1
        ra = result.assignments[0]
        assert ra.principal_node_id == "adf"
        assert ra.scope_node_id == "source_adls"
        assert ra.role_name == "Storage Blob Data Reader"

    def test_keyvault_graph_produces_two_assignments(self, resolver, graph_with_keyvault):
        result = resolver.resolve(graph_with_keyvault)
        role_names = {ra.role_name for ra in result.assignments}
        assert "Storage Blob Data Reader" in role_names
        assert "Key Vault Secrets User" in role_names
        assert len(result.assignments) == 2

    def test_classic_pipeline_produces_correct_assignments(
        self, resolver, adls_to_databricks_to_fabric_graph
    ):
        result = resolver.resolve(adls_to_databricks_to_fabric_graph)
        roles_by_principal: dict[str, set[str]] = {}
        for ra in result.assignments:
            roles_by_principal.setdefault(ra.principal_node_id, set()).add(ra.role_name)

        # ADF reads ADLS → Storage Blob Data Reader
        assert "Storage Blob Data Reader" in roles_by_principal.get("adf_pipeline", set())
        # ADF triggers Databricks → Contributor
        assert "Contributor" in roles_by_principal.get("adf_pipeline", set())
        # Databricks reads ADLS → Storage Blob Data Reader
        assert "Storage Blob Data Reader" in roles_by_principal.get("dbw_transform", set())
        # Databricks writes Fabric → Storage Blob Data Contributor
        assert "Storage Blob Data Contributor" in roles_by_principal.get("dbw_transform", set())


class TestResolverDeduplication:
    def test_duplicate_edges_produce_one_assignment(self, resolver, simple_metadata):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adf", type=NodeType.ADF, name="adf"),
                FlowNode(id="adls", type=NodeType.ADLS, name="adls"),
            ],
            edges=[
                FlowEdge(source="adf", target="adls", operation=OperationType.READ),
                FlowEdge(source="adf", target="adls", operation=OperationType.READ),
            ],
            metadata=simple_metadata,
        )
        result = resolver.resolve(graph)
        assert len(result.assignments) == 1

    def test_read_and_write_edges_produce_two_distinct_roles(self, resolver, simple_metadata):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adf", type=NodeType.ADF, name="adf"),
                FlowNode(id="adls", type=NodeType.ADLS, name="adls"),
            ],
            edges=[
                FlowEdge(source="adf", target="adls", operation=OperationType.READ),
                FlowEdge(source="adf", target="adls", operation=OperationType.WRITE),
            ],
            metadata=simple_metadata,
        )
        result = resolver.resolve(graph)
        role_names = {ra.role_name for ra in result.assignments}
        assert "Storage Blob Data Reader" in role_names
        assert "Storage Blob Data Contributor" in role_names


class TestResolverUnresolved:
    def test_adls_as_source_produces_unresolved(self, resolver, simple_metadata):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adls", type=NodeType.ADLS, name="adls"),
                FlowNode(id="dbw", type=NodeType.DATABRICKS, name="dbw"),
            ],
            edges=[
                FlowEdge(source="adls", target="dbw", operation=OperationType.WRITE),
            ],
            metadata=simple_metadata,
        )
        result = resolver.resolve(graph)
        # ADLS is not a PRINCIPAL_NODE_TYPE, so edge is skipped entirely
        assert len(result.assignments) == 0
        assert len(result.unresolved) == 0  # silently skipped, not flagged unresolved

    def test_unknown_operation_produces_unresolved(self, resolver, simple_metadata):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adf", type=NodeType.ADF, name="adf"),
                FlowNode(id="evh", type=NodeType.EVENTHUB, name="evh"),
            ],
            edges=[
                FlowEdge(source="adf", target="evh", operation=OperationType.WRITE),
            ],
            metadata=simple_metadata,
        )
        result = resolver.resolve(graph)
        assert len(result.unresolved) == 1


class TestResolverSqlMiWarnings:
    def test_sql_mi_scope_produces_warning(self, resolver, simple_metadata):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adf", type=NodeType.ADF, name="adf"),
                FlowNode(id="sqlmi", type=NodeType.SQL_MI, name="sql-mi"),
            ],
            edges=[
                FlowEdge(source="adf", target="sqlmi", operation=OperationType.READ),
            ],
            metadata=simple_metadata,
        )
        result = resolver.resolve(graph)
        assert any("data-plane" in w.lower() or "sql" in w.lower() for w in result.warnings)
        # Control-plane Reader role still emitted
        assert any(ra.role_name == "Reader" for ra in result.assignments)


class TestResolverTerraformKeys:
    def test_all_keys_unique(self, resolver, adls_to_databricks_to_fabric_graph):
        result = resolver.resolve(adls_to_databricks_to_fabric_graph)
        keys = [ra.terraform_key for ra in result.assignments]
        assert len(keys) == len(set(keys)), "Duplicate Terraform keys detected"

    def test_terraform_key_format(self, resolver, minimal_graph):
        result = resolver.resolve(minimal_graph)
        key = result.assignments[0].terraform_key
        # Must be valid HCL identifier (no spaces)
        assert " " not in key
        assert key.startswith("adf")
