"""Unit tests for AdfPipelineGenerator and ReadinessGenerator."""

from __future__ import annotations

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.generation.generators.adf_pipeline import AdfPipelineGenerator
from dataforge.generation.generators.readiness import ReadinessGenerator
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode
from dataforge.models.data_product import DataProduct
from dataforge.models.rbac import RbacResult
from dataforge.parsing.yaml_parser import YamlParser


# ── Shared fixtures ────────────────────────────────────────────────────────────

_RBAC = RbacResult(assignments=[], unresolved=[], warnings=[])


def _meta(env: str = "dev") -> FlowMetadata:
    return FlowMetadata(
        location="eastus",
        resource_group=f"rg-test-{env}",
        environment=env,
        application_name="test-product",
    )


def _graph_with_adf_sql() -> FlowGraph:
    """sql_mi → ADF → ADLS (standard sqlserver intent pipeline)."""
    return FlowGraph(
        nodes=[
            FlowNode(id="source_sql", type=NodeType.SQL_MI, name="Source SQL"),
            FlowNode(id="ingest_adf", type=NodeType.ADF, name="ADF"),
            FlowNode(id="bronze", type=NodeType.ADLS, name="Bronze ADLS"),
            FlowNode(id="transform", type=NodeType.DATABRICKS, name="Databricks"),
            FlowNode(id="secrets", type=NodeType.KEY_VAULT, name="KV"),
        ],
        edges=[
            FlowEdge(source="source_sql", target="ingest_adf", operation=OperationType.READ),
            FlowEdge(source="ingest_adf", target="bronze", operation=OperationType.WRITE),
            FlowEdge(source="ingest_adf", target="transform", operation=OperationType.TRIGGER),
            FlowEdge(source="transform", target="bronze", operation=OperationType.READ),
            FlowEdge(source="transform", target="secrets", operation=OperationType.SECRET_GET),
        ],
        metadata=_meta(),
    )


def _graph_with_adf_eventhub() -> FlowGraph:
    """eventhub → ADF → ADLS (streaming capture pipeline)."""
    return FlowGraph(
        nodes=[
            FlowNode(id="source_eh", type=NodeType.EVENTHUB, name="EventHub"),
            FlowNode(id="ingest_adf", type=NodeType.ADF, name="ADF"),
            FlowNode(id="bronze", type=NodeType.ADLS, name="Bronze ADLS"),
        ],
        edges=[
            FlowEdge(source="source_eh", target="ingest_adf", operation=OperationType.STREAM),
            FlowEdge(source="ingest_adf", target="bronze", operation=OperationType.WRITE),
        ],
        metadata=_meta(),
    )


def _graph_no_adf() -> FlowGraph:
    """ADLS → Databricks (no ADF)."""
    return FlowGraph(
        nodes=[
            FlowNode(id="source", type=NodeType.ADLS, name="Source"),
            FlowNode(id="transform", type=NodeType.DATABRICKS, name="Databricks"),
            FlowNode(id="secrets", type=NodeType.KEY_VAULT, name="KV"),
        ],
        edges=[
            FlowEdge(source="transform", target="source", operation=OperationType.READ),
            FlowEdge(source="transform", target="secrets", operation=OperationType.SECRET_GET),
        ],
        metadata=_meta(),
    )


def _minimal_product() -> DataProduct:
    return DataProduct(product="test-product", source={"type": "sqlserver"}, target={"type": "fabric"})


FULL_YAML = """
product: analytics-platform
environment: dev
source:
  type: sqlserver
target:
  type: fabric
governance:
  unity_catalog:
    catalog: analytics_platform
    schemas:
      - name: bronze
      - name: silver
      - name: gold
quality:
  checks:
    - layer: silver
      table: events
      rules:
        - not_null: [id]
"""


def _full_product() -> DataProduct:
    return YamlParser().parse_string(FULL_YAML)


# ── AdfPipelineGenerator ──────────────────────────────────────────────────────

