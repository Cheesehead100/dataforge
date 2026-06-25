"""DriftDetectionGenerator — nightly scheduled terraform plan + alert routing (L8 completion)."""

from __future__ import annotations

from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()

_DEFAULT_CHANNELS = ["email"]
_DEFAULT_SCHEDULE_UTC = "0 2 * * *"  # nightly at 02:00 UTC


def _parse_notification(cicd_raw: dict, monitoring_raw: dict) -> dict:
    """Extract notification channels from cicd or monitoring config."""
    channels: list[str] = []
    teams_webhook = ""
    slack_webhook = ""
    email = ""

    # Check monitoring alerts for an email contact
    for alert in monitoring_raw.get("alerts", []):
        channel = alert.get("channel", "")
        if channel.startswith("email:") and not email:
            email = channel.split(":", 1)[-1]

    cost = monitoring_raw.get("cost", {}) or {}
    if cost.get("alert_channel", "").startswith("email:") and not email:
        email = cost["alert_channel"].split(":", 1)[-1]

    if email:
        channels.append("email")
    channels.append("github_summary")  # always emit a job summary

    return {
        "channels": channels,
        "email": email,
        "teams_webhook_secret": "TEAMS_DRIFT_WEBHOOK",
        "slack_webhook_secret": "SLACK_DRIFT_WEBHOOK",
        "has_email": bool(email),
        "has_teams": False,
        "has_slack": False,
    }


def _cicd_provider(product: DataProduct) -> str:
    if product.cicd is None:
        return "github_actions"
    return product.cicd.model_dump().get("provider", "github_actions") or "github_actions"


class DriftDetectionGenerator(BaseGenerator):
    def applicable(self, product: DataProduct) -> bool:
        return True  # every product needs drift detection

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        provider = _cicd_provider(product)
        cicd_raw = product.cicd.model_dump() if product.cicd else {}
        monitoring_raw = product.monitoring.model_dump() if product.monitoring else {}
        notification = _parse_notification(cicd_raw, monitoring_raw)

        ctx = {
            "product_name": product.name,
            "app": product.name.replace("_", "-"),
            "env": graph.metadata.environment,
            "metadata": graph.metadata,
            "notification": notification,
            "schedule_cron": _DEFAULT_SCHEDULE_UTC,
            "tf_dir": "output",  # conventional — where generated TF lives
        }

        files: list[TerraformFile] = []

        if provider == "azure_devops":
            files.append(TerraformFile(
                filename="azure-pipelines-drift.yml",
                content=_RENDERER.render("drift/azure_devops_drift.yml.j2", ctx),
            ))
        else:
            files.append(TerraformFile(
                filename=".github/workflows/dataforge-drift.yml",
                content=_RENDERER.render("drift/github_actions_drift.yml.j2", ctx),
            ))

        files.append(TerraformFile(
            filename="scripts/drift_notify.py",
            content=_RENDERER.render("drift/drift_notify.py.j2", ctx),
        ))

        return GenerationResult(files=files)
