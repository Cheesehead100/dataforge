"""Tests for AKS Workload Identity — tf_refs and template rendering."""

import pytest
from dataforge.constants import NodeType
from dataforge.models.flow_graph import FlowNode
from dataforge.generation.tf_refs import principal_tf_ref, scope_tf_ref


def _aks_node(node_id: str = "aks1") -> FlowNode:
    return FlowNode(id=node_id, name="My AKS", type=NodeType.AKS)


# ── principal_tf_ref uses UAMI, not kubelet identity ─────────────────────────

def test_aks_principal_ref_is_uami():
    node = _aks_node("aks1")
    ref = principal_tf_ref(node)
    assert "user_assigned_identity" in ref
    assert "aks1_workload" in ref
    assert "principal_id" in ref


def test_aks_principal_ref_does_not_use_kubelet():
    node = _aks_node("aks1")
    ref = principal_tf_ref(node)
    assert "kubelet_identity" not in ref


def test_aks_principal_ref_uses_node_id():
    node = _aks_node("my_cluster")
    ref = principal_tf_ref(node)
    assert "my_cluster_workload" in ref


# ── scope_tf_ref unchanged ────────────────────────────────────────────────────

def test_aks_scope_ref_is_cluster_id():
    node = _aks_node("aks1")
    ref = scope_tf_ref(node)
    assert ref == "azurerm_kubernetes_cluster.aks1.id"


# ── Renderer produces UAMI + federated credential ────────────────────────────

def test_aks_template_includes_uami(aks_graph, rbac_result):
    from dataforge.generation.renderer import Renderer
    renderer = Renderer()
    files = renderer.render_all(aks_graph, rbac_result)
    aks_tf = next(f for f in files if f.filename == "aks.tf")
    assert "azurerm_user_assigned_identity" in aks_tf.content
    assert "_workload" in aks_tf.content


def test_aks_template_includes_federated_credential(aks_graph, rbac_result):
    from dataforge.generation.renderer import Renderer
    renderer = Renderer()
    files = renderer.render_all(aks_graph, rbac_result)
    aks_tf = next(f for f in files if f.filename == "aks.tf")
    assert "azurerm_federated_identity_credential" in aks_tf.content
    assert "oidc_issuer_url" in aks_tf.content


def test_aks_template_workload_identity_enabled(aks_graph, rbac_result):
    from dataforge.generation.renderer import Renderer
    renderer = Renderer()
    files = renderer.render_all(aks_graph, rbac_result)
    aks_tf = next(f for f in files if f.filename == "aks.tf")
    assert "workload_identity_enabled = true" in aks_tf.content
    assert "oidc_issuer_enabled       = true" in aks_tf.content


def test_variables_tf_includes_aks_workload_vars(aks_graph, rbac_result):
    from dataforge.generation.renderer import Renderer
    renderer = Renderer()
    files = renderer.render_all(aks_graph, rbac_result)
    vars_tf = next(f for f in files if f.filename == "variables.tf")
    assert "_workload_namespace" in vars_tf.content
    assert "_workload_service_account" in vars_tf.content


def test_outputs_tf_includes_workload_client_id(aks_graph, rbac_result):
    from dataforge.generation.renderer import Renderer
    renderer = Renderer()
    files = renderer.render_all(aks_graph, rbac_result)
    outputs_tf = next(f for f in files if f.filename == "outputs.tf")
    assert "_workload_client_id" in outputs_tf.content
    assert "_workload_principal_id" in outputs_tf.content


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def aks_graph():
    from dataforge.models.flow_graph import FlowGraph, FlowMetadata
    from dataforge.models.flow_graph import FlowNode
    meta = FlowMetadata(
        environment="dev", location="eastus",
        resource_group="rg-test", application_name="dftest",
    )
    nodes = [FlowNode(id="aks1", name="MyAKS", type=NodeType.AKS)]
    return FlowGraph(nodes=nodes, edges=[], metadata=meta)


@pytest.fixture()
def rbac_result():
    from dataforge.models.rbac import RbacResult
    return RbacResult(assignments=[], warnings=[], unresolved=[])
