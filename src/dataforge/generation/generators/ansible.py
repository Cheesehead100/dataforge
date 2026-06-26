"""L7: AnsibleGenerator — post-provisioning configuration playbooks for Databricks.

Terraform provisions Azure resources; Ansible handles the second-day configuration
that the azurerm provider cannot express — cluster creation, Unity Catalog schema
setup, and Key Vault secret injection into Databricks secrets. Generates an
inventory, a configure_databricks playbook, and a requirements file that the CI
pipeline runs immediately after terraform apply.
"""

from __future__ import annotations

from dataforge.constants import NodeType
from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()


def _has_databricks(graph: FlowGraph) -> bool:
    return bool(graph.nodes_of_type(NodeType.DATABRICKS))


class AnsibleGenerator(BaseGenerator):
    """Generates Ansible inventory and Databricks configuration playbooks for this product."""

    def applicable(self, product: DataProduct) -> bool:
        return True  # generated for any product; playbooks are no-ops if resources not present

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        compute_raw = product.compute.model_dump() if product.compute else {}
        dbx_compute = compute_raw.get("databricks", {}) or {}

        gov_raw = product.governance.model_dump() if product.governance else {}
        uc = gov_raw.get("unity_catalog", {}) or {}
        catalog = uc.get("catalog", product.name.replace("-", "_"))
        schemas = [
            (s.get("name", s) if isinstance(s, dict) else s)
            for s in uc.get("schemas", [])
        ]

        has_kv = bool(graph.nodes_of_type(NodeType.KEY_VAULT))

        ctx = {
            "product_name": product.name,
            "app": product.name.replace("_", "-"),
            "env": graph.metadata.environment,
            "metadata": graph.metadata,
            "has_databricks": _has_databricks(graph),
            "has_key_vault": has_kv,
            "has_unity_catalog": bool(uc),
            "catalog": catalog,
            "schemas": schemas,
            "node_type": dbx_compute.get("node_type", "Standard_DS3_v2"),
            "min_workers": (dbx_compute.get("autoscale", {}) or {}).get("min_workers", 2),
            "max_workers": (dbx_compute.get("autoscale", {}) or {}).get("max_workers", 8),
            "runtime": dbx_compute.get("runtime", "14.3.x-scala2.12"),
            "spot_enabled": dbx_compute.get("spot_enabled", True),
        }

        files: list[TerraformFile] = [
            TerraformFile(
                filename="ansible/inventory.yml",
                content=_RENDERER.render("ansible/inventory.yml.j2", ctx),
            ),
            TerraformFile(
                filename="ansible/playbooks/configure_databricks.yml",
                content=_RENDERER.render("ansible/configure_databricks.yml.j2", ctx),
            ),
            TerraformFile(
                filename="ansible/requirements.yml",
                content=_RENDERER.render("ansible/requirements.yml.j2", ctx),
            ),
        ]

        return GenerationResult(files=files)
