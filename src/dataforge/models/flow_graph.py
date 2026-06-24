"""Core data model: the directed graph that represents a data flow."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from dataforge.constants import DataSensitivity, NodeType, OperationType


class FlowNode(BaseModel):
    """A resource node in the data flow graph."""

    model_config = {"extra": "forbid", "frozen": True}

    id: str = Field(
        min_length=1,
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
    """A directed data movement between two nodes."""

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
    """Environment and deployment metadata."""

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
    """The complete data flow graph: nodes, edges, and deployment metadata."""

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
