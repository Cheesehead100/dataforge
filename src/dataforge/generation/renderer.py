"""Jinja2-based deterministic renderer for Terraform templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from dataforge.constants import NodeType
from dataforge.generation.tf_refs import principal_tf_ref, scope_tf_ref
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
    NodeType.AKS: "aks.tf.j2",
}

# Templates always rendered regardless of graph contents
ALWAYS_RENDER = ["providers.tf.j2", "variables.tf.j2", "outputs.tf.j2"]

# Non-Terraform files always rendered (CI pipeline, etc.)
ALWAYS_RENDER_EXTRA = ["azure-pipelines.yml.j2"]


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

        # Networking for Databricks VNet injection — rendered before databricks.tf
        if graph.nodes_of_type(NodeType.DATABRICKS):
            content = self.render("networking.tf.j2", ctx)
            files.append(TerraformFile(filename="networking.tf", content=content))

        # RBAC — deterministic, never touched by LLM
        rbac_content = self.render("rbac.tf.j2", ctx)
        files.append(TerraformFile(filename="rbac.tf", content=rbac_content))

        # CI / extra files (YAML, etc.)
        for template_name in ALWAYS_RENDER_EXTRA:
            filename = template_name.replace(".j2", "")
            content = self.render(template_name, ctx)
            files.append(TerraformFile(filename=filename, content=content))

        # Per-node-type resource files (deduplicated by template)
        rendered_templates: set[str] = set()
        for node in graph.nodes:
            template_name = NODE_TYPE_TEMPLATE.get(node.type)
            if template_name and template_name not in rendered_templates:
                rendered_templates.add(template_name)
                filename = template_name.replace(".j2", "")
                try:
                    content = self.render(template_name, ctx)
                    files.append(TerraformFile(filename=filename, content=content))
                except TemplateNotFound:
                    # Template not yet implemented — skip silently
                    pass

        return files

    def _build_context(self, graph: FlowGraph, rbac: RbacResult) -> dict:
        node_by_id = {n.id: n for n in graph.nodes}

        def _principal_ref(node_id: str) -> str:
            return principal_tf_ref(node_by_id[node_id])

        def _scope_ref(node_id: str) -> str:
            return scope_tf_ref(node_by_id[node_id])

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
            "blob_nodes": graph.nodes_of_type(NodeType.BLOB_STORAGE),
            "fabric_nodes": graph.nodes_of_type(NodeType.FABRIC_LAKEHOUSE),
            "kv_nodes": graph.nodes_of_type(NodeType.KEY_VAULT),
            "sql_mi_nodes": graph.nodes_of_type(NodeType.SQL_MI),
            "eventhub_nodes": graph.nodes_of_type(NodeType.EVENTHUB),
            "aks_nodes": graph.nodes_of_type(NodeType.AKS),
            "node_by_id": node_by_id,
            "principal_tf_ref": _principal_ref,
            "scope_tf_ref": _scope_ref,
        }
