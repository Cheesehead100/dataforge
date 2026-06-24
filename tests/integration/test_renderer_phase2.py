"""Phase 2 integration tests — validates template rendering produces real Terraform refs."""

from __future__ import annotations

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.generation.hcl_generator import HclGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode
from dataforge.models.rbac import RbacResult
from dataforge.rbac.resolver import RbacResolver


# ── shared helpers ────────────────────────────────────────────────────────────

def _meta(**kwargs) -> FlowMetadata:
    return FlowMetadata(
        original_prompt="test",
        location="eastus",
        resource_group="rg-test",
        environment="dev",
        application_name="testapp",
        **kwargs,
    )


def _render(graph: FlowGraph) -> dict[str, str]:
    """Return {filename: content} for all files rendered from graph."""
    rbac = RbacResolver().resolve(graph)
    files = Renderer().render_all(graph, rbac)
    return {f.filename: f.content for f in files}


# ── no TODO_REPLACE strings survive in any generated file ─────────────────────

class TestNoPlaceholders:
    def test_adf_adls_graph_no_todos(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adf1", type=NodeType.ADF, name="adf"),
                FlowNode(id="adls1", type=NodeType.ADLS, name="storage"),
            ],
            edges=[FlowEdge(source="adf1", target="adls1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        for filename, content in files.items():
            assert "TODO_REPLACE" not in content, (
                f"{filename} still contains TODO_REPLACE placeholder"
            )

    def test_full_stack_graph_no_todos(self, adls_to_databricks_to_fabric_graph):
        files = _render(adls_to_databricks_to_fabric_graph)
        for filename, content in files.items():
            assert "TODO_REPLACE" not in content, (
                f"{filename} still contains TODO_REPLACE placeholder"
            )

    def test_aks_eventhub_graph_no_todos(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="evh1", type=NodeType.EVENTHUB, name="event-hub"),
                FlowNode(id="aks1", type=NodeType.AKS, name="aks-spark"),
                FlowNode(id="adls1", type=NodeType.ADLS, name="storage"),
            ],
            edges=[
                FlowEdge(source="aks1", target="evh1", operation=OperationType.STREAM),
                FlowEdge(source="aks1", target="adls1", operation=OperationType.WRITE),
            ],
            metadata=_meta(),
        )
        files = _render(graph)
        for filename, content in files.items():
            assert "TODO_REPLACE" not in content, (
                f"{filename} still contains TODO_REPLACE placeholder"
            )


# ── rbac.tf uses real Terraform expressions ───────────────────────────────────

