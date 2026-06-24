"""Parse Azure Data Factory ARM exports into DataForge FlowGraph.

Supports two input shapes:
  1. Full ARM template: {"resources": [...]} with type "Microsoft.DataFactory/factories/linkedservices"
  2. Single linked-service JSON: {"type": "AzureDataLakeStorage", ...}
  3. ADF factory export ZIP or folder (not supported — extract JSON first)

ADF linked-service type -> DataForge NodeType mapping is structural (no LLM call).
Edges are inferred from pipeline activities when present.

Usage:
    from dataforge.parsing.adf_importer import AdfImporter, AdfImportError
    graph = AdfImporter().import_from_file(Path("factory-export.json"))
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dataforge.constants import NodeType
from dataforge.models.flow_graph import FlowEdge, FlowGraph, FlowMetadata, FlowNode

# Maps ADF linked-service type strings to DataForge NodeType
_LS_TYPE_MAP: dict[str, NodeType] = {
    "AzureDataLakeStorage": NodeType.ADLS,
    "AzureDataLakeStorageGen2": NodeType.ADLS,
    "AzureBlobStorage": NodeType.BLOB_STORAGE,
    "AzureBlobFS": NodeType.BLOB_STORAGE,
    "AzureDatabricks": NodeType.DATABRICKS,
    "AzureDatabricksLinkedService": NodeType.DATABRICKS,
    "AzureKeyVault": NodeType.KEY_VAULT,
    "AzureSqlMI": NodeType.SQL_MI,
    "AzureSqlDatabase": NodeType.SQL_MI,
    "AzureMySql": NodeType.SQL_MI,
    "AzureEventHub": NodeType.EVENTHUB,
    "AzureDataExplorer": NodeType.EVENTHUB,
    "MicrosoftFabricLakehouse": NodeType.FABRIC_LAKEHOUSE,
    "RestService": None,  # skip REST services
    "HttpServer": None,
    "SftpServer": None,
    "FileServer": None,
}

# ADF activity type -> edge operation
_ACTIVITY_OP_MAP: dict[str, str] = {
    "Copy": "write",
    "DatabricksNotebook": "trigger",
    "DatabricksSparkPython": "trigger",
    "DatabricksSparkJar": "trigger",
    "SqlServerStoredProcedure": "write",
    "Lookup": "read",
    "GetMetadata": "read",
    "Delete": "write",
    "ForEach": "read",
    "Until": "read",
    "Wait": "read",
    "WebActivity": "trigger",
    "ExecutePipeline": "trigger",
    "SetVariable": "read",
    "AzureFunctionActivity": "trigger",
}


class AdfImportError(Exception):
    pass


class AdfImporter:
    """Converts an ADF ARM export JSON into a DataForge FlowGraph."""

    def import_from_file(self, path: Path) -> FlowGraph:
        """Parse an ADF export file and return a FlowGraph."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdfImportError(f"Cannot read '{path}': {exc}") from exc

        return self._parse(raw, source_name=path.stem)

    def import_from_json(self, data: dict | list, *, source_name: str = "adf-import") -> FlowGraph:
        """Parse an already-loaded ADF JSON structure."""
        return self._parse(data, source_name=source_name)

    def _parse(self, raw: dict | list, source_name: str) -> FlowGraph:
        ls_resources: list[dict] = []
        pipeline_resources: list[dict] = []
        dataset_resources: list[dict] = []

        if isinstance(raw, list):
            for item in raw:
                self._classify(item, ls_resources, pipeline_resources, dataset_resources)
        elif isinstance(raw, dict):
            if "resources" in raw:
                for item in raw["resources"]:
                    self._classify(item, ls_resources, pipeline_resources, dataset_resources)
            elif "properties" in raw and "type" in raw.get("properties", {}):
                # Single linked service
                ls_resources.append(raw)
            else:
                raise AdfImportError(
                    "Unrecognised ADF JSON shape. Expected ARM template with 'resources' array "
                    "or a single linked-service object."
                )

        ls_nodes = self._build_nodes(ls_resources, source_name)
        adf_node = FlowNode(
            id=f"adf_{_slug(source_name)}",
            name=source_name,
            type=NodeType.ADF,
        )
        # Include ADF node in the map so activity edges can reference it
        all_node_map = {adf_node.id: adf_node, **ls_nodes}
        edges = self._build_edges(pipeline_resources, dataset_resources, all_node_map)

        all_nodes = list(all_node_map.values())

        metadata = FlowMetadata(
            environment="dev",
            location="eastus",
            resource_group="rg-dataforge",
            application_name=_slug(source_name),
        )

        return FlowGraph(nodes=all_nodes, edges=edges, metadata=metadata)

    def _classify(
        self,
        item: dict,
        ls_out: list[dict],
        pipeline_out: list[dict],
        dataset_out: list[dict],
    ) -> None:
        item_type = item.get("type", "")
        if "linkedservices" in item_type.lower():
            ls_out.append(item)
        elif "pipelines" in item_type.lower():
            pipeline_out.append(item)
        elif "datasets" in item_type.lower():
            dataset_out.append(item)

    def _build_nodes(self, ls_resources: list[dict], source_name: str) -> dict[str, FlowNode]:
        nodes: dict[str, FlowNode] = {}
        for ls in ls_resources:
            props = ls.get("properties", ls)
            ls_type = props.get("type", "")
            node_type = _LS_TYPE_MAP.get(ls_type)
            if node_type is None:
                continue

            name = ls.get("name", ls_type)
            # ADF ARM template: name is "factory/servicename" — take last segment
            if "/" in name:
                name = name.split("/")[-1]

            node_id = f"{node_type.value}_{_slug(name)}"
            if node_id not in nodes:
                nodes[node_id] = FlowNode(id=node_id, name=name, type=node_type)

        return nodes

    def _build_edges(
        self,
        pipelines: list[dict],
        datasets: list[dict],
        nodes: dict[str, FlowNode],
    ) -> list[FlowEdge]:
        # Build dataset -> linked-service name index
        ds_to_ls: dict[str, str] = {}
        for ds in datasets:
            props = ds.get("properties", {})
            ls_ref = props.get("linkedServiceName", {})
            ls_name = ls_ref.get("referenceName", "") if isinstance(ls_ref, dict) else ""
            ds_name = ds.get("name", "")
            if "/" in ds_name:
                ds_name = ds_name.split("/")[-1]
            if ls_name:
                ds_to_ls[ds_name] = ls_name

        # Build linked-service name -> node_id index
        ls_name_to_node: dict[str, str] = {
            n.name: nid for nid, n in nodes.items()
        }

        edges: list[FlowEdge] = []
        seen: set[tuple[str, str, str]] = set()

        for pipeline in pipelines:
            props = pipeline.get("properties", {})
            activities = props.get("activities", [])
            for act in activities:
                self._extract_edges_from_activity(
                    act, nodes, ds_to_ls, ls_name_to_node, edges, seen
                )

        return edges

    def _extract_edges_from_activity(
        self,
        act: dict,
        nodes: dict[str, FlowNode],
        ds_to_ls: dict[str, str],
        ls_name_to_node: dict[str, str],
        edges: list[FlowEdge],
        seen: set[tuple[str, str, str]],
    ) -> None:
        act_type = act.get("type", "")
        operation = _ACTIVITY_OP_MAP.get(act_type, "read")

        ls_ref = act.get("linkedServiceName", {})
        ls_name = ls_ref.get("referenceName", "") if isinstance(ls_ref, dict) else ""

        # Copy activity: source dataset -> sink dataset
        if act_type == "Copy":
            inputs = act.get("inputs", [])
            outputs = act.get("outputs", [])
            src_ds = inputs[0].get("referenceName", "") if inputs else ""
            dst_ds = outputs[0].get("referenceName", "") if outputs else ""
            src_ls = ds_to_ls.get(src_ds, "")
            dst_ls = ds_to_ls.get(dst_ds, "")
            src_node = ls_name_to_node.get(src_ls)
            dst_node = ls_name_to_node.get(dst_ls)
            if src_node and dst_node and src_node != dst_node:
                edge_key = (src_node, dst_node, "write")
                if edge_key not in seen:
                    seen.add(edge_key)
                    edges.append(FlowEdge(source=src_node, target=dst_node, operation="write"))
        elif ls_name and ls_name in ls_name_to_node:
            # Databricks notebook, web activity, etc.
            target = ls_name_to_node[ls_name]
            adf_node_id = next(
                (nid for nid, n in nodes.items() if n.type == NodeType.ADF),
                None,
            )
            if adf_node_id:
                edge_key = (adf_node_id, target, operation)
                if edge_key not in seen:
                    seen.add(edge_key)
                    edges.append(FlowEdge(source=adf_node_id, target=target, operation=operation))


def _slug(name: str) -> str:
    """Convert arbitrary string to a valid Terraform identifier segment."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32]
