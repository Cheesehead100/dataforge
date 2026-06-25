"""Tests for YamlParser — data-product.yaml → DataProduct model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dataforge.parsing.yaml_parser import ParseError, YamlParser


INTENT_MINIMAL = """
product: customer360
environment: prod
source:
  type: sqlserver
target:
  type: fabric
sla: hourly
"""

INTENT_FULL = """
product: crm-platform
environment: dev
source:
  type: eventhub
target:
  type: fabric
sla: realtime
classification:
  pii: true
retention:
  bronze: 90
  silver: 365
  gold: 2555
"""

EXPLICIT_MINIMAL = """
apiVersion: dataforge/v1
kind: DataProduct
metadata:
  name: sales-pipeline
pipeline:
  nodes:
    - id: raw
      type: adls
    - id: transform
      type: databricks
    - id: warehouse
      type: fabric_lakehouse
  edges:
    - from: raw
      to: transform
      operation: read
    - from: transform
      to: warehouse
      operation: write
"""

EXPLICIT_WITH_ENVS = """
apiVersion: dataforge/v1
kind: DataProduct
metadata:
  name: analytics
  sensitivity: confidential
environments:
  dev:
    subscription_id: aaaaaaaa-0000-0000-0000-000000000000
    region: eastus
    resource_group: rg-analytics-dev
  prod:
    subscription_id: bbbbbbbb-0000-0000-0000-000000000000
    region: eastus
    resource_group: rg-analytics-prod
pipeline:
  nodes:
    - id: source
      type: adls
    - id: transform
      type: databricks
  edges:
    - from: source
      to: transform
      operation: read
"""

BOTH_FORMS_CONFLICT = """
product: broken
source:
  type: sqlserver
target:
  type: fabric
pipeline:
  nodes:
    - id: raw
      type: adls
  edges: []
"""

NEITHER_FORM = """
product: broken
environment: dev
"""


class TestIntentForm:
    def test_minimal_parses(self):
        dp = YamlParser().parse_string(INTENT_MINIMAL)
        assert dp.product == "customer360"
        assert dp.is_intent_form
        assert dp.source.type == "sqlserver"
        assert dp.target.type == "fabric"
        assert dp.sla == "hourly"
        assert dp.active_environment == "prod"

    def test_name_from_product_field(self):
        dp = YamlParser().parse_string(INTENT_MINIMAL)
        assert dp.name == "customer360"

    def test_full_intent_parses(self):
        dp = YamlParser().parse_string(INTENT_FULL)
        assert dp.classification is not None
        assert dp.classification.pii is True
        assert dp.retention is not None
        assert dp.retention.bronze == 90
        assert dp.retention.silver == 365
        assert dp.retention.gold == 2555

    def test_eventhub_source(self):
        dp = YamlParser().parse_string(INTENT_FULL)
        assert dp.source.type == "eventhub"
        assert dp.sla == "realtime"

    def test_default_environment_is_dev(self):
        yaml_str = "product: test\nsource:\n  type: adls\ntarget:\n  type: fabric\n"
        dp = YamlParser().parse_string(yaml_str)
        assert dp.active_environment == "dev"


class TestExplicitForm:
    def test_minimal_parses(self):
        dp = YamlParser().parse_string(EXPLICIT_MINIMAL)
        assert not dp.is_intent_form
        assert dp.metadata is not None
        assert dp.metadata.name == "sales-pipeline"

    def test_name_from_metadata(self):
        dp = YamlParser().parse_string(EXPLICIT_MINIMAL)
        assert dp.name == "sales-pipeline"

    def test_pipeline_nodes_parsed(self):
        dp = YamlParser().parse_string(EXPLICIT_MINIMAL)
        assert dp.pipeline is not None
        assert len(dp.pipeline.nodes) == 3
        ids = {n.id for n in dp.pipeline.nodes}
        assert ids == {"raw", "transform", "warehouse"}

    def test_pipeline_edges_parsed(self):
        dp = YamlParser().parse_string(EXPLICIT_MINIMAL)
        assert len(dp.pipeline.edges) == 2
        edge = dp.pipeline.edges[0]
        assert edge.source == "raw"
        assert edge.target == "transform"
        assert edge.operation == "read"

    def test_environments_parsed(self):
        dp = YamlParser().parse_string(EXPLICIT_WITH_ENVS)
        assert dp.environments is not None
        assert "dev" in dp.environments
        assert dp.environments["dev"].region == "eastus"
        assert dp.environments["dev"].subscription_id == "aaaaaaaa-0000-0000-0000-000000000000"
        assert dp.environments["prod"].resource_group == "rg-analytics-prod"

    def test_sensitivity_parsed(self):
        dp = YamlParser().parse_string(EXPLICIT_WITH_ENVS)
        from dataforge.constants import DataSensitivity
        assert dp.metadata.sensitivity == DataSensitivity.CONFIDENTIAL


class TestValidation:
    def test_both_forms_raises(self):
        with pytest.raises(ParseError, match="cannot combine"):
            YamlParser().parse_string(BOTH_FORMS_CONFLICT)

    def test_neither_form_raises(self):
        with pytest.raises(ParseError, match="must have either"):
            YamlParser().parse_string(NEITHER_FORM)

    def test_invalid_yaml_raises(self):
        with pytest.raises(ParseError, match="Invalid YAML"):
            YamlParser().parse_string("key: [\nbad yaml")

    def test_empty_string_raises(self):
        with pytest.raises(ParseError):
            YamlParser().parse_string("")

    def test_optional_sections_parse_without_error(self):
        yaml_str = INTENT_MINIMAL + """
compute:
  databricks:
    node_type: Standard_DS3_v2
storage:
  medallion:
    bronze: {retention_days: 90}
governance:
  unity_catalog:
    catalog: mycat
quality:
  framework: great_expectations
cicd:
  provider: github_actions
monitoring:
  alerts: []
networking:
  vnet_cidr: 10.0.0.0/16
"""
        dp = YamlParser().parse_string(yaml_str)
        assert dp.compute is not None
        assert dp.governance is not None


class TestFileLoading:
    def test_parse_file(self, tmp_path):
        p = tmp_path / "data-product.yaml"
        p.write_text(INTENT_MINIMAL, encoding="utf-8")
        dp = YamlParser().parse_file(p)
        assert dp.product == "customer360"
