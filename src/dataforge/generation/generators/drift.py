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
    """Extract notification channels from cicd or monitoring config.

    Teams/Slack are enabled via monitoring.notifications:
        monitoring:
          notifications:
            teams: true   # webhook URL supplied via secret DRIFT_TEAMS_WEBHOOK
            slack: true   # webhook URL supplied via secret DRIFT_SLACK_WEBHOOK
    """
    channels: list[str] = []
    email = ""

    # Email: check monitoring.alerts channels then monitoring.cost.alert_channel
    for alert in monitoring_raw.get("alerts", []):
        channel = alert.get("channel", "")
        if channel.startswith("email:") and not email:
            email = channel.split(":", 1)[-1]

    cost = monitoring_raw.get("cost", {}) or {}
    if cost.get("alert_channel", "").startswith("email:") and not email:
        email = cost["alert_channel"].split(":", 1)[-1]

    # Teams / Slack: declared under monitoring.notifications
    notifications = monitoring_raw.get("notifications") or {}
    has_teams = bool(notifications.get("teams"))
    has_slack = bool(notifications.get("slack"))

    if email:
        channels.append("email")
    if has_teams:
        channels.append("teams")
    if has_slack:
        channels.append("slack")
    channels.append("github_summary")  # always emit a job summary

    return {
        "channels": channels,
        "email": email,
        "teams_webhook_secret": "DRIFT_TEAMS_WEBHOOK",
        "slack_webhook_secret": "DRIFT_SLACK_WEBHOOK",
        "has_email": bool(email),
        "has_teams": has_teams,
        "has_slack": has_slack,
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
