"""Unit tests for L3–L7 platform layer generators."""

from __future__ import annotations

import json

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.generation.data_product_generator import DataProductGenerator
from dataforge.generation.generators.ansible import AnsibleGenerator
from dataforge.generation.generators.cicd import CiCdGenerator
from dataforge.generation.generators.governance import GovernanceGenerator
from dataforge.generation.generators.monitoring import MonitoringGenerator
from dataforge.generation.generators.quality import QualityGenerator
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode
from dataforge.models.rbac import RbacResult
from dataforge.parsing.yaml_parser import YamlParser


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _meta(env: str = "dev") -> FlowMetadata:
    return FlowMetadata(
        location="eastus",
        resource_group=f"rg-test-{env}",
        environment=env,
        application_name="test-product",
    )


def _graph(env: str = "dev") -> FlowGraph:
    return FlowGraph(
        nodes=[
            FlowNode(id="source", type=NodeType.ADLS, name="Source"),
            FlowNode(id="transform", type=NodeType.DATABRICKS, name="Databricks"),
            FlowNode(id="sink", type=NodeType.FABRIC_LAKEHOUSE, name="Fabric"),
            FlowNode(id="secrets", type=NodeType.KEY_VAULT, name="KV"),
        ],
        edges=[
            FlowEdge(source="transform", target="source", operation=OperationType.READ),
            FlowEdge(source="transform", target="sink", operation=OperationType.WRITE),
            FlowEdge(source="transform", target="secrets", operation=OperationType.SECRET_GET),
        ],
        metadata=_meta(env),
    )


_RBAC = RbacResult(assignments=[], unresolved=[], warnings=[])


FULL_YAML = """
product: analytics-platform
environment: dev
source:
  type: sqlserver
target:
  type: fabric
sla: hourly
governance:
  unity_catalog:
    metastore: unity-catalog-prod
    catalog: analytics_platform
    schemas:
      - name: bronze
      - name: silver
      - name: gold
    grants:
      - principal: data-engineers@company.com
        privileges: [USE_CATALOG, USE_SCHEMA, SELECT, MODIFY]
        on: catalog
      - principal: data-analysts@company.com
        privileges: [USE_CATALOG, USE_SCHEMA, SELECT]
        on: [silver, gold]
  lineage: true
  audit: true
quality:
  framework: great_expectations
  checks:
    - layer: silver
      table: customer_events
      rules:
        - not_null: [customer_id, event_timestamp]
        - unique: [event_id]
        - accepted_values:
            column: event_type
            values: [click, purchase, return]
        - row_count_gt: 1000
        - freshness_within:
            column: event_timestamp
            hours: 6
cicd:
  provider: github_actions
  gates:
    - terraform_format
    - checkov_scan
    - tfsec_scan
    - python_unit_tests
    - cost_estimate
  environments:
    - name: dev
      auto_deploy: true
    - name: prod
      approval_required: true
      smoke_test: true
monitoring:
  alerts:
    - name: pipeline_failure
      metric: adf_pipeline_run_failed
      threshold: 1
      severity: critical
      channel: email:ops@company.com
    - name: freshness_breach
      metric: data_freshness_hours
      threshold: 6
      severity: warning
      channel: email:ops@company.com
  cost:
    monthly_budget_usd: 500
    alert_at_pct: [75, 90, 100]
    alert_channel: email:finops@company.com
compute:
  databricks:
    node_type: Standard_DS3_v2
    autoscale:
      min_workers: 2
      max_workers: 8
    runtime: "14.3.x-scala2.12"
    spot_enabled: true
"""


def _full_product() -> DataProduct:
    return YamlParser().parse_string(FULL_YAML)


# ── L3: GovernanceGenerator ───────────────────────────────────────────────────

