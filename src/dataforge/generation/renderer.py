"""Jinja2-based deterministic renderer for Terraform templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from dataforge.constants import NodeType
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import TerraformFile

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Template selected per node type present in the graph
NODE_TYPE_TEMPLATE: dict[NodeType, str] = {
    NodeType.ADF: "data_factory.tf.j2",
    NodeType.DATABRICKS: "databricks.tf.j2",
    NodeType.FABRIC_LAKEHOUSE: "fabric.tf.j2",
    NodeType.ADLS: "storage.tf.j2",
    NodeType.BLOB_STORAGE: "storage.tf.j2",
    NodeType.KEY_VAULT: "key_vault.tf.j2",
    NodeType.SQL_MI: "sql_mi.tf.j2",
    NodeType.EVENTHUB: "eventhub.tf.j2",
}

# Templates always rendered regardless of graph contents
ALWAYS_RENDER = ["providers.tf.j2", "variables.tf.j2", "outputs.tf.j2"]


class Renderer:
    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,
        )

    def render(self, template_name: str, ctx: dict) -> str:
        template = self._env.get_template(template_name)
        return template.render(**ctx)

    def render_all(self, graph: FlowGraph, rbac: RbacResult) -> list[TerraformFile]:
        ctx = self._build_context(graph, rbac)
        files: list[TerraformFile] = []

        # Always-present infrastructure files
        for template_name in ALWAYS_RENDER:
            filename = template_name.replace(".j2", "")
            content = self.render(template_name, ctx)
            files.append(TerraformFile(filename=filename, content=content))

        # RBAC — deterministic, never touched by LLM
        rbac_content = self.render("rbac.tf.j2", ctx)
        files.append(TerraformFile(filename="rbac.tf", content=rbac_content))

        # Per-node-type resource files (deduplicated by template)
        rendered_templates: set[str] = set()
        for node in graph.nodes:
            template_name = NODE_TYPE_TEMPLATE.get(node.type)
            if template_name and template_name not in rendered_templates:
                rendered_templates.add(template_name)
                filename = template_name.replace(".j2", "")
                try:
                    content = self.render(template_name, {**ctx, "nodes_of_type": node.type})
                    files.append(TerraformFile(filename=filename, content=content))
                except Exception:
                    # Template not yet implemented — skip silently in Phase 1
                    pass

        return files

    def _build_context(self, graph: FlowGraph, rbac: RbacResult) -> dict:
        return {
            "graph": graph,
            "rbac": rbac,
            "metadata": graph.metadata,
            "nodes": graph.nodes,
            "edges": graph.edges,
            "env": graph.metadata.environment,
            "location": graph.metadata.location,
            "resource_group": graph.metadata.resource_group,
            "app_name": graph.metadata.application_name,
            "tags": graph.metadata.tags,
            "adf_nodes": graph.nodes_of_type(NodeType.ADF),
            "databricks_nodes": graph.nodes_of_type(NodeType.DATABRICKS),
            "adls_nodes": graph.nodes_of_type(NodeType.ADLS),
            "fabric_nodes": graph.nodes_of_type(NodeType.FABRIC_LAKEHOUSE),
            "kv_nodes": graph.nodes_of_type(NodeType.KEY_VAULT),
            "sql_mi_nodes": graph.nodes_of_type(NodeType.SQL_MI),
        }
