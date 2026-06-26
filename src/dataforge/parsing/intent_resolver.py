"""Converts a DataProduct (from YAML) into a FlowGraph without an LLM call.

This is the deterministic parsing path: given a data-product.yaml, YamlParser
produces a DataProduct and IntentResolver converts it into a FlowGraph using
static lookup tables. No network calls are made. This path is faster, cheaper,
and fully reproducible — the same YAML always produces the same graph.

For the LLM path (natural-language input), see parsing/intent_parser.py.
"""

from __future__ import annotations

from dataforge.constants import NodeType, OperationType
from dataforge.models.data_product import DataProduct, EnvironmentSpec
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode

# ── Source/target type vocabulary ─────────────────────────────────────────────

_SOURCE_TYPE_MAP: dict[str, NodeType] = {
    "sqlserver": NodeType.SQL_MI,
    "sql_mi": NodeType.SQL_MI,
    "eventhub": NodeType.EVENTHUB,
    "event_hub": NodeType.EVENTHUB,
    "adls": NodeType.ADLS,
    "blob": NodeType.BLOB_STORAGE,
    "blob_storage": NodeType.BLOB_STORAGE,
    "csv": NodeType.BLOB_STORAGE,
}

_TARGET_TYPE_MAP: dict[str, NodeType] = {
    "fabric": NodeType.FABRIC_LAKEHOUSE,
    "fabric_lakehouse": NodeType.FABRIC_LAKEHOUSE,
    "adls": NodeType.ADLS,
    "blob": NodeType.BLOB_STORAGE,
    "databricks": NodeType.DATABRICKS,
    "eventhub": NodeType.EVENTHUB,
    "event_hub": NodeType.EVENTHUB,
}

# Storage sources (ADLS, Blob) don't need ADF for ingestion — Databricks
# can read them directly via ABFS/WASBS mount. Using ADF here would add cost
# without benefit, so these sources skip the ADF ingest node entirely.
_SKIP_ADF_SOURCES: frozenset[NodeType] = frozenset({NodeType.ADLS, NodeType.BLOB_STORAGE})

# Source type → ingest edge operation
_INGEST_OP: dict[NodeType, OperationType] = {
    NodeType.EVENTHUB: OperationType.STREAM,
    NodeType.SQL_MI: OperationType.READ,
    NodeType.ADLS: OperationType.READ,
    NodeType.BLOB_STORAGE: OperationType.READ,
}

# Node type vocabulary for explicit-form resolution
_NODE_TYPE_MAP: dict[str, NodeType] = {
    "adf": NodeType.ADF,
    "databricks": NodeType.DATABRICKS,
    "fabric_lakehouse": NodeType.FABRIC_LAKEHOUSE,
    "adls": NodeType.ADLS,
    "key_vault": NodeType.KEY_VAULT,
    "sql_mi": NodeType.SQL_MI,
    "eventhub": NodeType.EVENTHUB,
    "blob_storage": NodeType.BLOB_STORAGE,
    "aks": NodeType.AKS,
}

_OP_MAP: dict[str, OperationType] = {op.value: op for op in OperationType}