class TestRbacRefs:
    def test_adf_principal_uses_identity_ref(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adf1", type=NodeType.ADF, name="adf"),
                FlowNode(id="adls1", type=NodeType.ADLS, name="storage"),
            ],
            edges=[FlowEdge(source="adf1", target="adls1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        rbac = files["rbac.tf"]
        assert "azurerm_data_factory.adf1.identity[0].principal_id" in rbac
        assert "azurerm_storage_account.adls1.id" in rbac

    def test_databricks_principal_uses_sp_variable(self, adls_to_databricks_to_fabric_graph):
        files = _render(adls_to_databricks_to_fabric_graph)
        rbac = files["rbac.tf"]
        assert "var.dbw_transform_sp_object_id" in rbac

    def test_aks_principal_uses_workload_identity_uami(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="aks1", type=NodeType.AKS, name="aks"),
                FlowNode(id="adls1", type=NodeType.ADLS, name="storage"),
            ],
            edges=[FlowEdge(source="aks1", target="adls1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        assert "azurerm_user_assigned_identity.aks1_workload.principal_id" in files["rbac.tf"]


# ── networking.tf rendered iff Databricks present ─────────────────────────────

class TestNetworkingTemplate:
    def test_networking_generated_when_databricks_present(self, adls_to_databricks_to_fabric_graph):
        files = _render(adls_to_databricks_to_fabric_graph)
        assert "networking.tf" in files

    def test_networking_absent_when_no_databricks(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adf1", type=NodeType.ADF, name="adf"),
                FlowNode(id="adls1", type=NodeType.ADLS, name="storage"),
            ],
            edges=[FlowEdge(source="adf1", target="adls1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        assert "networking.tf" not in files

    def test_networking_has_eventhub_9093_rule(self, adls_to_databricks_to_fabric_graph):
        files = _render(adls_to_databricks_to_fabric_graph)
        net = files["networking.tf"]
        assert "databricks-worker-to-eventhub" in net
        assert "9093" in net
        assert "EventHub" in net

    def test_networking_has_both_nsg_associations(self, adls_to_databricks_to_fabric_graph):
        files = _render(adls_to_databricks_to_fabric_graph)
        net = files["networking.tf"]
        assert "azurerm_subnet_network_security_group_association" in net
        assert "dbw_private" in net
        assert "dbw_public" in net

    def test_databricks_tf_references_generated_networking(self, adls_to_databricks_to_fabric_graph):
        files = _render(adls_to_databricks_to_fabric_graph)
        dbw = files["databricks.tf"]
        assert "azurerm_virtual_network.dataforge.id" in dbw
        assert "azurerm_subnet_network_security_group_association.dbw_private.id" in dbw
        assert "TODO_REPLACE" not in dbw


# ── new node type templates rendered correctly ────────────────────────────────

class TestNewNodeTemplates:
    def test_eventhub_tf_generated(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="evh1", type=NodeType.EVENTHUB, name="event-hub"),
                FlowNode(id="adf1", type=NodeType.ADF, name="adf"),
            ],
            edges=[FlowEdge(source="adf1", target="evh1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        assert "eventhub.tf" in files
        evh = files["eventhub.tf"]
        assert "azurerm_eventhub_namespace" in evh
        assert "azurerm_eventhub" in evh
        assert "azurerm_eventhub_consumer_group" in evh

    def test_aks_tf_generated(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="aks1", type=NodeType.AKS, name="aks-spark"),
                FlowNode(id="adls1", type=NodeType.ADLS, name="storage"),
            ],
            edges=[FlowEdge(source="aks1", target="adls1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        assert "aks.tf" in files
        aks = files["aks.tf"]
        assert "azurerm_kubernetes_cluster" in aks
        assert "azurerm_kubernetes_cluster_node_pool" in aks
        assert "workload_identity_enabled" in aks
        assert "oidc_issuer_enabled" in aks
        assert "spark" in aks


# ── variables.tf SP variables emitted correctly ───────────────────────────────

class TestSpVariables:
    def test_databricks_sp_variable_emitted(self, adls_to_databricks_to_fabric_graph):
        files = _render(adls_to_databricks_to_fabric_graph)
        variables = files["variables.tf"]
        assert "dbw_transform_sp_object_id" in variables

    def test_fabric_sp_and_workspace_id_variables_emitted(self, adls_to_databricks_to_fabric_graph):
        files = _render(adls_to_databricks_to_fabric_graph)
        variables = files["variables.tf"]
        assert "fabric_lh_sp_object_id" in variables
        assert "fabric_lh_workspace_id" in variables

    def test_no_sp_variables_when_only_adf(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="adf1", type=NodeType.ADF, name="adf"),
                FlowNode(id="adls1", type=NodeType.ADLS, name="storage"),
            ],
            edges=[FlowEdge(source="adf1", target="adls1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        variables = files["variables.tf"]
        assert "_sp_object_id" not in variables


# ── outputs.tf ────────────────────────────────────────────────────────────────

class TestOutputs:
    def test_eventhub_outputs_present(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="evh1", type=NodeType.EVENTHUB, name="event-hub"),
                FlowNode(id="adf1", type=NodeType.ADF, name="adf"),
            ],
            edges=[FlowEdge(source="adf1", target="evh1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        outputs = files["outputs.tf"]
        assert "evh1_namespace_id" in outputs
        assert "evh1_eventhub_name" in outputs

    def test_aks_outputs_present(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="aks1", type=NodeType.AKS, name="aks"),
                FlowNode(id="adls1", type=NodeType.ADLS, name="storage"),
            ],
            edges=[FlowEdge(source="aks1", target="adls1", operation=OperationType.READ)],
            metadata=_meta(),
        )
        files = _render(graph)
        outputs = files["outputs.tf"]
        assert "aks1_oidc_issuer_url" in outputs
        assert "aks1_workload_client_id" in outputs
        assert "aks1_workload_principal_id" in outputs
