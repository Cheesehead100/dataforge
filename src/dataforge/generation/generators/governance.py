"""L3: GovernanceGenerator — Unity Catalog catalog, schemas, and grants."""

from __future__ import annotations

import re

from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()
_ID_RE = re.compile(r"[^a-z0-9_]")


def _safe_id(value: str) -> str:
    return _ID_RE.sub("_", value.lower()).strip("_")


def _normalize_grants(grants: list[dict], schemas: list[str]) -> list[dict]:
    """Expand multi-schema grants into individual grant records for template rendering."""
    out: list[dict] = []
    for i, g in enumerate(grants):
        # "on" is a YAML boolean alias (true/yes/on) — pyyaml parses it as True
        on = g.get("on") or g.get(True, "catalog")
        principal = g.get("principal", f"principal_{i}")
        privileges = g.get("privileges", [])
        if on == "catalog":
            out.append({
                "principal": principal,
                "privileges": privileges,
                "scope": "catalog",
                "schema": "",
                "resource_id": f"catalog_{_safe_id(principal)}",
            })
        elif isinstance(on, list):
            for schema in on:
                out.append({
                    "principal": principal,
                    "privileges": privileges,
                    "scope": "schema",
                    "schema": schema,
                    "resource_id": f"{_safe_id(schema)}_{_safe_id(principal)}",
                })
        else:
            out.append({
                "principal": principal,
                "privileges": privileges,
                "scope": "schema",
                "schema": on,
                "resource_id": f"{_safe_id(on)}_{_safe_id(principal)}",
            })
    return out


class GovernanceGenerator(BaseGenerator):
    def applicable(self, product: DataProduct) -> bool:
        if product.governance is None:
            return False
        gov = product.governance.model_dump()
        return bool(gov.get("unity_catalog"))

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        gov = product.governance.model_dump()  # type: ignore[union-attr]
        uc = gov.get("unity_catalog", {}) or {}

        catalog = uc.get("catalog") or _safe_id(product.name)
        metastore = uc.get("metastore", "")

        raw_schemas = uc.get("schemas", [])
        schemas: list[str] = []
        for s in raw_schemas:
            if isinstance(s, dict):
                schemas.append(s.get("name", ""))
            else:
                schemas.append(str(s))
        schemas = [s for s in schemas if s]

        grants = _normalize_grants(uc.get("grants", []), schemas)

        ctx = {
            "app": _safe_id(product.name),
            "product_name": product.name,
            "catalog": catalog,
            "metastore_name": metastore,
            "schemas": schemas,
            "normalized_grants": grants,
            "lineage": gov.get("lineage", False),
            "audit": gov.get("audit", False),
            "metadata": graph.metadata,
        }

        content = _RENDERER.render("governance.tf.j2", ctx)
        return GenerationResult(files=[TerraformFile(filename="governance.tf", content=content)])
