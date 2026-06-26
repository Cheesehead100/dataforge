"""Central Jinja2 rendering engine for the DataForge generation layer.

Sits immediately after the FlowGraph and RbacResult are built. The Renderer
selects the correct Jinja2 template for each NodeType present in the graph,
then renders a deterministic set of .tf and .yml files that form the base
Terraform skeleton. No LLM involvement here — that optional polish pass lives
in HclGenerator.

autoescape is intentionally disabled: the target format is HCL/YAML, not HTML,
so entity-escaping would corrupt the output. User-derived strings are sanitised
via _jinja_safe() before entering the template context.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from dataforge.constants import NodeType
from dataforge.generation.tf_refs import principal_tf_ref, scope_tf_ref
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import TerraformFile

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Matches Jinja2 block/expression/comment delimiters that could break template rendering
# if present in user-supplied strings (node names, tag values, etc.).
_JINJA_META_RE = re.compile(r"(\{\{|\}\}|\{%|%\}|\{#|#\})")


def _jinja_safe(value: str) -> str:
    """Escape Jinja2 metacharacters in a user-derived string.

    Replaces ``{{``, ``}}``, ``{%``, ``%}``, ``{#``, ``#}`` with their
    doubled-brace equivalents so Jinja2 treats them as literal text rather
    than template directives.  Applied to every user-controlled string that
    enters the template rendering context.
    """
    return _JINJA_META_RE.sub(lambda m: m.group(0).replace("{", "{{").replace("}", "}}"), value)

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
    """Thin wrapper around a Jinja2 Environment that renders templates against a dict context."""

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            undefined=StrictUndefined,  # fail loudly on missing variables rather than silently emitting ""
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,  # HCL/YAML output — HTML escaping would corrupt it
        )

    def render(self, template_name: str, ctx: dict) -> str:
        template = self._env.get_template(template_name)
        return template.render(**ctx)

    def render_all(self, graph: FlowGraph, rbac: RbacResult) -> list[TerraformFile]:
        """Render the complete set of Terraform files for the given graph and RBAC result.

        File ordering matters for readability of the ZIP output but not for Terraform
        correctness (HCL has no declaration order requirement).
        """
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

        # Per-node-type resource files (deduplicated by template).
        # Multiple graph nodes of the same type share one template (e.g. two ADLS nodes
        # both use storage.tf.j2 — the template iterates adls_nodes internally).
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
        """Assemble the Jinja2 template context from the graph and RBAC result.

        Exposes typed node lists per service (e.g. adf_nodes, databricks_nodes) so
        templates can iterate without filtering. Scalar metadata fields that originate
        from user input are sanitised here — the rest of the context is typed model
        objects that never contain raw user strings.
        """
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
            # Scalar strings that originate from user input are sanitised to prevent
            # Jinja2 metacharacter injection ({{ }}, {% %}, {# #}).
            "env":            _jinja_safe(graph.metadata.environment or ""),
            "location":       _jinja_safe(graph.metadata.location or ""),
            "resource_group": _jinja_safe(graph.metadata.resource_group or ""),
            "app_name":       _jinja_safe(graph.metadata.application_name or ""),
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
