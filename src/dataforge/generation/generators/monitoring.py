"""L6: MonitoringGenerator — Azure Monitor alerts, action groups, and cost budgets."""

from __future__ import annotations

import re
from datetime import date

from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()
_ID_RE = re.compile(r"[^a-z0-9_]")

_SEVERITY_CODE = {"critical": 0, "error": 1, "warning": 2, "info": 3}

# Metric namespace mapping for common DataForge metrics
_METRIC_NAMESPACES = {
    "adf_pipeline_run_failed": "Microsoft.DataFactory/factories",
    "data_freshness_hours": "Microsoft.DataFactory/factories",
    "dq_rule_failed": "Microsoft.DataFactory/factories",
    "databricks_job_failed": "Microsoft.Databricks/workspaces",
}


def _safe_id(value: str) -> str:
    return _ID_RE.sub("_", value.lower()).strip("_")


def _parse_alerts(raw: list[dict]) -> list[dict]:
    out: list[dict] = []
    for alert in raw:
        name = alert.get("name", "unnamed")
        channel = alert.get("channel", "")
        email = channel.split(":", 1)[-1] if ":" in channel else channel
        metric = alert.get("metric", "")
        namespace = _METRIC_NAMESPACES.get(metric, "Microsoft.DataFactory/factories")
        sev = alert.get("severity", "warning").lower()
        out.append({
            "name": name,
            "id": _safe_id(name),
            "metric": metric,
            "namespace": namespace,
            "threshold": alert.get("threshold", 1),
            "window_minutes": alert.get("window_minutes", 5),
            "severity_code": _SEVERITY_CODE.get(sev, 2),
            "email": email,
        })
    return out


def _parse_email_channels(alerts: list[dict]) -> list[dict]:
    seen: set[str] = set()
    channels: list[dict] = []
    for a in alerts:
        email = a.get("email", "")
        if email and email not in seen:
            seen.add(email)
            channels.append({"name": _safe_id(email), "address": email})
    return channels


class MonitoringGenerator(BaseGenerator):
    def applicable(self, product: DataProduct) -> bool:
        if product.monitoring is None:
            return False
        m = product.monitoring.model_dump()
        return bool(m.get("alerts") or m.get("cost"))

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        m = product.monitoring.model_dump()  # type: ignore[union-attr]
        alerts = _parse_alerts(m.get("alerts", []))
        email_channels = _parse_email_channels(alerts)

        cost_raw = m.get("cost") or {}
        cost_budget = None
        if cost_raw:
            channel = cost_raw.get("alert_channel", "")
            email = channel.split(":", 1)[-1] if ":" in channel else channel
            if email and email not in {c["address"] for c in email_channels}:
                email_channels.append({"name": _safe_id(email), "address": email})
            cost_budget = {
                "amount": cost_raw.get("monthly_budget_usd", 1000),
                "thresholds": cost_raw.get("alert_at_pct", [75, 90, 100]),
            }

        ctx = {
            "app": _safe_id(product.name),
            "product_name": product.name,
            "env": graph.metadata.environment,
            "metadata": graph.metadata,
            "alerts": alerts,
            "email_channels": email_channels,
            "cost_budget": cost_budget,
            "budget_start_date": date.today().strftime("%Y-%m-01") + "T00:00:00Z",
        }

        content = _RENDERER.render("monitoring.tf.j2", ctx)
        return GenerationResult(files=[TerraformFile(filename="monitoring.tf", content=content)])
