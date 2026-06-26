"""Shared enumerations and constants used across every layer of DataForge.

This module is the single source of truth for the typed vocabulary (node types,
edge operations, principal types, data sensitivity levels) that the models,
parsing layer, RBAC resolver, and Jinja2 templates all import from. Keeping
them here avoids string literals scattered across the codebase.
"""

from enum import StrEnum


class NodeType(StrEnum):
    """All Azure resource types that can appear as nodes in a FlowGraph."""

    ADF = "adf"                        # Azure Data Factory
    DATABRICKS = "databricks"          # Azure Databricks workspace
    FABRIC_LAKEHOUSE = "fabric_lakehouse"  # Microsoft Fabric Lakehouse
    ADLS = "adls"                      # Azure Data Lake Storage Gen2
    KEY_VAULT = "key_vault"            # Azure Key Vault
    SQL_MI = "sql_mi"                  # Azure SQL Managed Instance
    EVENTHUB = "eventhub"             # Azure Event Hub (streaming source)
    BLOB_STORAGE = "blob_storage"     # Azure Blob Storage (non-hierarchical)
    AKS = "aks"                       # Azure Kubernetes Service (Spark workloads)


class OperationType(StrEnum):
    """The set of allowed edge operations, each mapped to a distinct RBAC role by the resolver."""

    READ = "read"
    WRITE = "write"
    TRIGGER = "trigger"        # orchestration hop (e.g. ADF triggers Databricks)
    SECRET_GET = "secret_get"  # read a secret / connection string from Key Vault
    CONNECT = "connect"        # data-plane DB connection (SQL MI)
    STREAM = "stream"          # real-time ingest from Event Hub


class PrincipalType(StrEnum):
    """Who holds the permission in a role assignment. Only managed identities are supported today."""

    MANAGED_IDENTITY = "managed_identity"
    SERVICE_PRINCIPAL = "service_principal"  # Phase 2


class DataSensitivity(StrEnum):
    """Data classification tier — influences network and key vault policy in generated Terraform."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


# Map node type → whether it acts as a principal (has a managed identity)
PRINCIPAL_NODE_TYPES: frozenset[NodeType] = frozenset({
    NodeType.ADF,
    NodeType.DATABRICKS,
    NodeType.FABRIC_LAKEHOUSE,
    NodeType.SQL_MI,
    NodeType.AKS,
})
