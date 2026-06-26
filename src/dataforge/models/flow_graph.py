"""FlowGraph — the central intermediate representation of DataForge.

After the parsing layer (IntentParser or IntentResolver) converts user input
into a FlowGraph, every downstream stage — the RBAC resolver and the Jinja2
renderer — consumes this object exclusively. Nothing downstream reads the
original NL description or YAML again.

FlowGraph is an immutable, validated DAG: Pydantic enforces field types and
required fields, while the model_validators enforce graph-level invariants
(unique node IDs, edge endpoints exist).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from dataforge.constants import DataSensitivity, NodeType, OperationType


class FlowNode(BaseModel):
    """A resource node in the data flow graph, representing one Azure service instance."""

    model_config = {"extra": "forbid", "frozen": True}

    id: str = Field(
        min_length=1,
        # The id constraint is stricter than a Python identifier: must start with a
        # lowercase letter so it is safe to embed directly in Terraform resource labels
        # without quoting or transformation.
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Stable, HCL-safe identifier (lowercase, underscores only)",
    )
    type: NodeType
    name: str = Field(min_length=1, description="Human-readable / Azure resource name")
    properties: dict[str, str] = Field(
        default_factory=dict,
        description="Type-specific config: format, container, runtime, cluster_size, etc.",
    )


class FlowEdge(BaseModel):
    """A directed relationship between two nodes that drives RBAC role assignment.

    The edge's operation determines which Azure built-in role the RBAC resolver
    assigns (e.g. read → Storage Blob Data Reader, write → Storage Blob Data Contributor).
    """

    model_config = {"extra": "forbid", "frozen": True}

    source: str = Field(description="FlowNode.id of the principal (data producer / caller)")
    target: str = Field(description="FlowNode.id of the scope (data consumer / callee)")
    operation: OperationType
    description: str | None = None

    @model_validator(mode="after")
    def _no_self_loop(self) -> FlowEdge:
        if self.source == self.target:
            raise ValueError("edge source and target must be different nodes")
        return self


class FlowMetadata(BaseModel):
    """Deployment context that flows through to every generated Terraform resource.

    Fields here become variables in the Jinja2 templates — they control resource
    group, location, environment suffix, and Azure tags on all emitted resources.
    """

    model_config = {"extra": "forbid"}

    original_prompt: str = Field(default="", description="Raw natural-language input from the user")
    location: str = Field(default="eastus", description="Azure region (e.g. eastus, westeurope)")
    resource_group: str = Field(default="rg-dataforge", description="Target Azure resource group")
    environment: str = Field(default="dev", description="Deployment environment: dev / test / prod")
    application_name: str = Field(default="dataforge", description="Short application label for naming")
    data_sensitivity: DataSensitivity = Field(default=DataSensitivity.INTERNAL)
    tags: dict[str, str] = Field(
        default_factory=lambda: {"managed-by": "dataforge", "environment": "dev"},
        description="Azure tags applied to all generated resources",
    )


class FlowGraph(BaseModel):
    """The complete, validated data flow graph passed between all pipeline stages.

    Invariants guaranteed after construction:
      - All node ids are unique.
      - Every edge source and target references an existing node id.
      - The graph is acyclic (enforced separately by graph_validator.validate_graph).
    """

    model_config = {"extra": "forbid"}

    nodes: list[FlowNode] = Field(min_length=1)
    edges: list[FlowEdge] = Field(default_factory=list)
    metadata: FlowMetadata

    # ------------------------------------------------------------------ validators

    @field_validator("nodes")
    @classmethod
    def _unique_ids(cls, nodes: list[FlowNode]) -> list[FlowNode]:
        ids = [n.id for n in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        return nodes

    @model_validator(mode="after")
    def _edges_reference_known_nodes(self) -> FlowGraph:
        ids = {n.id for n in self.nodes}
        for edge in self.edges:
            if edge.source not in ids:
                raise ValueError(f"edge source '{edge.source}' not found in nodes")
            if edge.target not in ids:
                raise ValueError(f"edge target '{edge.target}' not found in nodes")
        return self

    # ------------------------------------------------------------------ helpers

    def node(self, node_id: str) -> FlowNode:
        """Look up a node by id; raises KeyError if not found."""
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"node '{node_id}' not in graph")

    def nodes_of_type(self, node_type: NodeType) -> list[FlowNode]:
        return [n for n in self.nodes if n.type == node_type]

    def edges_from(self, node_id: str) -> list[FlowEdge]:
        return [e for e in self.edges if e.source == node_id]

    def edges_to(self, node_id: str) -> list[FlowEdge]:
        return [e for e in self.edges if e.target == node_id]
