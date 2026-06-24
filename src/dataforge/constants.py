"""Shared enumerations — the vocabulary the entire system uses."""

from enum import StrEnum


class NodeType(StrEnum):
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
    READ = "read"
    WRITE = "write"
    TRIGGER = "trigger"        # orchestration hop (e.g. ADF triggers Databricks)
    SECRET_GET = "secret_get"  # read a secret / connection string from Key Vault
    CONNECT = "connect"        # data-plane DB connection (SQL MI)
    STREAM = "stream"          # real-time ingest from Event Hub


class PrincipalType(StrEnum):
    MANAGED_IDENTITY = "managed_identity"
    SERVICE_PRINCIPAL = "service_principal"  # Phase 2


class DataSensitivity(StrEnum):
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
