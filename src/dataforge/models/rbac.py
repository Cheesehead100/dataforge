"""RBAC result models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from dataforge.constants import NodeType, OperationType, PrincipalType


class RbacKey(BaseModel):
    """The lookup key into the RBAC matrix."""

    model_config = {"frozen": True}

    principal_node_type: NodeType
    scope_node_type: NodeType
    operation: OperationType

    def __str__(self) -> str:
        return f"{self.principal_node_type} →[{self.operation}]→ {self.scope_node_type}"


class RoleAssignment(BaseModel):
    """A single Azure RBAC role assignment to emit in Terraform."""

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
        """Stable Terraform for_each key: principal__scope__role_slug."""
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
