"""
RBAC Matrix — static lookup table: (principal, scope, operation) → Azure role names.

This is the authoritative source of truth for which Azure built-in roles DataForge
assigns.  Every (NodeType principal, NodeType scope, OperationType) triple that can
appear in a generated FlowGraph must have an entry here; unrecognised triples fall
through to RbacResult.unresolved warnings rather than hard errors, so adding new
node types is safe as long as this table is updated in parallel.  The resolver
consumes this table deterministically — no LLM is involved in role selection.
"""

from __future__ import annotations

from dataforge.constants import NodeType, OperationType
from dataforge.models.rbac import RbacKey

# ---------------------------------------------------------------------------
# The matrix: key → list of role names (from roles.py catalog)
# ---------------------------------------------------------------------------
# Design rules:
#  - Principal is always the node whose managed identity receives the role.
#  - Scope is the target resource the principal acts on.
#  - SQL MI data-plane logins (CREATE USER FROM EXTERNAL PROVIDER) are NOT
#    expressible as RBAC assignments; those edges emit a control-plane role
#    PLUS a warning. Rows marked [+warn] follow this convention.
#  - Fabric Lakehouse is modelled via its OneLake ADLS-Gen2-compatible endpoint
#    (Storage Blob roles). Native Fabric workspace roles are a Phase 2 concern.
# ---------------------------------------------------------------------------

RBAC_MATRIX: dict[RbacKey, list[str]] = {

    # ── ADF as principal ──────────────────────────────────────────────────
    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.BLOB_STORAGE,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.BLOB_STORAGE,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.DATABRICKS,
        operation=OperationType.TRIGGER,
    ): ["Contributor"],

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.KEY_VAULT,
        operation=OperationType.SECRET_GET,
    ): ["Key Vault Secrets User"],

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.FABRIC_LAKEHOUSE,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.FABRIC_LAKEHOUSE,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    # ADF → SQL MI: control-plane Reader; data-plane login emitted as warning
    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.SQL_MI,
        operation=OperationType.READ,
    ): ["Reader"],  # +warn: CREATE USER FROM EXTERNAL PROVIDER in SQL MI

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.SQL_MI,
        operation=OperationType.WRITE,
    ): ["Contributor"],  # +warn: data-plane SQL login also required

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.EVENTHUB,
        operation=OperationType.READ,
    ): ["Azure Event Hubs Data Receiver"],

    RbacKey(
        principal_node_type=NodeType.ADF,
        scope_node_type=NodeType.EVENTHUB,
        operation=OperationType.WRITE,
    ): ["Azure Event Hubs Data Sender"],

    # ── Databricks as principal ───────────────────────────────────────────
    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.BLOB_STORAGE,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.BLOB_STORAGE,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.FABRIC_LAKEHOUSE,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.FABRIC_LAKEHOUSE,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.KEY_VAULT,
        operation=OperationType.SECRET_GET,
    ): ["Key Vault Secrets User"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.SQL_MI,
        operation=OperationType.READ,
    ): ["Reader"],  # +warn: data-plane SQL login

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.SQL_MI,
        operation=OperationType.WRITE,
    ): ["Contributor"],  # +warn: data-plane SQL login

    # Databricks → ADF reverse orchestration (Databricks triggers ADF pipeline)
    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.ADF,
        operation=OperationType.TRIGGER,
    ): ["Contributor"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.EVENTHUB,
        operation=OperationType.STREAM,
    ): ["Azure Event Hubs Data Receiver"],

    RbacKey(
        principal_node_type=NodeType.DATABRICKS,
        scope_node_type=NodeType.EVENTHUB,
        operation=OperationType.WRITE,
    ): ["Azure Event Hubs Data Sender"],

    # ── Fabric Lakehouse as principal ─────────────────────────────────────
    RbacKey(
        principal_node_type=NodeType.FABRIC_LAKEHOUSE,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.FABRIC_LAKEHOUSE,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    RbacKey(
        principal_node_type=NodeType.FABRIC_LAKEHOUSE,
        scope_node_type=NodeType.KEY_VAULT,
        operation=OperationType.SECRET_GET,
    ): ["Key Vault Secrets User"],

    RbacKey(
        principal_node_type=NodeType.FABRIC_LAKEHOUSE,
        scope_node_type=NodeType.SQL_MI,
        operation=OperationType.READ,
    ): ["Reader"],  # +warn: data-plane SQL login

    # ── AKS as principal ──────────────────────────────────────────────────
    RbacKey(
        principal_node_type=NodeType.AKS,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.AKS,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    RbacKey(
        principal_node_type=NodeType.AKS,
        scope_node_type=NodeType.KEY_VAULT,
        operation=OperationType.SECRET_GET,
    ): ["Key Vault Secrets User"],

    RbacKey(
        principal_node_type=NodeType.AKS,
        scope_node_type=NodeType.EVENTHUB,
        operation=OperationType.STREAM,
    ): ["Azure Event Hubs Data Receiver"],

    RbacKey(
        principal_node_type=NodeType.AKS,
        scope_node_type=NodeType.EVENTHUB,
        operation=OperationType.WRITE,
    ): ["Azure Event Hubs Data Sender"],

    # ── SQL MI as principal ───────────────────────────────────────────────
    RbacKey(
        principal_node_type=NodeType.SQL_MI,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.READ,
    ): ["Storage Blob Data Reader"],

    RbacKey(
        principal_node_type=NodeType.SQL_MI,
        scope_node_type=NodeType.ADLS,
        operation=OperationType.WRITE,
    ): ["Storage Blob Data Contributor"],

    RbacKey(
        principal_node_type=NodeType.SQL_MI,
        scope_node_type=NodeType.KEY_VAULT,
        operation=OperationType.SECRET_GET,
    ): ["Key Vault Secrets User"],
}

# Edge triples involving SQL MI as SCOPE that also need a data-plane login warning
SQL_MI_SCOPE_WARN: frozenset[RbacKey] = frozenset({
    k for k in RBAC_MATRIX if k.scope_node_type == NodeType.SQL_MI
})


def lookup(
    principal: NodeType,
    scope: NodeType,
    operation: OperationType,
) -> list[str]:
    """Return the list of role names for this (principal, scope, operation) triple.

    Returns an empty list for unrecognised combinations — the resolver converts
    those into RbacResult.unresolved warnings rather than hard errors.
    """
    key = RbacKey(
        principal_node_type=principal,
        scope_node_type=scope,
        operation=operation,
    )
    return list(RBAC_MATRIX.get(key, []))


def needs_sql_login_warning(principal: NodeType, scope: NodeType, operation: OperationType) -> bool:
    """Return True when this edge requires a data-plane SQL login that can't be expressed in RBAC."""
    key = RbacKey(principal_node_type=principal, scope_node_type=scope, operation=operation)
    return key in SQL_MI_SCOPE_WARN
