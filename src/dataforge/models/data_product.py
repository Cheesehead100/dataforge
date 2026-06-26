"""DataProduct — the schema for the data-product.yaml config file.

This is the YAML-first entry point into DataForge. A data-product.yaml is
parsed by YamlParser into a DataProduct, then converted into a FlowGraph by
IntentResolver (no LLM involved). Two authoring styles are supported and are
mutually exclusive:

  Intent form   — describe source + target; IntentResolver builds a canonical
                  pipeline with ADF → ADLS → Databricks → target automatically.
  Explicit form — author the full pipeline.nodes/edges list when you need
                  fine-grained control over the graph topology.

Optional sections (compute, storage, governance, etc.) use extra="allow" so
future spec loops can introduce new keys without breaking existing parsers.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataforge.constants import DataSensitivity


class SourceSpec(BaseModel):
    """The data source for intent-form pipelines. extra='allow' accepts provider-specific keys."""

    model_config = ConfigDict(extra="allow")
    type: str


class TargetSpec(BaseModel):
    """The data target for intent-form pipelines. extra='allow' accepts provider-specific keys."""

    model_config = ConfigDict(extra="allow")
    type: str


class ClassificationSpec(BaseModel):
    pii: bool = False


class RetentionSpec(BaseModel):
    """Data retention in days per medallion layer. Defaults follow a 90 / 365 / 7yr pattern."""

    bronze: int = 90
    silver: int = 365
    gold: int = 2555


class SlaSpec(BaseModel):
    freshness: str = "24h"
    availability: str = "99.9%"


class ProductMetadata(BaseModel):
    name: str
    description: str | None = None
    owner: str | None = None
    domain: str | None = None
    sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    sla: SlaSpec | None = None


class EnvironmentSpec(BaseModel):
    subscription_id: str | None = None
    region: str = "eastus"
    resource_group: str | None = None


class PipelineNodeSpec(BaseModel):
    id: str
    type: str
    name: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)


class PipelineEdgeSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # YAML uses "from"/"to" because they read naturally in config files, but
    # "from" is a Python keyword so Pydantic must alias it to "source"/"target".
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    operation: str
    description: str | None = None


class PipelineSpec(BaseModel):
    nodes: list[PipelineNodeSpec]
    edges: list[PipelineEdgeSpec] = Field(default_factory=list)


# ── Optional section stubs (extra="allow" so future loops extend freely) ──────

class ComputeSpec(BaseModel):
    model_config = ConfigDict(extra="allow")


class StorageSpec(BaseModel):
    model_config = ConfigDict(extra="allow")


class GovernanceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")


class QualitySpec(BaseModel):
    model_config = ConfigDict(extra="allow")


class CiCdSpec(BaseModel):
    model_config = ConfigDict(extra="allow")


class MonitoringSpec(BaseModel):
    model_config = ConfigDict(extra="allow")


class NetworkingSpec(BaseModel):
    model_config = ConfigDict(extra="allow")


# ── Root model ─────────────────────────────────────────────────────────────────

class DataProduct(BaseModel):
    """Root model for a data-product.yaml. Supports intent form and explicit pipeline form."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    api_version: str = Field(default="dataforge/v1", alias="apiVersion")
    kind: str = Field(default="DataProduct")

    # Intent form
    product: str | None = None
    environment: str | None = None
    source: SourceSpec | None = None
    target: TargetSpec | None = None
    sla: str | None = None
    classification: ClassificationSpec | None = None
    retention: RetentionSpec | None = None

    # Explicit form
    metadata: ProductMetadata | None = None
    environments: dict[str, EnvironmentSpec] | None = None
    pipeline: PipelineSpec | None = None

    # Optional sections (both forms)
    compute: ComputeSpec | None = None
    storage: StorageSpec | None = None
    governance: GovernanceSpec | None = None
    quality: QualitySpec | None = None
    cicd: CiCdSpec | None = None
    monitoring: MonitoringSpec | None = None
    networking: NetworkingSpec | None = None

    @model_validator(mode="after")
    def _validate_form(self) -> DataProduct:
        has_intent = self.source is not None and self.target is not None
        has_explicit = self.pipeline is not None

        if not has_intent and not has_explicit:
            raise ValueError(
                "DataProduct must have either (source + target) for intent form, "
                "or pipeline.nodes/edges for explicit form"
            )
        if has_intent and has_explicit:
            raise ValueError(
                "DataProduct cannot combine intent form (source/target) with explicit pipeline"
            )
        return self

    @property
    def is_intent_form(self) -> bool:
        return self.source is not None and self.target is not None

    @property
    def name(self) -> str:
        if self.product:
            return self.product
        if self.metadata:
            return self.metadata.name
        return "dataforge"

    @property
    def active_environment(self) -> str:
        return self.environment or "dev"