class IntentResolver:
    """Converts a DataProduct into a FlowGraph.

    Intent form  → builds a standard pipeline from source→target pattern.
    Explicit form → maps pipeline.nodes/edges directly.
    """

    def resolve(self, product: DataProduct, env: str | None = None) -> FlowGraph:
        effective_env = env or product.active_environment
        metadata = self._build_metadata(product, effective_env)

        if product.is_intent_form:
            nodes, edges = self._build_intent_pipeline(product)
        else:
            nodes, edges = self._build_explicit_pipeline(product)

        return FlowGraph(nodes=nodes, edges=edges, metadata=metadata)

    # ── Intent form ───────────────────────────────────────────────────────────

    def _build_intent_pipeline(
        self, product: DataProduct
    ) -> tuple[list[FlowNode], list[FlowEdge]]:
        assert product.source is not None
        assert product.target is not None

        src_type_str = product.source.type.lower()
        tgt_type_str = product.target.type.lower()

        if src_type_str not in _SOURCE_TYPE_MAP:
            raise ValueError(f"Unknown source type '{src_type_str}'. Known: {list(_SOURCE_TYPE_MAP)}")
        if tgt_type_str not in _TARGET_TYPE_MAP:
            raise ValueError(f"Unknown target type '{tgt_type_str}'. Known: {list(_TARGET_TYPE_MAP)}")

        src_node_type = _SOURCE_TYPE_MAP[src_type_str]
        tgt_node_type = _TARGET_TYPE_MAP[tgt_type_str]
        use_adf = src_node_type not in _SKIP_ADF_SOURCES

        name = product.name
        nodes: list[FlowNode] = []
        edges: list[FlowEdge] = []

        # Source node
        src_id = "source"
        nodes.append(FlowNode(id=src_id, type=src_node_type, name=f"{name} Source"))

        # ADF ingest (skipped when source is ADLS/Blob — direct to Databricks)
        if use_adf:
            adf_id = "ingest"
            nodes.append(FlowNode(id=adf_id, type=NodeType.ADF, name=f"{name} Ingest Factory"))
            ingest_op = _INGEST_OP.get(src_node_type, OperationType.READ)
            edges.append(FlowEdge(source=src_id, target=adf_id, operation=ingest_op))

        # Bronze ADLS is the raw landing zone. Skip it only when source is already ADLS
        # and the target is also ADLS — in that case the source IS the bronze lake and
        # adding another ADLS node would create a duplicate resource with no added value.
        include_bronze = not (src_node_type == NodeType.ADLS and tgt_node_type == NodeType.ADLS)
        if use_adf and include_bronze:
            bronze_id = "bronze"
            nodes.append(FlowNode(id=bronze_id, type=NodeType.ADLS, name=f"{name} Bronze Lake"))
            edges.append(FlowEdge(source=adf_id, target=bronze_id, operation=OperationType.WRITE))

        # Databricks transform (always present)
        dbx_id = "transform"
        nodes.append(FlowNode(id=dbx_id, type=NodeType.DATABRICKS, name=f"{name} Transform Workspace"))

        if use_adf:
            edges.append(FlowEdge(source=adf_id, target=dbx_id, operation=OperationType.TRIGGER))
            if include_bronze:
                edges.append(FlowEdge(source=dbx_id, target=bronze_id, operation=OperationType.READ))
        else:
            # ADLS source: Databricks reads directly from source
            edges.append(FlowEdge(source=dbx_id, target=src_id, operation=OperationType.READ))

        # Key Vault (always)
        kv_id = "secrets"
        nodes.append(FlowNode(id=kv_id, type=NodeType.KEY_VAULT, name=f"{name} Key Vault"))
        edges.append(FlowEdge(source=dbx_id, target=kv_id, operation=OperationType.SECRET_GET))

        # When both source and target are ADLS (e.g. raw lake → curated lake), the
        # bronze node was skipped above and we need a distinct silver node as the
        # Databricks write target. Without this, we'd have only one ADLS node and
        # the graph would contain no meaningful write edge.
        is_adls_passthrough = (src_node_type == NodeType.ADLS and tgt_node_type == NodeType.ADLS)
        if is_adls_passthrough:
            # Use a distinct silver ADLS node as the output
            silver_id = "silver"
            nodes.append(FlowNode(id=silver_id, type=NodeType.ADLS, name=f"{name} Silver Lake"))
            edges.append(FlowEdge(source=dbx_id, target=silver_id, operation=OperationType.WRITE))
        else:
            tgt_id = "target"
            nodes.append(FlowNode(id=tgt_id, type=tgt_node_type, name=f"{name} Target"))
            edges.append(FlowEdge(source=dbx_id, target=tgt_id, operation=OperationType.WRITE))

        return nodes, edges

    # ── Explicit form ─────────────────────────────────────────────────────────

    def _build_explicit_pipeline(
        self, product: DataProduct
    ) -> tuple[list[FlowNode], list[FlowEdge]]:
        assert product.pipeline is not None

        nodes: list[FlowNode] = []
        for spec in product.pipeline.nodes:
            type_str = spec.type.lower()
            if type_str not in _NODE_TYPE_MAP:
                raise ValueError(
                    f"Unknown node type '{spec.type}'. Known: {list(_NODE_TYPE_MAP)}"
                )
            node_type = _NODE_TYPE_MAP[type_str]
            name = spec.name or f"{spec.id.replace('_', ' ').title()}"
            nodes.append(FlowNode(id=spec.id, type=node_type, name=name, properties=spec.properties))

        edges: list[FlowEdge] = []
        for spec in product.pipeline.edges:
            op_str = spec.operation.lower()
            if op_str not in _OP_MAP:
                raise ValueError(
                    f"Unknown operation '{spec.operation}'. Known: {list(_OP_MAP)}"
                )
            edges.append(FlowEdge(source=spec.source, target=spec.target, operation=_OP_MAP[op_str]))

        return nodes, edges

    # ── Metadata ──────────────────────────────────────────────────────────────

    def _build_metadata(self, product: DataProduct, env: str) -> FlowMetadata:
        """Build FlowMetadata from the DataProduct, preferring per-environment overrides.

        If product.environments contains an entry for the resolved env, its region
        and resource_group take precedence over defaults. This lets a single YAML
        file declare different Azure regions or resource groups per environment.
        """
        env_spec: EnvironmentSpec | None = None
        if product.environments:
            env_spec = product.environments.get(env)

        location = env_spec.region if env_spec else "eastus"
        resource_group = (
            env_spec.resource_group
            if env_spec and env_spec.resource_group
            else f"rg-{product.name}-{env}"
        )

        return FlowMetadata(
            original_prompt=f"data-product: {product.name}",
            location=location,
            resource_group=resource_group,
            environment=env,
            application_name=product.name,
            tags={"managed-by": "dataforge", "environment": env},
        )
