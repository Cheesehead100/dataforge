"""Resolves a FlowGraph into the complete set of RBAC role assignments."""

from __future__ import annotations

from dataforge.constants import PRINCIPAL_NODE_TYPES, NodeType
from dataforge.models.flow_graph import FlowEdge, FlowGraph
from dataforge.models.rbac import RbacKey, RbacResult, RoleAssignment
from dataforge.rbac.matrix import lookup, needs_sql_login_warning
from dataforge.rbac.roles import role_definition_id


class RbacResolver:
    """Converts a FlowGraph into a deduplicated list of Azure RBAC role assignments.

    The output is fully deterministic — no AI calls are made. The result is used
    directly to render rbac.tf without any LLM polish step.
    """

    def resolve(self, graph: FlowGraph) -> RbacResult:
        assignments: dict[str, RoleAssignment] = {}
        unresolved: list[RbacKey] = []
        warnings: list[str] = []

        for edge in graph.edges:
            principal_node = graph.node(edge.source)
            scope_node = graph.node(edge.target)

            if principal_node.type not in PRINCIPAL_NODE_TYPES:
                # Source cannot hold a managed identity; skip silently.
                continue

            new_assignments, new_unresolved, new_warnings = self._edge_to_assignments(
                graph, edge, principal_node.type, scope_node.type
            )

            for ra in new_assignments:
                key = ra.terraform_key
                if key not in assignments:
                    assignments[key] = ra

            unresolved.extend(new_unresolved)
            warnings.extend(new_warnings)

        return RbacResult(
            assignments=list(assignments.values()),
            unresolved=unresolved,
            warnings=warnings,
        )

    def _edge_to_assignments(
        self,
        graph: FlowGraph,
        edge: FlowEdge,
        principal_type: NodeType,
        scope_type: NodeType,
    ) -> tuple[list[RoleAssignment], list[RbacKey], list[str]]:
        role_names = lookup(principal_type, scope_type, edge.operation)
        key = RbacKey(
            principal_node_type=principal_type,
            scope_node_type=scope_type,
            operation=edge.operation,
        )

        if not role_names:
            return [], [key], []

        warnings: list[str] = []

        if needs_sql_login_warning(principal_type, scope_type, edge.operation):
            warnings.append(
                f"Edge {edge.source} →[{edge.operation}]→ {edge.target} requires a "
                f"SQL Managed Instance data-plane login "
                f"('CREATE USER [{edge.source}] FROM EXTERNAL PROVIDER' in SQL MI). "
                f"This cannot be expressed as an Azure RBAC role assignment and must be "
                f"applied separately. See the generated sql_mi_logins.md for instructions."
            )

        assignments = [
            RoleAssignment(
                principal_node_id=edge.source,
                scope_node_id=edge.target,
                scope_node_type=scope_type,
                role_name=role_name,
                role_definition_id=role_definition_id(role_name),
                operation=edge.operation,
                rationale=self._rationale(edge.source, edge.target, role_name, edge.operation),
            )
            for role_name in role_names
        ]

        return assignments, [], warnings

    @staticmethod
    def _rationale(
        principal_id: str,
        scope_id: str,
        role_name: str,
        operation: str,
    ) -> str:
        return f"{principal_id} managed identity needs {role_name} on {scope_id} for {operation}"
