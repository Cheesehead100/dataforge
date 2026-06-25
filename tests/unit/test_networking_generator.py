"""Unit tests for NetworkingGenerator — private endpoints, DNS zones, sequencing."""

from __future__ import annotations

import pytest

from dataforge.constants import NodeType, OperationType
from dataforge.generation.generators.networking import NetworkingGenerator, _build_pe_specs, _private_endpoints_enabled
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


def _full_graph() -> FlowGraph:
    """ADLS + Databricks + KeyVault + ADF + SQL MI + EventHub."""
    return FlowGraph(
        nodes=[
            FlowNode(id="bronze",   type=NodeType.ADLS,       name="Bronze ADLS"),
            FlowNode(id="dbx",      type=NodeType.DATABRICKS,  name="Databricks"),
            FlowNode(id="kv",       type=NodeType.KEY_VAULT,   name="Key Vault"),
            FlowNode(id="adf",      type=NodeType.ADF,         name="ADF"),
            FlowNode(id="sql",      type=NodeType.SQL_MI,      name="SQL MI"),
            FlowNode(id="eh",       type=NodeType.EVENTHUB,    name="EventHub"),
        ],
        edges=[
            FlowEdge(source="sql",    target="adf",    operation=OperationType.READ),
            FlowEdge(source="adf",    target="bronze", operation=OperationType.WRITE),
            FlowEdge(source="bronze", target="dbx",    operation=OperationType.READ),
            FlowEdge(source="dbx",    target="kv",     operation=OperationType.SECRET_GET),
        ],
        metadata=_meta(),
    )


def _adls_only_graph() -> FlowGraph:
    return FlowGraph(
        nodes=[FlowNode(id="lake", type=NodeType.ADLS, name="Data Lake")],
        edges=[],
        metadata=_meta(),
    )


def _dbx_only_graph() -> FlowGraph:
    return FlowGraph(
        nodes=[FlowNode(id="workspace", type=NodeType.DATABRICKS, name="DBX")],
        edges=[],
        metadata=_meta(),
    )


def _product_with_pe(extra_yaml: str = "") -> DataProduct:
    yaml = f"""
product: secure-platform
environment: dev
source:
  type: sqlserver
target:
  type: adls
sla: daily
networking:
  private_endpoints: true
  vnet_cidr: 10.20.0.0/16
cicd:
  provider: github_actions
{extra_yaml}
"""
    return YamlParser().parse_string(yaml)


def _product_no_pe() -> DataProduct:
    yaml = """
product: open-platform
environment: dev
source:
  type: blob_storage
target:
  type: adls
sla: daily
"""
    return YamlParser().parse_string(yaml)


def _product_pe_false() -> DataProduct:
    yaml = """
product: open-platform
environment: dev
source:
  type: blob_storage
target:
  type: adls
sla: daily
networking:
  private_endpoints: false
  vnet_cidr: 10.0.0.0/16
"""
    return YamlParser().parse_string(yaml)


# ── applicable() ─────────────────────────────────────────────────────────────

class TestApplicable:

    def test_applicable_when_pe_true(self):
        gen = NetworkingGenerator()
        assert gen.applicable(_product_with_pe()) is True

    def test_not_applicable_when_no_networking_section(self):
        gen = NetworkingGenerator()
        assert gen.applicable(_product_no_pe()) is False

    def test_not_applicable_when_pe_false(self):
        gen = NetworkingGenerator()
        assert gen.applicable(_product_pe_false()) is False

    def test_private_endpoints_enabled_helper_true(self):
        assert _private_endpoints_enabled(_product_with_pe()) is True

    def test_private_endpoints_enabled_helper_false(self):
        assert _private_endpoints_enabled(_product_no_pe()) is False


# ── PE spec building ──────────────────────────────────────────────────────────

