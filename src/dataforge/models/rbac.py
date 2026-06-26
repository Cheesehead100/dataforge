"""RBAC result models produced by the rbac/resolver layer.

The RBAC resolver walks every FlowEdge in a FlowGraph, looks up the
(principal_node_type, scope_node_type, operation) triple in a built-in role
matrix, and produces a RbacResult. The renderer then emits one
azurerm_role_assignment Terraform resource per RoleAssignment.

Edges whose triple has no matrix entry go into RbacResult.unresolved and are
surfaced as warnings rather than hard errors — this keeps generation working
for novel resource combinations even when RBAC coverage is incomplete.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dataforge.constants import NodeType, OperationType, PrincipalType


class RbacKey(BaseModel):
    """The lookup key into the RBAC role matrix: (who, what, how) → built-in role."""

    model_config = {"frozen": True}

    principal_node_type: NodeType
    scope_node_type: NodeType
    operation: OperationType

    def __str__(self) -> str:
        return f"{self.principal_node_type} →[{self.operation}]→ {self.scope_node_type}"


class RoleAssignment(BaseModel):
    """One azurerm_role_assignment resource to be emitted by the Terraform renderer."""

    principal_node_id: str = Field(description="FlowNode.id whose managed identity is the principal")
    principal_type: PrincipalType = PrincipalType.MANAGED_IDENTITY
    scope_node_id: str = Field(description="FlowNode.id whose resource is the scope")
    scope_node_type: NodeType
    role_name: str = Field(description="Azure built-in role display name")
    role_definition_id: str = Field(description="Azure built-in role GUID")
    operation: OperationType = Field(description="The edge operation that produced this assignment")
    rationale: str = Field(description="Human-readable explanation shown in code comment")

    @property
    def terraform_key(self) -> str:
        """Stable Terraform for_each key: principal__scope__role_slug.

        The double-underscore separator avoids collisions because node IDs
        use single underscores and role names use spaces (lowered to underscores).
        Stability matters: changing this key in a plan would destroy and re-create
        existing role assignments, briefly removing access.
        """
        role_slug = self.role_name.lower().replace(" ", "_")
        return f"{self.principal_node_id}__{self.scope_node_id}__{role_slug}"


class RbacResult(BaseModel):
    """Output of the RBAC resolver: all assignments + any unresolved edges."""

    assignments: list[RoleAssignment] = Field(default_factory=list)
    unresolved: list[RbacKey] = Field(
        default_factory=list,
        description="Edge triples with no matrix entry — emitted as warnings",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Human-readable notices (e.g. SQL MI data-plane login not expressible in RBAC)",
    )
