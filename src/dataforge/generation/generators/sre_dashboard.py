"""SreDashboardGenerator — Azure Monitor Workbook, workbook JSON, and Markdown runbook (L9).

Generates three files for every product: a Terraform resource that deploys the
workbook (sre/workbook.tf), the workbook content as a parameterized JSON template
(sre/workbook.json), and an operator runbook (sre/runbook.md) with escalation
steps, SLA thresholds, and quality table references. Runs unconditionally so
every generated stack has an SRE starting point.
"""

from __future__ import annotations

import re

from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()
_ID_RE = re.compile(r"[^a-z0-9]")


def _safe_id(value: str) -> str:
    return _ID_RE.sub("_", value.lower()).strip("_")


class SreDashboardGenerator(BaseGenerator):
    """Generates an Azure Monitor Workbook and operator runbook for every product."""

    def applicable(self, product: DataProduct) -> bool:
        return True  # every product gets an SRE dashboard

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        monitoring_raw = product.monitoring.model_dump() if product.monitoring else {}
        sla_freshness = _parse_freshness(product)
        quality_tables = _quality_tables(product)

        ctx = {
            "product_name": product.name,
            "app": product.name.replace("_", "-"),
            "safe_app": _safe_id(product.name),
            "env": graph.metadata.environment,
            "metadata": graph.metadata,
            "sla_freshness_hours": sla_freshness,
            "quality_tables": quality_tables,
            "has_adf": bool(monitoring_raw.get("alerts")),
            "monthly_budget": (monitoring_raw.get("cost") or {}).get("monthly_budget_usd", 1000),
        }

        return GenerationResult(files=[
            TerraformFile(
                filename="sre/workbook.tf",
                content=_RENDERER.render("sre/workbook.tf.j2", ctx),
            ),
            TerraformFile(
                filename="sre/workbook.json",
                content=_RENDERER.render("sre/workbook.json.j2", ctx),
            ),
            TerraformFile(
                filename="sre/runbook.md",
                content=_RENDERER.render("sre/runbook.md.j2", ctx),
            ),
        ])


def _parse_freshness(product: DataProduct) -> int:
    if product.metadata and product.metadata.sla:
        raw = product.metadata.sla.freshness
        if raw.endswith("h"):
            return int(raw[:-1])
    return {"hourly": 1, "daily": 24, "weekly": 168}.get(
        (product.sla or "").lower(), 24
    )


def _quality_tables(product: DataProduct) -> list[dict]:
    if product.quality is None:
        return []
    q = product.quality.model_dump()
    tables = []
    for check in q.get("checks", []):
        tables.append({
            "layer": check.get("layer", "silver"),
            "table": check.get("table", "unknown"),
        })
    return tables
