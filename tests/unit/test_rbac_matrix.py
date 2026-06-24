"""Exhaustive tests for the RBAC matrix — every entry must be asserted."""

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.models.rbac import RbacKey
from dataforge.rbac.matrix import RBAC_MATRIX, lookup, needs_sql_login_warning


# ── lookup() — happy path, every matrix entry ─────────────────────────────

class TestLookupHappyPath:
    @pytest.mark.parametrize("principal,scope,operation,expected_role", [
        # ADF
        (NodeType.ADF, NodeType.ADLS, OperationType.READ, "Storage Blob Data Reader"),
        (NodeType.ADF, NodeType.ADLS, OperationType.WRITE, "Storage Blob Data Contributor"),
        (NodeType.ADF, NodeType.BLOB_STORAGE, OperationType.READ, "Storage Blob Data Reader"),
        (NodeType.ADF, NodeType.BLOB_STORAGE, OperationType.WRITE, "Storage Blob Data Contributor"),
        (NodeType.ADF, NodeType.DATABRICKS, OperationType.TRIGGER, "Contributor"),
        (NodeType.ADF, NodeType.KEY_VAULT, OperationType.SECRET_GET, "Key Vault Secrets User"),
        (NodeType.ADF, NodeType.FABRIC_LAKEHOUSE, OperationType.READ, "Storage Blob Data Reader"),
        (NodeType.ADF, NodeType.FABRIC_LAKEHOUSE, OperationType.WRITE, "Storage Blob Data Contributor"),
        (NodeType.ADF, NodeType.SQL_MI, OperationType.READ, "Reader"),
        (NodeType.ADF, NodeType.SQL_MI, OperationType.WRITE, "Contributor"),
        (NodeType.ADF, NodeType.EVENTHUB, OperationType.READ, "Azure Event Hubs Data Receiver"),
        # Databricks
        (NodeType.DATABRICKS, NodeType.ADLS, OperationType.READ, "Storage Blob Data Reader"),
        (NodeType.DATABRICKS, NodeType.ADLS, OperationType.WRITE, "Storage Blob Data Contributor"),
        (NodeType.DATABRICKS, NodeType.BLOB_STORAGE, OperationType.READ, "Storage Blob Data Reader"),
        (NodeType.DATABRICKS, NodeType.BLOB_STORAGE, OperationType.WRITE, "Storage Blob Data Contributor"),
        (NodeType.DATABRICKS, NodeType.FABRIC_LAKEHOUSE, OperationType.READ, "Storage Blob Data Reader"),
        (NodeType.DATABRICKS, NodeType.FABRIC_LAKEHOUSE, OperationType.WRITE, "Storage Blob Data Contributor"),
        (NodeType.DATABRICKS, NodeType.KEY_VAULT, OperationType.SECRET_GET, "Key Vault Secrets User"),
        (NodeType.DATABRICKS, NodeType.SQL_MI, OperationType.READ, "Reader"),
        (NodeType.DATABRICKS, NodeType.SQL_MI, OperationType.WRITE, "Contributor"),
        (NodeType.DATABRICKS, NodeType.ADF, OperationType.TRIGGER, "Contributor"),
        (NodeType.DATABRICKS, NodeType.EVENTHUB, OperationType.STREAM, "Azure Event Hubs Data Receiver"),
        # Fabric
        (NodeType.FABRIC_LAKEHOUSE, NodeType.ADLS, OperationType.READ, "Storage Blob Data Reader"),
        (NodeType.FABRIC_LAKEHOUSE, NodeType.ADLS, OperationType.WRITE, "Storage Blob Data Contributor"),
        (NodeType.FABRIC_LAKEHOUSE, NodeType.KEY_VAULT, OperationType.SECRET_GET, "Key Vault Secrets User"),
        (NodeType.FABRIC_LAKEHOUSE, NodeType.SQL_MI, OperationType.READ, "Reader"),
        # SQL MI as principal
        (NodeType.SQL_MI, NodeType.ADLS, OperationType.READ, "Storage Blob Data Reader"),
        (NodeType.SQL_MI, NodeType.ADLS, OperationType.WRITE, "Storage Blob Data Contributor"),
        (NodeType.SQL_MI, NodeType.KEY_VAULT, OperationType.SECRET_GET, "Key Vault Secrets User"),
    ])
    def test_role_present_in_result(self, principal, scope, operation, expected_role):
        roles = lookup(principal, scope, operation)
        assert expected_role in roles, (
            f"Expected '{expected_role}' for {principal}→[{operation}]→{scope}; got {roles}"
        )

    def test_all_matrix_keys_covered_by_parametrize(self):
        """Ensure no RBAC_MATRIX entry was silently skipped by the parametrize list."""
        assert len(RBAC_MATRIX) >= 29, (
            f"Matrix has {len(RBAC_MATRIX)} entries — fewer than expected. "
            "Was an entry accidentally removed?"
        )


# ── lookup() — miss / empty path ──────────────────────────────────────────

class TestLookupMiss:
    def test_unknown_combination_returns_empty_list(self):
        result = lookup(NodeType.KEY_VAULT, NodeType.ADF, OperationType.READ)
        assert result == []

    def test_adls_as_principal_not_in_matrix(self):
        result = lookup(NodeType.ADLS, NodeType.DATABRICKS, OperationType.WRITE)
        assert result == []

    def test_unknown_operation_returns_empty_list(self):
        result = lookup(NodeType.ADF, NodeType.ADLS, OperationType.STREAM)
        assert result == []

    def test_returns_new_list_not_mutable_reference(self):
        r1 = lookup(NodeType.ADF, NodeType.ADLS, OperationType.READ)
        r2 = lookup(NodeType.ADF, NodeType.ADLS, OperationType.READ)
        r1.append("MUTANT")
        assert "MUTANT" not in r2


# ── SQL MI warning detection ───────────────────────────────────────────────

class TestSqlMiWarning:
    @pytest.mark.parametrize("principal,scope,operation", [
        (NodeType.ADF, NodeType.SQL_MI, OperationType.READ),
        (NodeType.ADF, NodeType.SQL_MI, OperationType.WRITE),
        (NodeType.DATABRICKS, NodeType.SQL_MI, OperationType.READ),
        (NodeType.DATABRICKS, NodeType.SQL_MI, OperationType.WRITE),
        (NodeType.FABRIC_LAKEHOUSE, NodeType.SQL_MI, OperationType.READ),
    ])
    def test_needs_sql_login_warning(self, principal, scope, operation):
        assert needs_sql_login_warning(principal, scope, operation) is True

    def test_non_sql_mi_edge_no_warning(self):
        assert needs_sql_login_warning(NodeType.ADF, NodeType.ADLS, OperationType.READ) is False


# ── matrix integrity ──────────────────────────────────────────────────────

class TestMatrixIntegrity:
    def test_all_keys_are_rbac_key_instances(self):
        for key in RBAC_MATRIX:
            assert isinstance(key, RbacKey)

    def test_all_values_are_non_empty_lists(self):
        for key, roles in RBAC_MATRIX.items():
            assert isinstance(roles, list) and roles, f"Empty role list for {key}"

    def test_all_role_names_are_strings(self):
        for key, roles in RBAC_MATRIX.items():
            for role in roles:
                assert isinstance(role, str) and role.strip(), f"Blank role name for {key}"

    def test_no_duplicate_roles_per_key(self):
        for key, roles in RBAC_MATRIX.items():
            assert len(roles) == len(set(roles)), f"Duplicate roles for {key}: {roles}"