class TestPeSpecBuilding:

    def test_adls_produces_blob_and_dfs_specs(self):
        pe_specs, _ = _build_pe_specs(_adls_only_graph())
        names = [s.safe_name for s in pe_specs]
        assert "adls_lake_blob" in names
        assert "adls_lake_dfs" in names

    def test_adls_subresources_correct(self):
        pe_specs, _ = _build_pe_specs(_adls_only_graph())
        sub_resources = {s.sub_resource for s in pe_specs}
        assert sub_resources == {"blob", "dfs"}

    def test_adls_resource_id_ref_uses_node_id(self):
        pe_specs, _ = _build_pe_specs(_adls_only_graph())
        for spec in pe_specs:
            assert "azurerm_storage_account.lake" in spec.resource_id_ref

    def test_databricks_produces_single_pe(self):
        pe_specs, _ = _build_pe_specs(_dbx_only_graph())
        assert len(pe_specs) == 1
        assert pe_specs[0].sub_resource == "databricks_ui_api"

    def test_databricks_deduplicates_multiple_nodes(self):
        graph = FlowGraph(
            nodes=[
                FlowNode(id="ws1", type=NodeType.DATABRICKS, name="WS1"),
                FlowNode(id="ws2", type=NodeType.DATABRICKS, name="WS2"),
            ],
            edges=[],
            metadata=_meta(),
        )
        pe_specs, _ = _build_pe_specs(graph)
        dbx_specs = [s for s in pe_specs if s.sub_resource == "databricks_ui_api"]
        assert len(dbx_specs) == 1

    def test_full_graph_produces_correct_count(self):
        # ADLS: 2 (blob + dfs), DBX: 1, KV: 1, ADF: 1, SQL_MI: 1, EH: 1 = 7
        pe_specs, _ = _build_pe_specs(_full_graph())
        assert len(pe_specs) == 7

    def test_dns_zones_deduplicated(self):
        pe_specs, dns_zones = _build_pe_specs(_full_graph())
        zone_names = [z.zone_name for z in dns_zones]
        assert len(zone_names) == len(set(zone_names))

    def test_adls_dns_zones_include_blob_and_dfs(self):
        _, dns_zones = _build_pe_specs(_adls_only_graph())
        zone_names = [z.zone_name for z in dns_zones]
        assert "privatelink.blob.core.windows.net" in zone_names
        assert "privatelink.dfs.core.windows.net" in zone_names

    def test_full_graph_dns_zones_all_present(self):
        _, dns_zones = _build_pe_specs(_full_graph())
        zone_names = {z.zone_name for z in dns_zones}
        expected = {
            "privatelink.blob.core.windows.net",
            "privatelink.dfs.core.windows.net",
            "privatelink.vaultcore.azure.net",
            "privatelink.azuredatabricks.net",
            "privatelink.datafactory.azure.net",
            "privatelink.database.windows.net",
            "privatelink.servicebus.windows.net",
        }
        assert zone_names == expected

    def test_non_pe_graph_produces_no_specs(self):
        # FABRIC_LAKEHOUSE has no private endpoint in the catalogue
        graph = FlowGraph(
            nodes=[FlowNode(id="fabric", type=NodeType.FABRIC_LAKEHOUSE, name="Fabric")],
            edges=[],
            metadata=_meta(),
        )
        pe_specs, dns_zones = _build_pe_specs(graph)
        assert pe_specs == []
        assert dns_zones == []


# ── Output files ─────────────────────────────────────────────────────────────

class TestNetworkingGeneratorOutput:

    def test_generates_three_files(self):
        gen = NetworkingGenerator()
        result = gen.generate(_product_with_pe(), _full_graph(), _RBAC)
        assert len(result.files) == 3

    def test_filenames_correct(self):
        gen = NetworkingGenerator()
        result = gen.generate(_product_with_pe(), _full_graph(), _RBAC)
        names = {f.filename for f in result.files}
        assert names == {
            "network/private_endpoints.tf",
            "network/dns.tf",
            "network/sequencing.sh",
        }

    def test_non_pe_graph_returns_warning_not_files(self):
        gen = NetworkingGenerator()
        graph = FlowGraph(
            nodes=[FlowNode(id="fabric", type=NodeType.FABRIC_LAKEHOUSE, name="Fabric")],
            edges=[],
            metadata=_meta(),
        )
        result = gen.generate(_product_with_pe(), graph, _RBAC)
        assert result.files == []
        assert result.warnings

    def test_not_applicable_skips_generation(self):
        gen = NetworkingGenerator()
        assert gen.applicable(_product_no_pe()) is False


# ── private_endpoints.tf content ─────────────────────────────────────────────

class TestPrivateEndpointsTf:

    def _pe_tf(self, graph=None):
        gen = NetworkingGenerator()
        g = graph or _full_graph()
        result = gen.generate(_product_with_pe(), g, _RBAC)
        return next(f for f in result.files if "private_endpoints.tf" in f.filename)

    def test_has_azurerm_private_endpoint_resource(self):
        assert "azurerm_private_endpoint" in self._pe_tf().content

    def test_has_private_service_connection_block(self):
        assert "private_service_connection" in self._pe_tf().content

    def test_has_private_dns_zone_group_block(self):
        assert "private_dns_zone_group" in self._pe_tf().content

    def test_adls_blob_endpoint_present(self):
        content = self._pe_tf().content
        assert "adls_lake_blob" in content or "adls_bronze_blob" in content

    def test_adls_dfs_endpoint_present(self):
        content = self._pe_tf().content
        assert "adls_lake_dfs" in content or "adls_bronze_dfs" in content

    def test_databricks_endpoint_present(self):
        assert "databricks_ui_api" in self._pe_tf().content

    def test_key_vault_endpoint_present(self):
        assert "key_vault" in self._pe_tf().content
        assert '"vault"' in self._pe_tf().content

    def test_adf_endpoint_present(self):
        assert "dataFactory" in self._pe_tf().content

    def test_sql_mi_endpoint_present(self):
        assert "managedInstance" in self._pe_tf().content

    def test_eventhub_endpoint_present(self):
        assert "namespace" in self._pe_tf().content

    def test_depends_on_dns_zone_link(self):
        assert "azurerm_private_dns_zone_virtual_network_link" in self._pe_tf().content

    def test_storage_firewall_rule_generated_for_adls(self):
        tf = self._pe_tf(_adls_only_graph())
        assert "azurerm_storage_account_network_rules" in tf.content

    def test_storage_firewall_default_deny(self):
        tf = self._pe_tf(_adls_only_graph())
        assert '"Deny"' in tf.content

    def test_storage_firewall_depends_on_pe(self):
        tf = self._pe_tf(_adls_only_graph())
        assert "depends_on" in tf.content
        assert "azurerm_private_endpoint" in tf.content

    def test_storage_firewall_bypass_azure_services(self):
        tf = self._pe_tf(_adls_only_graph())
        assert "AzureServices" in tf.content

    def test_no_storage_firewall_without_adls(self):
        tf = self._pe_tf(_dbx_only_graph())
        assert "azurerm_storage_account_network_rules" not in tf.content

    def test_subnet_id_uses_private_endpoints_subnet(self):
        assert "azurerm_subnet.private_endpoints.id" in self._pe_tf().content