class TestAdfPipelineGenerator:
    def test_always_applicable(self):
        assert AdfPipelineGenerator().applicable(_minimal_product())

    def test_no_output_without_adf_node(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_no_adf(), _RBAC)
        assert result.files == []

    def test_generates_adf_pipeline_tf_for_sql_source(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert "adf_pipeline.tf" in filenames

    def test_generates_adf_pipeline_tf_for_eventhub_source(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_eventhub(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert "adf_pipeline.tf" in filenames

    def test_sql_linked_service_in_output(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "azurerm_data_factory_linked_service_sql_server" in content

    def test_adls_linked_service_in_output(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "azurerm_data_factory_linked_service_data_lake_storage_gen2" in content

    def test_keyvault_linked_service_in_output(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "azurerm_data_factory_linked_service_key_vault" in content

    def test_parquet_sink_dataset_in_output(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "azurerm_data_factory_dataset_parquet" in content

    def test_pipeline_resource_in_output(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "azurerm_data_factory_pipeline" in content

    def test_schedule_trigger_in_output(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "azurerm_data_factory_trigger_schedule" in content

    def test_copy_activity_in_pipeline(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "CopyToBronze" in content

    def test_eventhub_source_uses_blob_dataset(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_eventhub(), _RBAC)
        content = result.files[0].content
        assert "azurerm_data_factory_dataset_azure_blob" in content

    def test_bronze_container_as_sink(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert '"bronze"' in content

    def test_retry_policy_in_copy_activity(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "retry" in content.lower()

    def test_rejected_rows_redirect(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_sql(), _RBAC)
        content = result.files[0].content
        assert "_rejected" in content

    def test_no_sql_linked_service_for_eventhub_only(self):
        result = AdfPipelineGenerator().generate(_minimal_product(), _graph_with_adf_eventhub(), _RBAC)
        content = result.files[0].content
        assert "linked_service_sql_server" not in content


# ── ReadinessGenerator ────────────────────────────────────────────────────────

class TestReadinessGenerator:
    def test_always_applicable(self):
        assert ReadinessGenerator().applicable(_minimal_product())

    def test_generates_five_files(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        filenames = {f.filename for f in result.files}
        assert "tests/readiness/conftest.py" in filenames
        assert "tests/readiness/test_storage.py" in filenames
        assert "tests/readiness/test_platform.py" in filenames
        assert "tests/readiness/requirements.txt" in filenames
        assert "tests/readiness/run_readiness.sh" in filenames

    def test_conftest_has_blob_fixture(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        conf = next(f for f in result.files if "conftest" in f.filename)
        assert "BlobServiceClient" in conf.content

    def test_conftest_has_databricks_fixture(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        conf = next(f for f in result.files if "conftest" in f.filename)
        assert "WorkspaceClient" in conf.content

    def test_conftest_has_keyvault_fixture(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        conf = next(f for f in result.files if "conftest" in f.filename)
        assert "SecretClient" in conf.content

    def test_storage_tests_check_medallion_containers(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        storage = next(f for f in result.files if "test_storage" in f.filename)
        assert "bronze" in storage.content
        assert "silver" in storage.content
        assert "gold" in storage.content

    def test_storage_tests_check_write_access(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        storage = next(f for f in result.files if "test_storage" in f.filename)
        assert "upload_blob" in storage.content

    def test_storage_tests_check_no_public_access(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        storage = next(f for f in result.files if "test_storage" in f.filename)
        assert "public_access" in storage.content

    def test_platform_tests_check_cluster_policy(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        platform = next(f for f in result.files if "test_platform" in f.filename)
        assert "cluster_policy" in platform.content

    def test_platform_tests_check_catalog_exists(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        platform = next(f for f in result.files if "test_platform" in f.filename)
        assert "catalog_exists" in platform.content or "analytics_platform" in platform.content

    def test_platform_tests_check_secret_scope(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        platform = next(f for f in result.files if "test_platform" in f.filename)
        assert "secret_scope" in platform.content

    def test_platform_tests_include_e2e_smoke(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        platform = next(f for f in result.files if "test_platform" in f.filename)
        assert "smoke" in platform.content.lower()

    def test_requirements_has_azure_storage(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        reqs = next(f for f in result.files if "requirements" in f.filename)
        assert "azure-storage-blob" in reqs.content

    def test_requirements_has_databricks_sdk(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        reqs = next(f for f in result.files if "requirements" in f.filename)
        assert "databricks-sdk" in reqs.content

    def test_shell_script_exits_nonzero_on_failure(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        sh = next(f for f in result.files if f.filename.endswith(".sh"))
        assert "exit ${EXIT_CODE}" in sh.content

    def test_shell_script_sources_terraform_outputs(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        sh = next(f for f in result.files if f.filename.endswith(".sh"))
        assert "tf_outputs.json" in sh.content

    def test_shell_script_blocks_promotion_on_failure(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        sh = next(f for f in result.files if f.filename.endswith(".sh"))
        assert "DO NOT promote" in sh.content

    def test_dns_resolution_check_in_platform_tests(self):
        result = ReadinessGenerator().generate(_full_product(), _graph_with_adf_sql(), _RBAC)
        platform = next(f for f in result.files if "test_platform" in f.filename)
        assert "getaddrinfo" in platform.content or "dns" in platform.content.lower()

    def test_minimal_product_skips_storage_tests(self):
        no_adls_graph = FlowGraph(
            nodes=[FlowNode(id="transform", type=NodeType.DATABRICKS, name="DBX")],
            edges=[],
            metadata=_meta(),
        )
        result = ReadinessGenerator().generate(_minimal_product(), no_adls_graph, _RBAC)
        storage = next(f for f in result.files if "test_storage" in f.filename)
        assert "pytestmark = pytest.mark.skip" in storage.content


# ── Ansible fix verification ──────────────────────────────────────────────────

class TestAnsibleFix:
    def test_requirements_uses_correct_collection_namespace(self):
        from dataforge.generation.generators.ansible import AnsibleGenerator
        result = AnsibleGenerator().generate(_full_product(), _graph_no_adf(), _RBAC)
        req = next(f for f in result.files if f.filename == "ansible/requirements.yml")
        assert "databricks.databricks" in req.content
        assert "community.databricks" not in req.content

    def test_playbook_uses_uri_module_not_collection_modules(self):
        from dataforge.generation.generators.ansible import AnsibleGenerator
        result = AnsibleGenerator().generate(_full_product(), _graph_no_adf(), _RBAC)
        pb = next(f for f in result.files if "configure_databricks" in f.filename)
        assert "uri:" in pb.content
        assert "community.databricks" not in pb.content

    def test_playbook_uses_databricks_rest_api(self):
        from dataforge.generation.generators.ansible import AnsibleGenerator
        result = AnsibleGenerator().generate(_full_product(), _graph_no_adf(), _RBAC)
        pb = next(f for f in result.files if "configure_databricks" in f.filename)
        assert "/api/2.0/policies/clusters/create" in pb.content

    def test_playbook_asserts_env_vars_present(self):
        from dataforge.generation.generators.ansible import AnsibleGenerator
        result = AnsibleGenerator().generate(_full_product(), _graph_no_adf(), _RBAC)
        pb = next(f for f in result.files if "configure_databricks" in f.filename)
        assert "DATABRICKS_HOST" in pb.content
        assert "DATABRICKS_TOKEN" in pb.content


# ── Quality jobs TF fix ────────────────────────────────────────────────────────

class TestQualityJobsFix:
    def test_quality_generator_emits_databricks_jobs_tf(self):
        from dataforge.generation.generators.quality import QualityGenerator
        from dataforge.models.flow_graph import FlowGraph, FlowMetadata, FlowNode

        graph = FlowGraph(
            nodes=[FlowNode(id="transform", type=NodeType.DATABRICKS, name="DBX")],
            edges=[],
            metadata=_meta(),
        )
        result = QualityGenerator().generate(_full_product(), graph, _RBAC)
        filenames = {f.filename for f in result.files}
        assert "quality/databricks_jobs.tf" in filenames

    def test_databricks_job_resource_in_tf(self):
        from dataforge.generation.generators.quality import QualityGenerator
        from dataforge.models.flow_graph import FlowGraph, FlowMetadata, FlowNode

        graph = FlowGraph(
            nodes=[FlowNode(id="transform", type=NodeType.DATABRICKS, name="DBX")],
            edges=[],
            metadata=_meta(),
        )
        result = QualityGenerator().generate(_full_product(), graph, _RBAC)
        jobs_tf = next(f for f in result.files if f.filename == "quality/databricks_jobs.tf")
        assert "databricks_job" in jobs_tf.content

    def test_job_has_schedule(self):
        from dataforge.generation.generators.quality import QualityGenerator
        from dataforge.models.flow_graph import FlowGraph, FlowMetadata, FlowNode

        graph = FlowGraph(
            nodes=[FlowNode(id="transform", type=NodeType.DATABRICKS, name="DBX")],
            edges=[],
            metadata=_meta(),
        )
        result = QualityGenerator().generate(_full_product(), graph, _RBAC)
        jobs_tf = next(f for f in result.files if f.filename == "quality/databricks_jobs.tf")
        assert "quartz_cron_expression" in jobs_tf.content

    def test_job_uploads_pyspark_script(self):
        from dataforge.generation.generators.quality import QualityGenerator
        from dataforge.models.flow_graph import FlowGraph, FlowMetadata, FlowNode

        graph = FlowGraph(
            nodes=[FlowNode(id="transform", type=NodeType.DATABRICKS, name="DBX")],
            edges=[],
            metadata=_meta(),
        )
        result = QualityGenerator().generate(_full_product(), graph, _RBAC)
        jobs_tf = next(f for f in result.files if f.filename == "quality/databricks_jobs.tf")
        assert "dbfs:/dataforge/quality/" in jobs_tf.content
