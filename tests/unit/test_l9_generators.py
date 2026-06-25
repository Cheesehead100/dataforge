"""Unit tests for L9 generators: DriftDetectionGenerator, SreDashboardGenerator, CostOptimizationGenerator."""

from __future__ import annotations

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.generation.generators.cost_optimizer import CostOptimizationGenerator
from dataforge.generation.generators.drift import DriftDetectionGenerator
from dataforge.generation.generators.sre_dashboard import SreDashboardGenerator
from dataforge.generation.data_product_generator import DataProductGenerator
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode
from dataforge.models.data_product import DataProduct
from dataforge.models.rbac import RbacResult
from dataforge.parsing.yaml_parser import YamlParser


_RBAC = RbacResult(assignments=[], unresolved=[], warnings=[])


def _meta(env: str = "dev") -> FlowMetadata:
    return FlowMetadata(
        location="eastus",
        resource_group=f"rg-test-{env}",
        environment=env,
        application_name="test-product",
    )


def _simple_graph(env: str = "dev") -> FlowGraph:
    return FlowGraph(
        nodes=[
            FlowNode(id="adls", type=NodeType.ADLS, name="Storage"),
            FlowNode(id="dbx",  type=NodeType.DATABRICKS, name="Databricks"),
        ],
        edges=[FlowEdge(source="adls", target="dbx", operation=OperationType.READ)],
        metadata=_meta(env),
    )


def _product_gha() -> DataProduct:
    yaml = """
product: customer360
environment: dev
source:
  type: sqlserver
target:
  type: adls
sla: daily
compute:
  databricks:
    node_type: Standard_DS3_v2
    autoscale:
      min_workers: 2
      max_workers: 8
    spot_enabled: false
monitoring:
  cost:
    monthly_budget_usd: 1200
    alert_channel: "email:sre@example.com"
  alerts:
    - name: pipeline_fail
      metric: adf_pipeline_run_failed
      threshold: 1
      severity: critical
      channel: "email:sre@example.com"
cicd:
  provider: github_actions
governance:
  unity_catalog:
    metastore: unity-catalog-prod
    catalog: prod_catalog
    schemas:
      - name: bronze
      - name: silver
      - name: gold
    grants:
      - principal: data_engineers
        privileges: [USE_CATALOG]
        on: catalog
"""
    return YamlParser().parse_string(yaml)


def _product_ado() -> DataProduct:
    yaml = """
product: sales_mart
environment: dev
source:
  type: blob_storage
target:
  type: adls
sla: hourly
monitoring:
  cost:
    monthly_budget_usd: 500
cicd:
  provider: azure_devops
governance:
  unity_catalog:
    metastore: unity-catalog-dev
    catalog: dev_catalog
    schemas:
      - name: silver
"""
    return YamlParser().parse_string(yaml)


# ── DriftDetectionGenerator ─────────────────────────────────────────────────