# ── dns.tf content ────────────────────────────────────────────────────────────

class TestDnsTf:

    def _dns_tf(self, graph=None):
        gen = NetworkingGenerator()
        g = graph or _full_graph()
        result = gen.generate(_product_with_pe(), g, _RBAC)
        return next(f for f in result.files if "dns.tf" in f.filename)

    def test_has_azurerm_private_dns_zone(self):
        assert "azurerm_private_dns_zone" in self._dns_tf().content

    def test_has_vnet_link_resource(self):
        assert "azurerm_private_dns_zone_virtual_network_link" in self._dns_tf().content

    def test_vnet_link_references_vnet(self):
        assert "azurerm_virtual_network.dataforge.id" in self._dns_tf().content

    def test_blob_zone_present(self):
        assert "privatelink.blob.core.windows.net" in self._dns_tf().content

    def test_dfs_zone_present(self):
        assert "privatelink.dfs.core.windows.net" in self._dns_tf().content

    def test_vault_zone_present_in_full_graph(self):
        assert "privatelink.vaultcore.azure.net" in self._dns_tf().content

    def test_registration_disabled(self):
        assert "registration_enabled  = false" in self._dns_tf().content

    def test_adls_only_has_two_zones(self):
        content = self._dns_tf(_adls_only_graph()).content
        assert "blob.core.windows.net" in content
        assert "dfs.core.windows.net" in content
        assert "vaultcore" not in content
        assert "databricks" not in content


# ── sequencing.sh content ─────────────────────────────────────────────────────

class TestSequencingSh:

    def _seq_sh(self, graph=None):
        gen = NetworkingGenerator()
        g = graph or _full_graph()
        result = gen.generate(_product_with_pe(), g, _RBAC)
        return next(f for f in result.files if "sequencing.sh" in f.filename)

    def test_has_shebang(self):
        assert self._seq_sh().content.startswith("#!/usr/bin/env bash")

    def test_has_six_stages(self):
        content = self._seq_sh().content
        for i in range(1, 7):
            assert f"Stage {i}" in content

    def test_stage_1_targets_vnet(self):
        assert "azurerm_virtual_network.dataforge" in self._seq_sh().content

    def test_stage_2_targets_dns_zones(self):
        content = self._seq_sh().content
        assert "azurerm_private_dns_zone" in content

    def test_stage_5_targets_private_endpoints(self):
        content = self._seq_sh().content
        assert "azurerm_private_endpoint" in content

    def test_stage_5_targets_storage_firewall_for_adls(self):
        content = self._seq_sh(_adls_only_graph()).content
        assert "azurerm_storage_account_network_rules" in content

    def test_set_euo_pipefail(self):
        assert "set -euo pipefail" in self._seq_sh().content

    def test_references_environment(self):
        assert "dev" in self._seq_sh().content

    def test_explains_pain_point(self):
        assert "Pain Point 2" in self._seq_sh().content or "Private Endpoint Hell" in self._seq_sh().content


# ── DataProductGenerator integration ─────────────────────────────────────────

class TestNetworkingInOrchestrator:

    def test_orchestrator_includes_networking_when_pe_enabled(self):
        gen = DataProductGenerator()
        result = gen.generate(_product_with_pe(), _full_graph(), _RBAC)
        net_files = [f.filename for f in result.files if "network/" in f.filename]
        assert net_files, "No network/ files generated"

    def test_orchestrator_excludes_networking_when_no_pe(self):
        gen = DataProductGenerator()
        graph = FlowGraph(
            nodes=[FlowNode(id="adls", type=NodeType.ADLS, name="ADLS")],
            edges=[],
            metadata=_meta(),
        )
        result = gen.generate(_product_no_pe(), graph, _RBAC)
        net_files = [f.filename for f in result.files if "network/" in f.filename]
        assert net_files == [], f"Unexpected network files: {net_files}"

    def test_no_duplicate_filenames_with_networking(self):
        gen = DataProductGenerator()
        result = gen.generate(_product_with_pe(), _full_graph(), _RBAC)
        filenames = [f.filename for f in result.files]
        assert len(filenames) == len(set(filenames))
