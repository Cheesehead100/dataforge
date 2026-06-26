"""L4: QualityGenerator — PySpark data quality check scripts and Databricks job definitions.

Only activates when the product declares `quality.checks`. For each declared check,
renders a PySpark script that runs the specified rules (not_null, unique, freshness,
etc.) against the named table in the Unity Catalog. Also renders a checks_manifest.json
summary and a databricks_jobs.tf that schedules the scripts as Databricks Workflows.
"""

from __future__ import annotations

import json

from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()

_SEVERITY_MAP = {"critical": "ERROR", "warning": "WARN", "info": "INFO"}

# Maps YAML rule key → check type understood by the template runner
_RULE_TYPES = {
    "not_null", "unique", "accepted_values", "row_count_between",
    "row_count_gt", "value_between", "freshness_within",
}


def _parse_checks(raw_checks: list[dict]) -> list[dict]:
    # rule_cfg can be a list (column names), a dict (typed config), or a scalar —
    # each shape maps to a different template path in run_checks.py.j2.
    parsed: list[dict] = []
    for check in raw_checks:
        layer = check.get("layer", "silver")
        table = check.get("table", "unknown")
        rules: list[dict] = []

        for rule in check.get("rules", []):
            if isinstance(rule, dict):
                for rule_type, rule_cfg in rule.items():
                    if rule_type in _RULE_TYPES:
                        if isinstance(rule_cfg, list):
                            rules.append({"type": rule_type, "columns": rule_cfg})
                        elif isinstance(rule_cfg, dict):
                            rules.append({"type": rule_type, **rule_cfg})
                        else:
                            rules.append({"type": rule_type, "value": rule_cfg})

        parsed.append({
            "layer": layer,
            "table": table,
            "rules": rules,
            "safe_table": table.replace("-", "_"),
        })
    return parsed


class QualityGenerator(BaseGenerator):
    """Generates per-table PySpark quality check scripts and the Databricks job scheduler."""

    def applicable(self, product: DataProduct) -> bool:
        if product.quality is None:
            return False
        q = product.quality.model_dump()
        return bool(q.get("checks"))

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        q = product.quality.model_dump()  # type: ignore[union-attr]
        checks = _parse_checks(q.get("checks", []))
        catalog = _catalog_name(product)

        files: list[TerraformFile] = []

        # One validation script per table
        for check in checks:
            ctx = {
                "product_name": product.name,
                "catalog": catalog,
                "layer": check["layer"],
                "table": check["table"],
                "safe_table": check["safe_table"],
                "rules": check["rules"],
            }
            content = _RENDERER.render("quality/run_checks.py.j2", ctx)
            filename = f"quality/{check['layer']}_{check['safe_table']}_checks.py"
            files.append(TerraformFile(filename=filename, content=content))

        # Manifest of all checks
        manifest = {"product": product.name, "checks": checks}
        files.append(TerraformFile(
            filename="quality/checks_manifest.json",
            content=json.dumps(manifest, indent=2),
        ))

        # Databricks job definitions (scheduled runners)
        jobs_ctx = {
            "product_name": product.name,
            "catalog": catalog,
            "checks": checks,
            "env": graph.metadata.environment,
        }
        files.append(TerraformFile(
            filename="quality/databricks_jobs.tf",
            content=_RENDERER.render("quality/databricks_jobs.tf.j2", jobs_ctx),
        ))

        return GenerationResult(files=files)


def _catalog_name(product: DataProduct) -> str:
    if product.governance:
        gov = product.governance.model_dump()
        uc = gov.get("unity_catalog", {}) or {}
        if uc.get("catalog"):
            return uc["catalog"]
    return product.name.replace("-", "_")
