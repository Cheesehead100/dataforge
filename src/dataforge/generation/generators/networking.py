"""NetworkingGenerator — private endpoints, DNS zones, and deployment sequencing (L10 / Pain Point 2).

Supplements the existing networking.tf.j2 (VNet/NSG/subnets) with the private-endpoint
layer that prevents the 'storage firewall blocks container creation' failure in secure
Azure environments.

Only activates when `networking.private_endpoints: true` is set in the Data Product YAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dataforge.constants import NodeType
from dataforge.generation.generators.base import BaseGenerator
from dataforge.generation.renderer import Renderer
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph, FlowNode
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

_RENDERER = Renderer()
_SAFE_RE = re.compile(r"[^a-z0-9]")


def _safe(value: str) -> str:
    return _SAFE_RE.sub("_", value.lower()).strip("_")


# ── Private-endpoint catalogue ──────────────────────────────────────────────
# Describes how each NodeType maps to Azure private-endpoint resources.

@dataclass(frozen=True)
class _PeSpec:
    safe_name: str          # TF resource suffix, e.g. "adls_bronze_blob"
    display_name: str       # Human-readable, for comments
    sub_resource: str       # Azure PE sub-resource name
    resource_id_ref: str    # HCL expression for the target resource ID
    dns_zone_refs: list[str] = field(default_factory=list)  # DNS zone safe_names


@dataclass(frozen=True)
class _DnsZoneSpec:
    safe_name: str          # TF resource name, e.g. "blob_core_windows_net"
    zone_name: str          # Azure DNS zone name, e.g. "privatelink.blob.core.windows.net"


# Maps NodeType → function that builds the PE specs for a given node
_PE_BUILDERS: dict[NodeType, callable] = {}

# Keys equal the TF resource safe_name so svc.primary_dns_zone_ref can be used
# directly as both the lookup key and the azurerm_private_dns_zone.* resource name.
_DNS_ZONE_CATALOGUE: dict[str, _DnsZoneSpec] = {
    "blob_core":   _DnsZoneSpec("blob_core",   "privatelink.blob.core.windows.net"),
    "dfs_core":    _DnsZoneSpec("dfs_core",    "privatelink.dfs.core.windows.net"),
    "vault_core":  _DnsZoneSpec("vault_core",  "privatelink.vaultcore.azure.net"),
    "databricks":  _DnsZoneSpec("databricks",  "privatelink.azuredatabricks.net"),
    "datafactory": _DnsZoneSpec("datafactory", "privatelink.datafactory.azure.net"),
    "sqlmi":       _DnsZoneSpec("sqlmi",        "privatelink.database.windows.net"),
    "servicebus":  _DnsZoneSpec("servicebus",   "privatelink.servicebus.windows.net"),
}


def _pe_for_adls(node: FlowNode) -> list[_PeSpec]:
    safe_id = _safe(node.id)
    return [
        _PeSpec(
            safe_name=f"adls_{safe_id}_blob",
            display_name=f"ADLS blob ({node.id})",
            sub_resource="blob",
            resource_id_ref=f"azurerm_storage_account.{node.id}.id",
            dns_zone_refs=["blob_core"],
        ),
        _PeSpec(
            safe_name=f"adls_{safe_id}_dfs",
            display_name=f"ADLS DFS / hierarchical namespace ({node.id})",
            sub_resource="dfs",
            resource_id_ref=f"azurerm_storage_account.{node.id}.id",
            dns_zone_refs=["dfs_core"],
        ),
    ]


def _pe_for_key_vault(_node: FlowNode) -> list[_PeSpec]:
    return [_PeSpec(
        safe_name="key_vault",
        display_name="Key Vault",
        sub_resource="vault",
        resource_id_ref="azurerm_key_vault.main.id",
        dns_zone_refs=["vault_core"],
    )]


def _pe_for_databricks(_node: FlowNode) -> list[_PeSpec]:
    return [_PeSpec(
        safe_name="databricks",
        display_name="Databricks workspace (UI + API)",
        sub_resource="databricks_ui_api",
        resource_id_ref="azurerm_databricks_workspace.main.id",
        dns_zone_refs=["databricks"],
    )]


def _pe_for_adf(_node: FlowNode) -> list[_PeSpec]:
    return [_PeSpec(
        safe_name="adf",
        display_name="Azure Data Factory",
        sub_resource="dataFactory",
        resource_id_ref="azurerm_data_factory.main.id",
        dns_zone_refs=["datafactory"],
    )]


def _pe_for_sql_mi(_node: FlowNode) -> list[_PeSpec]:
    return [_PeSpec(
        safe_name="sql_mi",
        display_name="SQL Managed Instance",
        sub_resource="managedInstance",
        resource_id_ref="azurerm_mssql_managed_instance.main.id",
        dns_zone_refs=["sqlmi"],
    )]


def _pe_for_eventhub(_node: FlowNode) -> list[_PeSpec]:
    return [_PeSpec(
        safe_name="eventhub",
        display_name="Event Hub namespace",
        sub_resource="namespace",
        resource_id_ref="azurerm_eventhub_namespace.main.id",
        dns_zone_refs=["servicebus"],
    )]


_PE_FACTORY: dict[NodeType, callable] = {
    NodeType.ADLS:        _pe_for_adls,
    NodeType.KEY_VAULT:   _pe_for_key_vault,
    NodeType.DATABRICKS:  _pe_for_databricks,
    NodeType.ADF:         _pe_for_adf,
    NodeType.SQL_MI:      _pe_for_sql_mi,
    NodeType.EVENTHUB:    _pe_for_eventhub,
}

# NodeTypes that have already been mapped (dedup guard for non-ADLS singletons)
_SINGLETON_TYPES = {NodeType.KEY_VAULT, NodeType.DATABRICKS, NodeType.ADF,
                    NodeType.SQL_MI, NodeType.EVENTHUB}


def _build_pe_specs(graph: FlowGraph) -> tuple[list[_PeSpec], list[_DnsZoneSpec]]:
    """Return (pe_specs, dns_zone_specs) for all PE-capable services in the graph."""
    pe_specs: list[_PeSpec] = []
    seen_singletons: set[NodeType] = set()
    dns_zone_keys_needed: set[str] = set()

    for node in graph.nodes:
        factory = _PE_FACTORY.get(node.type)
        if factory is None:
            continue
        if node.type in _SINGLETON_TYPES:
            if node.type in seen_singletons:
                continue
            seen_singletons.add(node.type)
        specs = factory(node)
        pe_specs.extend(specs)
        for spec in specs:
            dns_zone_keys_needed.update(spec.dns_zone_refs)

    dns_zones = [_DNS_ZONE_CATALOGUE[k] for k in dns_zone_keys_needed
                 if k in _DNS_ZONE_CATALOGUE]
    dns_zones.sort(key=lambda z: z.safe_name)

    return pe_specs, dns_zones


def _private_endpoints_enabled(product: DataProduct) -> bool:
    if product.networking is None:
        return False
    raw = product.networking.model_dump()
    return bool(raw.get("private_endpoints", False))


class NetworkingGenerator(BaseGenerator):

    def applicable(self, product: DataProduct) -> bool:
        return _private_endpoints_enabled(product)

    def generate(self, product: DataProduct, graph: FlowGraph, rbac: RbacResult) -> GenerationResult:
        pe_specs, dns_zones = _build_pe_specs(graph)

        if not pe_specs:
            return GenerationResult(files=[], warnings=[
                "networking.private_endpoints=true but no PE-capable services found in graph"
            ])

        adls_nodes = graph.nodes_of_type(NodeType.ADLS)
        adls_pe_safe_names = [
            f"adls_{_safe(n.id)}_blob" for n in adls_nodes
        ] + [
            f"adls_{_safe(n.id)}_dfs" for n in adls_nodes
        ]

        ctx = {
            "product_name": product.name,
            "app": product.name.replace("_", "-"),
            "env": graph.metadata.environment,
            "metadata": graph.metadata,
            "pe_specs": [
                {
                    "safe_name": s.safe_name,
                    "display_name": s.display_name,
                    "sub_resource": s.sub_resource,
                    "resource_id_ref": s.resource_id_ref,
                    "primary_dns_zone_ref": s.dns_zone_refs[0] if s.dns_zone_refs else "",
                }
                for s in pe_specs
            ],
            "dns_zones": [
                {"safe_name": z.safe_name, "zone_name": z.zone_name}
                for z in dns_zones
            ],
            "adls_nodes": [{"id": n.id, "safe_id": _safe(n.id)} for n in adls_nodes],
            "adls_pe_safe_names": adls_pe_safe_names,
            "has_adls": bool(adls_nodes),
            "has_databricks": bool(graph.nodes_of_type(NodeType.DATABRICKS)),
            "has_key_vault": bool(graph.nodes_of_type(NodeType.KEY_VAULT)),
        }

        return GenerationResult(files=[
            TerraformFile(
                filename="network/private_endpoints.tf",
                content=_RENDERER.render("network/private_endpoints.tf.j2", ctx),
            ),
            TerraformFile(
                filename="network/dns.tf",
                content=_RENDERER.render("network/dns.tf.j2", ctx),
            ),
            TerraformFile(
                filename="network/sequencing.sh",
                content=_RENDERER.render("network/sequencing.sh.j2", ctx),
            ),
        ])
