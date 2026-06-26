"""CostOptimizationGenerator — utilization analysis script and weekly scheduled CI job (L9).

Generates a Python cost-analysis script (scripts/analyze_costs.py) that queries
Azure Monitor metrics and recommends right-sizing or auto-termination actions when
cluster utilization falls below the configured thresholds. Also generates a weekly
CI pipeline that runs the script and posts results back to the PR or pipeline summary.
"""

from __future__ import annotations

from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()

_DEFAULT_THRESHOLDS = {
    "cpu_low_pct": 20,       # CPU below this → recommend downsize
    "memory_low_pct": 30,    # Memory below this → recommend downsize
    "idle_hours": 2,         # Cluster idle longer than this → flag for auto-termination
    "savings_threshold_usd": 50,  # Only surface recommendations with savings > $X/month
}


class CostOptimizationGenerator(BaseGenerator):
    """Generates a cost analysis script and a weekly scheduled pipeline for every product."""

    def applicable(self, product: DataProduct) -> bool:
        return True  # all products get cost optimization

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        monitoring_raw = product.monitoring.model_dump() if product.monitoring else {}
        compute_raw = product.compute.model_dump() if product.compute else {}
        dbx_compute = compute_raw.get("databricks", {}) or {}

        budget = (monitoring_raw.get("cost") or {}).get("monthly_budget_usd", 1000)
        max_workers = (dbx_compute.get("autoscale") or {}).get("max_workers", 8)
        node_type = dbx_compute.get("node_type", "Standard_DS3_v2")

        cicd_raw = product.cicd.model_dump() if product.cicd else {}
        provider = cicd_raw.get("provider", "github_actions") or "github_actions"

        ctx = {
            "product_name": product.name,
            "app": product.name.replace("_", "-"),
            "env": graph.metadata.environment,
            "metadata": graph.metadata,
            "monthly_budget_usd": budget,
            "max_workers": max_workers,
            "node_type": node_type,
            "thresholds": _DEFAULT_THRESHOLDS,
            "provider": provider,
        }

        files: list[TerraformFile] = [
            TerraformFile(
                filename="scripts/analyze_costs.py",
                content=_RENDERER.render("cost/analyze_costs.py.j2", ctx),
            ),
        ]

        if provider == "azure_devops":
            files.append(TerraformFile(
                filename="azure-pipelines-cost-optimization.yml",
                content=_RENDERER.render("cost/azure_devops_cost.yml.j2", ctx),
            ))
        else:
            files.append(TerraformFile(
                filename=".github/workflows/dataforge-cost-optimization.yml",
                content=_RENDERER.render("cost/github_actions_cost.yml.j2", ctx),
            ))

        return GenerationResult(files=files)
