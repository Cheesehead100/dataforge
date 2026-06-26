"""L5: CiCdGenerator — GitHub Actions or Azure DevOps pipeline with a full quality gate sequence.

Generates a single CI/CD pipeline file whose provider (GitHub Actions vs Azure DevOps)
is determined by product.cicd.provider. When no CI/CD config is supplied, defaults to
GitHub Actions with a standard gate list covering format, validate, security scan,
unit tests, cost estimate, and policy checks across dev / test / prod environments.
"""

from __future__ import annotations

from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()

_DEFAULT_GATES = [
    "terraform_format",
    "terraform_validate",
    "checkov_scan",
    "tfsec_scan",
    "python_unit_tests",
    "cost_estimate",
    "policy_validation",
]

_DEFAULT_ENVS = [
    {"name": "dev", "auto_deploy": True, "approval_required": False, "smoke_test": False},
    {"name": "test", "auto_deploy": False, "approval_required": True, "smoke_test": False},
    {"name": "prod", "auto_deploy": False, "approval_required": True, "smoke_test": True},
]


def _parse_cicd(product: DataProduct) -> dict:
    """Normalise the product CI/CD config into a flat dict safe for the template context.

    Provider strings are normalised to snake_case so templates can compare with simple
    equality checks (e.g. `provider == "github_actions"`) regardless of how the user
    typed them in the YAML.
    """
    if product.cicd is None:
        return {"provider": "github_actions", "gates": _DEFAULT_GATES, "environments": _DEFAULT_ENVS}

    cicd = product.cicd.model_dump()
    provider = cicd.get("provider", "github_actions").lower().replace(" ", "_").replace("-", "_")
    gates = cicd.get("gates", _DEFAULT_GATES)

    raw_envs = cicd.get("environments", _DEFAULT_ENVS)
    envs: list[dict] = []
    for e in raw_envs:
        if isinstance(e, dict):
            envs.append({
                "name": e.get("name", "dev"),
                "auto_deploy": e.get("auto_deploy", False),
                "approval_required": e.get("approval_required", False),
                "smoke_test": e.get("smoke_test", False),
                "approvers": e.get("approvers", []),
            })
    return {"provider": provider, "gates": gates, "environments": envs or _DEFAULT_ENVS}


class CiCdGenerator(BaseGenerator):
    """Generates the primary deployment pipeline file for this product."""

    def applicable(self, product: DataProduct) -> bool:
        return True  # always generate a CI/CD pipeline

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        cfg = _parse_cicd(product)
        ctx = {
            "product_name": product.name,
            "app": product.name.replace("_", "-"),
            "metadata": graph.metadata,
            "gates": cfg["gates"],
            "environments": cfg["environments"],
        }

        if cfg["provider"] in ("github_actions", "github"):
            content = _RENDERER.render("cicd/github_actions.yml.j2", ctx)
            filename = ".github/workflows/dataforge-deploy.yml"
        else:
            content = _RENDERER.render("cicd/azure_devops.yml.j2", ctx)
            filename = "azure-pipelines-deploy.yml"

        return GenerationResult(files=[TerraformFile(filename=filename, content=content)])
