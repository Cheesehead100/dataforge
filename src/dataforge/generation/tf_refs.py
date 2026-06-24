"""Deterministic Terraform reference expressions for FlowNode principal and scope IDs.

These functions map a FlowNode to the azurerm expression that resolves its
managed-identity principal_id (for RBAC assignments) or its resource ID (scope).
They are injected into the Jinja2 context so rbac.tf.j2 emits valid HCL instead
of TODO placeholders.
"""

from __future__ import annotations

from dataforge.constants import NodeType
from dataforge.models.flow_graph import FlowNode


def principal_tf_ref(node: FlowNode) -> str:
    """Return the Terraform expression for a node's managed-identity principal_id."""
    match node.type:
        case NodeType.ADF:
            return f"azurerm_data_factory.{node.id}.identity[0].principal_id"
        case NodeType.DATABRICKS:
            # Standard workspaces have no resolvable cluster MSI at provision time.
            # Operator supplies the service principal object ID created in Databricks.
            return f"var.{node.id}_sp_object_id"
        case NodeType.FABRIC_LAKEHOUSE:
            return f"var.{node.id}_sp_object_id"
        case NodeType.SQL_MI:
            return f"azurerm_mssql_managed_instance.{node.id}.identity[0].principal_id"
        case NodeType.AKS:
            # Workload Identity UAMI — pod-level access via OIDC federated credential.
            return f"azurerm_user_assigned_identity.{node.id}_workload.principal_id"
        case _:
            raise ValueError(
                f"Node type '{node.type}' is not in PRINCIPAL_NODE_TYPES — "
                "cannot generate principal_id reference."
            )


def scope_tf_ref(node: FlowNode) -> str:
    """Return the Terraform expression for a node's Azure resource ID (RBAC scope)."""
    match node.type:
        case NodeType.ADLS | NodeType.BLOB_STORAGE:
            return f"azurerm_storage_account.{node.id}.id"
        case NodeType.DATABRICKS:
            return f"azurerm_databricks_workspace.{node.id}.id"
        case NodeType.KEY_VAULT:
            return f"azurerm_key_vault.{node.id}.id"
        case NodeType.EVENTHUB:
            return f"azurerm_eventhub_namespace.{node.id}.id"
        case NodeType.SQL_MI:
            return f"azurerm_mssql_managed_instance.{node.id}.id"
        case NodeType.ADF:
            return f"azurerm_data_factory.{node.id}.id"
        case NodeType.FABRIC_LAKEHOUSE:
            # Fabric workspace IDs are not resolvable via azurerm; supplied as variable.
            return f"var.{node.id}_workspace_id"
        case NodeType.AKS:
            return f"azurerm_kubernetes_cluster.{node.id}.id"
        case _:
            raise ValueError(
                f"No scope_tf_ref mapping for node type '{node.type}'. "
                "Add an entry to tf_refs.py."
            )
