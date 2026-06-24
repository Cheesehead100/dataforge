"""Unit tests for the tf_refs helper functions."""

import pytest

from dataforge.constants import NodeType
from dataforge.generation.tf_refs import principal_tf_ref, scope_tf_ref
from dataforge.models.flow_graph import FlowNode


def _node(node_id: str, node_type: NodeType) -> FlowNode:
    return FlowNode(id=node_id, type=node_type, name=f"Test {node_id}")


class TestPrincipalTfRef:
    def test_adf(self):
        node = _node("my_adf", NodeType.ADF)
        assert principal_tf_ref(node) == "azurerm_data_factory.my_adf.identity[0].principal_id"

    def test_databricks_uses_sp_variable(self):
        node = _node("dbw", NodeType.DATABRICKS)
        assert principal_tf_ref(node) == "var.dbw_sp_object_id"

    def test_fabric_uses_sp_variable(self):
        node = _node("lh", NodeType.FABRIC_LAKEHOUSE)
        assert principal_tf_ref(node) == "var.lh_sp_object_id"

    def test_sql_mi(self):
        node = _node("sqlmi", NodeType.SQL_MI)
        assert principal_tf_ref(node) == "azurerm_mssql_managed_instance.sqlmi.identity[0].principal_id"

    def test_aks_kubelet_identity(self):
        node = _node("aks1", NodeType.AKS)
        assert principal_tf_ref(node) == "azurerm_kubernetes_cluster.aks1.kubelet_identity[0].object_id"

    def test_non_principal_raises(self):
        node = _node("adls1", NodeType.ADLS)
        with pytest.raises(ValueError, match="PRINCIPAL_NODE_TYPES"):
            principal_tf_ref(node)


class TestScopeTfRef:
    def test_adls(self):
        node = _node("raw_adls", NodeType.ADLS)
        assert scope_tf_ref(node) == "azurerm_storage_account.raw_adls.id"

    def test_blob_storage(self):
        node = _node("blob1", NodeType.BLOB_STORAGE)
        assert scope_tf_ref(node) == "azurerm_storage_account.blob1.id"

    def test_databricks(self):
        node = _node("dbw", NodeType.DATABRICKS)
        assert scope_tf_ref(node) == "azurerm_databricks_workspace.dbw.id"

    def test_key_vault(self):
        node = _node("kv1", NodeType.KEY_VAULT)
        assert scope_tf_ref(node) == "azurerm_key_vault.kv1.id"

    def test_eventhub(self):
        node = _node("evh1", NodeType.EVENTHUB)
        assert scope_tf_ref(node) == "azurerm_eventhub_namespace.evh1.id"

    def test_sql_mi(self):
        node = _node("sqlmi", NodeType.SQL_MI)
        assert scope_tf_ref(node) == "azurerm_mssql_managed_instance.sqlmi.id"

    def test_adf(self):
        node = _node("adf1", NodeType.ADF)
        assert scope_tf_ref(node) == "azurerm_data_factory.adf1.id"

    def test_fabric_uses_variable(self):
        node = _node("lh", NodeType.FABRIC_LAKEHOUSE)
        assert scope_tf_ref(node) == "var.lh_workspace_id"

    def test_aks(self):
        node = _node("aks1", NodeType.AKS)
        assert scope_tf_ref(node) == "azurerm_kubernetes_cluster.aks1.id"
