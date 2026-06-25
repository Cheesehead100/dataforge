"""Tests for IntentResolver — DataProduct → FlowGraph."""

from __future__ import annotations

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.models.data_product import (
    ClassificationSpec,
    DataProduct,
    EnvironmentSpec,
    PipelineEdgeSpec,
    PipelineNodeSpec,
    PipelineSpec,
    ProductMetadata,
    RetentionSpec,
    SourceSpec,
    TargetSpec,
)
from dataforge.parsing.intent_resolver import IntentResolver


def _intent(source_type: str, target_type: str, **kwargs) -> DataProduct:
    return DataProduct(
        product=kwargs.pop("product", "test-product"),
        environment=kwargs.pop("environment", "dev"),
        source=SourceSpec(type=source_type),
        target=TargetSpec(type=target_type),
        **kwargs,
    )


def _explicit(nodes, edges, env="dev") -> DataProduct:
    return DataProduct(
        metadata=ProductMetadata(name="explicit-product"),
        pipeline=PipelineSpec(
            nodes=[PipelineNodeSpec(id=n[0], type=n[1]) for n in nodes],
            edges=[
                PipelineEdgeSpec(**{"from": e[0], "to": e[1], "operation": e[2]})
                for e in edges
            ],
        ),
    )


class TestIntentFormSqlserverToFabric:
    def setup_method(self):
        self.graph = IntentResolver().resolve(_intent("sqlserver", "fabric"))

    def test_has_sql_mi_node(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.SQL_MI in types

    def test_has_adf_node(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.ADF in types

    def test_has_adls_bronze_node(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.ADLS in types

    def test_has_databricks_node(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.DATABRICKS in types

    def test_has_fabric_lakehouse_node(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.FABRIC_LAKEHOUSE in types

    def test_has_key_vault_node(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.KEY_VAULT in types

    def test_databricks_reads_secrets(self):
        secret_edges = [e for e in self.graph.edges if e.operation == OperationType.SECRET_GET]
        assert len(secret_edges) >= 1
        principals = {e.source for e in secret_edges}
        dbx = next(n.id for n in self.graph.nodes if n.type == NodeType.DATABRICKS)
        assert dbx in principals

    def test_databricks_writes_to_fabric(self):
        fabric_id = next(n.id for n in self.graph.nodes if n.type == NodeType.FABRIC_LAKEHOUSE)
        write_to_fabric = [e for e in self.graph.edges if e.target == fabric_id and e.operation == OperationType.WRITE]
        assert len(write_to_fabric) >= 1

    def test_adf_triggers_databricks(self):
        dbx_id = next(n.id for n in self.graph.nodes if n.type == NodeType.DATABRICKS)
        trigger = [e for e in self.graph.edges if e.target == dbx_id and e.operation == OperationType.TRIGGER]
        assert len(trigger) == 1


class TestIntentFormEventhubToFabric:
    def setup_method(self):
        self.graph = IntentResolver().resolve(_intent("eventhub", "fabric", sla="realtime"))

    def test_has_eventhub_node(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.EVENTHUB in types

    def test_eventhub_uses_stream_edge(self):
        eh_id = next(n.id for n in self.graph.nodes if n.type == NodeType.EVENTHUB)
        stream_edges = [
            e for e in self.graph.edges
            if e.source == eh_id and e.operation == OperationType.STREAM
        ]
        assert len(stream_edges) == 1

    def test_has_databricks(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.DATABRICKS in types


class TestIntentFormAdlsToFabric:
    def setup_method(self):
        self.graph = IntentResolver().resolve(_intent("adls", "fabric"))

    def test_no_adf_node(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.ADF not in types

    def test_has_databricks(self):
        types = {n.type for n in self.graph.nodes}
        assert NodeType.DATABRICKS in types

    def test_databricks_writes_fabric(self):
        fabric_id = next(n.id for n in self.graph.nodes if n.type == NodeType.FABRIC_LAKEHOUSE)
        writes = [e for e in self.graph.edges if e.target == fabric_id and e.operation == OperationType.WRITE]
        assert len(writes) >= 1


class TestIntentFormAdlsToAdls:
    def setup_method(self):
        self.graph = IntentResolver().resolve(_intent("adls", "adls"))

    def test_no_adf(self):
        assert NodeType.ADF not in {n.type for n in self.graph.nodes}

    def test_has_databricks(self):
        assert NodeType.DATABRICKS in {n.type for n in self.graph.nodes}

    def test_has_at_least_two_adls_nodes(self):
        adls_count = sum(1 for n in self.graph.nodes if n.type == NodeType.ADLS)
        assert adls_count >= 2


class TestMetadataMapping:
    def test_environment_flows_to_metadata(self):
        graph = IntentResolver().resolve(_intent("sqlserver", "fabric", environment="prod"))
        assert graph.metadata.environment == "prod"

    def test_product_name_as_app_name(self):
        graph = IntentResolver().resolve(_intent("sqlserver", "fabric", product="my-platform"))
        assert graph.metadata.application_name == "my-platform"

    def test_node_ids_are_valid(self):
        graph = IntentResolver().resolve(_intent("sqlserver", "fabric"))
        for node in graph.nodes:
            assert node.id.replace("_", "").isalnum(), f"Invalid id: {node.id}"
            assert node.id[0].isalpha(), f"Id must start with letter: {node.id}"

    def test_no_duplicate_node_ids(self):
        graph = IntentResolver().resolve(_intent("eventhub", "fabric"))
        ids = [n.id for n in graph.nodes]
        assert len(ids) == len(set(ids))

    def test_env_region_from_environments(self):
        dp = DataProduct(
            metadata=ProductMetadata(name="test"),
            environments={
                "prod": EnvironmentSpec(
                    subscription_id="aaa",
                    region="westeurope",
                    resource_group="rg-test-prod",
                )
            },
            pipeline=PipelineSpec(
                nodes=[PipelineNodeSpec(id="src", type="adls")],
                edges=[],
            ),
        )
        graph = IntentResolver().resolve(dp, env="prod")
        assert graph.metadata.location == "westeurope"
        assert graph.metadata.resource_group == "rg-test-prod"


class TestExplicitFormResolution:
    def test_nodes_mapped_directly(self):
        dp = _explicit(
            nodes=[("raw", "adls"), ("transform", "databricks"), ("sink", "fabric_lakehouse")],
            edges=[("raw", "transform", "read"), ("transform", "sink", "write")],
        )
        graph = IntentResolver().resolve(dp)
        ids = {n.id for n in graph.nodes}
        assert ids == {"raw", "transform", "sink"}

    def test_edges_mapped_correctly(self):
        dp = _explicit(
            nodes=[("src", "adls"), ("dbx", "databricks")],
            edges=[("src", "dbx", "read")],
        )
        graph = IntentResolver().resolve(dp)
        assert len(graph.edges) == 1
        assert graph.edges[0].source == "src"
        assert graph.edges[0].target == "dbx"
        assert graph.edges[0].operation == OperationType.READ

    def test_unknown_node_type_raises(self):
        dp = _explicit(
            nodes=[("src", "unknown_type")],
            edges=[],
        )
        with pytest.raises(ValueError, match="Unknown node type"):
            IntentResolver().resolve(dp)