class TestDriftDetectionGenerator:

    def test_applicable_always(self):
        gen = DriftDetectionGenerator()
        assert gen.applicable(_product_gha()) is True

    def test_generates_gha_workflow(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert ".github/workflows/dataforge-drift.yml" in filenames

    def test_generates_drift_notify_script(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert "scripts/drift_notify.py" in filenames

    def test_gha_workflow_has_schedule(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wf = next(f for f in result.files if "dataforge-drift.yml" in f.filename)
        assert "schedule" in wf.content
        assert "cron" in wf.content

    def test_gha_workflow_uses_detailed_exitcode(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wf = next(f for f in result.files if "dataforge-drift.yml" in f.filename)
        assert "-detailed-exitcode" in wf.content

    def test_gha_workflow_fails_on_drift(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wf = next(f for f in result.files if "dataforge-drift.yml" in f.filename)
        assert "exit 1" in wf.content

    def test_gha_workflow_references_product_name(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wf = next(f for f in result.files if "dataforge-drift.yml" in f.filename)
        assert "customer360" in wf.content

    def test_ado_generates_azure_pipelines_file(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_ado(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert "azure-pipelines-drift.yml" in filenames
        assert ".github/workflows/dataforge-drift.yml" not in filenames

    def test_ado_drift_pipeline_has_schedule(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_ado(), _simple_graph(), _RBAC)
        ado = next(f for f in result.files if "azure-pipelines-drift.yml" in f.filename)
        assert "schedules" in ado.content

    def test_drift_notify_parses_arguments(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "drift_notify.py" in f.filename)
        assert "--plan-output" in script.content
        assert "--status" in script.content
        assert "--output" in script.content

    def test_drift_notify_handles_critical_destroy(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "drift_notify.py" in f.filename)
        assert "DESTRUCTIVE" in script.content

    def test_drift_notify_exits_nonzero_on_drift(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "drift_notify.py" in f.filename)
        assert "sys.exit(1)" in script.content

    def test_gha_workflow_has_manual_dispatch(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wf = next(f for f in result.files if "dataforge-drift.yml" in f.filename)
        assert "workflow_dispatch" in wf.content

    def test_returns_exactly_two_files_gha(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        assert len(result.files) == 2

    def test_returns_exactly_two_files_ado(self):
        gen = DriftDetectionGenerator()
        result = gen.generate(_product_ado(), _simple_graph(), _RBAC)
        assert len(result.files) == 2


# ── SreDashboardGenerator ───────────────────────────────────────────────────

class TestSreDashboardGenerator:

    def test_applicable_always(self):
        gen = SreDashboardGenerator()
        assert gen.applicable(_product_gha()) is True

    def test_generates_three_files(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        assert len(result.files) == 3

    def test_generates_workbook_tf(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert "sre/workbook.tf" in filenames

    def test_generates_workbook_json(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert "sre/workbook.json" in filenames

    def test_generates_runbook_md(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert "sre/runbook.md" in filenames

    def test_workbook_json_has_pipeline_health_section(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wj = next(f for f in result.files if "workbook.json" in f.filename)
        assert "Pipeline Health" in wj.content
        assert "MICROSOFT.DATAFACTORY" in wj.content

    def test_workbook_json_has_freshness_section(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wj = next(f for f in result.files if "workbook.json" in f.filename)
        assert "Freshness" in wj.content

    def test_workbook_tf_deploys_azurerm_workbook(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        tf = next(f for f in result.files if "workbook.tf" in f.filename)
        assert "azurerm_application_insights_workbook" in tf.content

    def test_workbook_tf_outputs_dashboard_url(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        tf = next(f for f in result.files if "workbook.tf" in f.filename)
        assert "sre_dashboard_url" in tf.content

    def test_runbook_has_incident_severity_table(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        rb = next(f for f in result.files if "runbook.md" in f.filename)
        assert "P1" in rb.content
        assert "P2" in rb.content

    def test_runbook_has_drift_scenario(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        rb = next(f for f in result.files if "runbook.md" in f.filename)
        assert "Terraform Drift" in rb.content

    def test_runbook_has_cost_scenario(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        rb = next(f for f in result.files if "runbook.md" in f.filename)
        assert "Cost Budget" in rb.content

    def test_runbook_references_product_name(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        rb = next(f for f in result.files if "runbook.md" in f.filename)
        assert "customer360" in rb.content

    def test_workbook_json_is_valid_notebook_schema(self):
        import json
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wj = next(f for f in result.files if "workbook.json" in f.filename)
        parsed = json.loads(wj.content)
        assert parsed["version"] == "Notebook/1.0"
        assert "items" in parsed

    def test_workbook_json_has_cost_section(self):
        gen = SreDashboardGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wj = next(f for f in result.files if "workbook.json" in f.filename)
        assert "Cost" in wj.content
        assert "1200" in wj.content   # budget from fixture


# ── CostOptimizationGenerator ───────────────────────────────────────────────

class TestCostOptimizationGenerator:

    def test_applicable_always(self):
        gen = CostOptimizationGenerator()
        assert gen.applicable(_product_gha()) is True

    def test_generates_gha_workflow_and_script(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert ".github/workflows/dataforge-cost-optimization.yml" in filenames
        assert "scripts/analyze_costs.py" in filenames

    def test_generates_ado_workflow_and_script(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_ado(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert "azure-pipelines-cost-optimization.yml" in filenames
        assert "scripts/analyze_costs.py" in filenames

    def test_gha_workflow_runs_weekly(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wf = next(f for f in result.files if "cost-optimization.yml" in f.filename)
        assert "0 6 * * 1" in wf.content

    def test_gha_workflow_opens_github_issue(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        wf = next(f for f in result.files if "cost-optimization.yml" in f.filename)
        assert "issues.create" in wf.content

    def test_analyze_costs_has_rightsizing_logic(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "analyze_costs.py" in f.filename)
        assert "downsize_cluster" in script.content
        assert "estimated_monthly_savings" in script.content

    def test_analyze_costs_has_mock_for_dry_run(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "analyze_costs.py" in f.filename)
        assert "--dry-run" in script.content
        assert "_mock_metrics" in script.content

    def test_analyze_costs_references_budget(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "analyze_costs.py" in f.filename)
        assert "1200" in script.content   # from fixture monthly_budget_usd

    def test_analyze_costs_max_workers_from_product(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "analyze_costs.py" in f.filename)
        assert "8" in script.content   # max_workers from fixture

    def test_analyze_costs_exits_2_on_high_priority(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "analyze_costs.py" in f.filename)
        assert "sys.exit(2)" in script.content

    def test_script_has_rich_output(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "analyze_costs.py" in f.filename)
        assert "rich" in script.content

    def test_script_has_spot_recommendation(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        script = next(f for f in result.files if "analyze_costs.py" in f.filename)
        assert "enable_spot" in script.content

    def test_returns_two_files(self):
        gen = CostOptimizationGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        assert len(result.files) == 2


# ── DataProductGenerator integration ────────────────────────────────────────

class TestDataProductGeneratorL9:

    def test_orchestrator_includes_drift_detection(self):
        gen = DataProductGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        drift_files = [f for f in filenames if "drift" in f]
        assert drift_files, f"No drift files in {filenames}"

    def test_orchestrator_includes_sre_dashboard(self):
        gen = DataProductGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        sre_files = [f for f in filenames if "sre/" in f or "runbook" in f]
        assert sre_files, f"No SRE files in {filenames}"

    def test_orchestrator_includes_cost_optimizer(self):
        gen = DataProductGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        cost_files = [f for f in filenames if "cost" in f or "analyze" in f]
        assert cost_files, f"No cost files in {filenames}"

    def test_orchestrator_no_duplicate_filenames(self):
        gen = DataProductGenerator()
        result = gen.generate(_product_gha(), _simple_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert len(filenames) == len(set(filenames)), f"Duplicate filenames: {sorted(filenames)}"
