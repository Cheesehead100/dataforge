"""AdfPipelineGenerator — TF resources for ADF linked services, datasets, and pipelines."""

from __future__ import annotations

from dataforge.constants import NodeType, OperationType
from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowNode
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()

# Source node types that ADF can natively ingest from
_ADF_SOURCE_TYPES = {NodeType.SQL_MI, NodeType.BLOB_STORAGE, NodeType.EVENTHUB}


def _find_adf_pipelines(graph: FlowGraph) -> list[dict]:
    """
    Walk the graph to find ADF-mediated ingestion paths.
    Returns one pipeline spec per (source → ADF → sink) chain.
    """
    adf_nodes = graph.nodes_of_type(NodeType.ADF)
    if not adf_nodes:
        return []

    pipelines: list[dict] = []
    adf_ids = {n.id for n in adf_nodes}

    for adf_node in adf_nodes:
        sources = _upstream_sources(graph, adf_node, _ADF_SOURCE_TYPES)
        sinks = _downstream_sinks(graph, adf_node)

        for source in sources:
            for sink in sinks:
                pipelines.append({
                    "adf_node": adf_node,
                    "source": source,
                    "sink": sink,
                    "safe_name": f"{source.type.value}_{sink.type.value}".replace("_", ""),
                    "source_type": source.type,
                    "sink_type": sink.type,
                })

    return pipelines


def _node_or_none(graph: FlowGraph, node_id: str) -> FlowNode | None:
    try:
        return graph.node(node_id)
    except KeyError:
        return None


def _upstream_sources(
    graph: FlowGraph, adf_node: FlowNode, allowed: set[NodeType]
) -> list[FlowNode]:
    sources = []
    for edge in graph.edges:
        if edge.target == adf_node.id:
            node = _node_or_none(graph, edge.source)
            if node is not None and node.type in allowed:
                sources.append(node)
    return sources


def _downstream_sinks(graph: FlowGraph, adf_node: FlowNode) -> list[FlowNode]:
    sinks = []
    for edge in graph.edges:
        if edge.source == adf_node.id:
            node = _node_or_none(graph, edge.target)
            if node is not None and node.type in {NodeType.ADLS, NodeType.BLOB_STORAGE}:
                sinks.append(node)
    return sinks


class AdfPipelineGenerator(BaseGenerator):
    def applicable(self, product: DataProduct) -> bool:
        return True  # always attempt; produces empty result if no ADF in graph

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        pipelines = _find_adf_pipelines(graph)
        if not pipelines:
            return GenerationResult(files=[])

        ctx = {
            "product_name": product.name,
            "app": product.name.replace("_", "-"),
            "env": graph.metadata.environment,
            "metadata": graph.metadata,
            "pipelines": [_enrich_pipeline(p, graph) for p in pipelines],
            "has_sql_source": any(
                p["source_type"] == NodeType.SQL_MI for p in pipelines
            ),
            "has_eventhub_source": any(
                p["source_type"] == NodeType.EVENTHUB for p in pipelines
            ),
            "has_blob_source": any(
                p["source_type"] == NodeType.BLOB_STORAGE for p in pipelines
            ),
        }

        content = _RENDERER.render("adf_pipeline.tf.j2", ctx)
        return GenerationResult(
            files=[TerraformFile(filename="adf_pipeline.tf", content=content)]
        )


def _enrich_pipeline(p: dict, graph: FlowGraph) -> dict:
    """Add display names and copy activity config to a pipeline spec."""
    source = p["source"]
    sink = p["sink"]
    return {
        **p,
        "display_name": f"pl-{source.type.value}-to-{sink.type.value}",
        "source_name": source.name or source.type.value,
        "sink_name": sink.name or sink.type.value,
        "copy_behavior": "PreserveHierarchy",
        "batch_size": 10000,
        "parallelism": 4,
    }