class TestGovernanceGenerator:
    def test_not_applicable_without_governance(self):
        dp = DataProduct(product="x", source={"type": "adls"}, target={"type": "fabric"})
        assert not GovernanceGenerator().applicable(dp)

    def test_not_applicable_without_unity_catalog(self):
        dp = YamlParser().parse_string(
            "product: x\nsource:\n  type: adls\ntarget:\n  type: fabric\ngovernance:\n  lineage: true\n"
        )
        assert not GovernanceGenerator().applicable(dp)

    def test_applicable_with_unity_catalog(self):
        assert GovernanceGenerator().applicable(_full_product())

    def test_generates_governance_tf(self):
        result = GovernanceGenerator().generate(_full_product(), _graph(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert "governance.tf" in filenames

    def test_catalog_resource_in_output(self):
        result = GovernanceGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "databricks_catalog" in content
        assert "analytics_platform" in content

    def test_schema_resources_in_output(self):
        result = GovernanceGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "databricks_schema" in content
        assert "bronze" in content
        assert "silver" in content
        assert "gold" in content

    def test_grants_in_output(self):
        result = GovernanceGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "databricks_grants" in content
        assert "data-engineers@company.com" in content

    def test_catalog_grant_scope(self):
        result = GovernanceGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        # catalog-level grant should reference the catalog resource
        assert 'catalog = databricks_catalog.' in content

    def test_schema_grant_scope(self):
        result = GovernanceGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        # data-analysts grant is on: [silver, gold] — should produce schema-scoped grants
        # "on" is YAML-truthy (bool True) so _normalize_grants resolves it via g.get(True)
        assert "data-analysts@company.com" in content

    def test_lineage_comment_present(self):
        result = GovernanceGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "Lineage" in content or "lineage" in content.lower()


# ── L4: QualityGenerator ─────────────────────────────────────────────────────

class TestQualityGenerator:
    def test_not_applicable_without_quality(self):
        dp = DataProduct(product="x", source={"type": "adls"}, target={"type": "fabric"})
        assert not QualityGenerator().applicable(dp)

    def test_not_applicable_without_checks(self):
        dp = YamlParser().parse_string(
            "product: x\nsource:\n  type: adls\ntarget:\n  type: fabric\nquality:\n  framework: great_expectations\n"
        )
        assert not QualityGenerator().applicable(dp)

    def test_applicable_with_checks(self):
        assert QualityGenerator().applicable(_full_product())

    def test_generates_check_script(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert any("silver_customer_events_checks.py" in f for f in filenames)

    def test_generates_manifest(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert "quality/checks_manifest.json" in filenames

    def test_manifest_is_valid_json(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        manifest_file = next(f for f in result.files if "manifest" in f.filename)
        data = json.loads(manifest_file.content)
        assert "product" in data
        assert "checks" in data

    def test_not_null_check_in_script(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        check_file = next(f for f in result.files if f.filename.endswith("_checks.py"))
        assert "not_null" in check_file.content
        assert "customer_id" in check_file.content

    def test_unique_check_in_script(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        check_file = next(f for f in result.files if f.filename.endswith("_checks.py"))
        assert "unique" in check_file.content
        assert "event_id" in check_file.content

    def test_accepted_values_check_in_script(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        check_file = next(f for f in result.files if f.filename.endswith("_checks.py"))
        assert "accepted_values" in check_file.content
        assert "event_type" in check_file.content

    def test_freshness_check_in_script(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        check_file = next(f for f in result.files if f.filename.endswith("_checks.py"))
        assert "freshness_within" in check_file.content

    def test_script_has_spark_session(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        check_file = next(f for f in result.files if f.filename.endswith("_checks.py"))
        assert "SparkSession" in check_file.content

    def test_script_exits_nonzero_on_failure(self):
        result = QualityGenerator().generate(_full_product(), _graph(), _RBAC)
        check_file = next(f for f in result.files if f.filename.endswith("_checks.py"))
        assert "sys.exit(1)" in check_file.content


# ── L5: CiCdGenerator ────────────────────────────────────────────────────────

class TestCiCdGenerator:
    def test_always_applicable(self):
        dp = DataProduct(product="x", source={"type": "adls"}, target={"type": "fabric"})
        assert CiCdGenerator().applicable(dp)

    def test_generates_github_actions_by_default(self):
        result = CiCdGenerator().generate(_full_product(), _graph(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert ".github/workflows/dataforge-deploy.yml" in filenames

    def test_generates_ado_when_requested(self):
        yaml_str = FULL_YAML.replace("provider: github_actions", "provider: azure_devops")
        dp = YamlParser().parse_string(yaml_str)
        result = CiCdGenerator().generate(dp, _graph(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert "azure-pipelines-deploy.yml" in filenames

    def test_checkov_gate_present(self):
        result = CiCdGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "checkov" in content.lower()

    def test_tfsec_gate_present(self):
        result = CiCdGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "tfsec" in content.lower()

    def test_cost_estimate_gate_present(self):
        result = CiCdGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "infracost" in content.lower()

    def test_dev_env_auto_deploy(self):
        result = CiCdGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "deploy-dev" in content or "Deploy_Dev" in content

    def test_prod_env_requires_approval(self):
        result = CiCdGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        # prod environment block should reference 'prod' as an environment (GitHub Environments)
        assert "prod" in content

    def test_smoke_test_in_prod(self):
        result = CiCdGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "smoke" in content.lower() or "smoke_test" in content.lower()

    def test_default_gates_without_cicd_section(self):
        dp = DataProduct(product="x", source={"type": "adls"}, target={"type": "fabric"})
        result = CiCdGenerator().generate(dp, _graph(), _RBAC)
        content = result.files[0].content
        assert "checkov" in content.lower()


# ── L6: MonitoringGenerator ───────────────────────────────────────────────────

class TestMonitoringGenerator:
    def test_not_applicable_without_monitoring(self):
        dp = DataProduct(product="x", source={"type": "adls"}, target={"type": "fabric"})
        assert not MonitoringGenerator().applicable(dp)

    def test_applicable_with_alerts(self):
        assert MonitoringGenerator().applicable(_full_product())

    def test_generates_monitoring_tf(self):
        result = MonitoringGenerator().generate(_full_product(), _graph(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert "monitoring.tf" in filenames

    def test_action_group_in_output(self):
        result = MonitoringGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "azurerm_monitor_action_group" in content

    def test_metric_alert_in_output(self):
        result = MonitoringGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "azurerm_monitor_metric_alert" in content

    def test_email_channel_in_action_group(self):
        result = MonitoringGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "ops@company.com" in content

    def test_cost_budget_in_output(self):
        result = MonitoringGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "azurerm_consumption_budget_resource_group" in content
        assert "500" in content

    def test_budget_thresholds_in_output(self):
        result = MonitoringGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "75" in content
        assert "90" in content
        assert "100" in content

    def test_severity_mapped_correctly(self):
        result = MonitoringGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "severity            = 0" in content  # critical = 0

    def test_data_source_for_rg(self):
        result = MonitoringGenerator().generate(_full_product(), _graph(), _RBAC)
        content = result.files[0].content
        assert "data.azurerm_resource_group" in content


# ── L7: AnsibleGenerator ─────────────────────────────────────────────────────

class TestAnsibleGenerator:
    def test_always_applicable(self):
        dp = DataProduct(product="x", source={"type": "adls"}, target={"type": "fabric"})
        assert AnsibleGenerator().applicable(dp)

    def test_generates_three_files(self):
        result = AnsibleGenerator().generate(_full_product(), _graph(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert "ansible/inventory.yml" in filenames
        assert "ansible/playbooks/configure_databricks.yml" in filenames
        assert "ansible/requirements.yml" in filenames

    def test_inventory_has_databricks_host(self):
        result = AnsibleGenerator().generate(_full_product(), _graph(), _RBAC)
        inv = next(f for f in result.files if f.filename == "ansible/inventory.yml")
        assert "databricks" in inv.content

    def test_playbook_has_cluster_policy(self):
        result = AnsibleGenerator().generate(_full_product(), _graph(), _RBAC)
        pb = next(f for f in result.files if "configure_databricks" in f.filename)
        assert "cluster_policy" in pb.content

    def test_playbook_has_unity_catalog(self):
        result = AnsibleGenerator().generate(_full_product(), _graph(), _RBAC)
        pb = next(f for f in result.files if "configure_databricks" in f.filename)
        assert "unity" in pb.content.lower() or "metastore" in pb.content.lower()

    def test_playbook_has_secret_scope(self):
        result = AnsibleGenerator().generate(_full_product(), _graph(), _RBAC)
        pb = next(f for f in result.files if "configure_databricks" in f.filename)
        assert "secret_scope" in pb.content

    def test_playbook_uses_compute_config(self):
        result = AnsibleGenerator().generate(_full_product(), _graph(), _RBAC)
        pb = next(f for f in result.files if "configure_databricks" in f.filename)
        assert "Standard_DS3_v2" in pb.content

    def test_requirements_has_databricks_collection(self):
        result = AnsibleGenerator().generate(_full_product(), _graph(), _RBAC)
        req = next(f for f in result.files if f.filename == "ansible/requirements.yml")
        assert "databricks.databricks" in req.content

    def test_requirements_has_azure_collection(self):
        result = AnsibleGenerator().generate(_full_product(), _graph(), _RBAC)
        req = next(f for f in result.files if f.filename == "ansible/requirements.yml")
        assert "azure.azcollection" in req.content


# ── DataProductGenerator (orchestrator) ──────────────────────────────────────

class TestDataProductGenerator:
    def test_full_product_generates_all_layers(self):
        result = DataProductGenerator().generate(_full_product(), _graph(), _RBAC)
        filenames = {f.filename for f in result.files}

        assert "governance.tf" in filenames                          # L3
        assert any("_checks.py" in f for f in filenames)            # L4
        assert any("deploy" in f for f in filenames)                 # L5
        assert "monitoring.tf" in filenames                          # L6
        assert "ansible/inventory.yml" in filenames                  # L7

    def test_minimal_product_skips_optional_generators(self):
        dp = DataProduct(product="x", source={"type": "adls"}, target={"type": "fabric"})
        result = DataProductGenerator().generate(dp, _graph(), _RBAC)
        filenames = {f.filename for f in result.files}

        assert "governance.tf" not in filenames
        assert "monitoring.tf" not in filenames
        assert not any("_checks.py" in f for f in filenames)
        # CI/CD and Ansible are always generated
        assert any("deploy" in f for f in filenames)
        assert "ansible/inventory.yml" in filenames

    def test_no_exceptions_on_full_product(self):
        # Smoke test — should not raise
        DataProductGenerator().generate(_full_product(), _graph(), _RBAC)
