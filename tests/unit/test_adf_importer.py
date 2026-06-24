"""Unit tests for ADF JSON importer."""

import json
import pytest
from pathlib import Path

from dataforge.constants import NodeType
from dataforge.parsing.adf_importer import AdfImporter, AdfImportError, _slug


# ── slug helper ───────────────────────────────────────────────────────────────

def test_slug_lowercases_and_replaces_special_chars():
    assert _slug("My-ADLS Gen2!") == "my_adls_gen2"


def test_slug_truncates_at_32_chars():
    result = _slug("a" * 40)
    assert len(result) <= 32


# ── ARM template parsing ──────────────────────────────────────────────────────

MINIMAL_ARM = {
    "resources": [
        {
            "type": "Microsoft.DataFactory/factories/linkedservices",
            "name": "myfactory/ADLSStorage",
            "properties": {
                "type": "AzureDataLakeStorageGen2",
            },
        },
        {
            "type": "Microsoft.DataFactory/factories/linkedservices",
            "name": "myfactory/DatabricksWS",
            "properties": {
                "type": "AzureDatabricks",
            },
        },
    ]
}


def test_import_arm_template_builds_nodes():
    graph = AdfImporter().import_from_json(MINIMAL_ARM, source_name="myfactory")
    types = {n.type for n in graph.nodes}
    assert NodeType.ADF in types
    assert NodeType.ADLS in types
    assert NodeType.DATABRICKS in types


def test_import_arm_template_adf_node_always_present():
    graph = AdfImporter().import_from_json({"resources": []}, source_name="empty")
    assert any(n.type == NodeType.ADF for n in graph.nodes)


def test_import_deduplicates_same_type():
    arm = {
        "resources": [
            {
                "type": "Microsoft.DataFactory/factories/linkedservices",
                "name": "f/ADLS1",
                "properties": {"type": "AzureDataLakeStorage"},
            },
            {
                "type": "Microsoft.DataFactory/factories/linkedservices",
                "name": "f/ADLS2",
                "properties": {"type": "AzureDataLakeStorageGen2"},
            },
        ]
    }
    graph = AdfImporter().import_from_json(arm, source_name="test")
    adls_nodes = [n for n in graph.nodes if n.type == NodeType.ADLS]
    # Each unique name -> unique node even if same type
    assert len(adls_nodes) == 2


def test_import_skips_unsupported_ls_types():
    arm = {
        "resources": [
            {
                "type": "Microsoft.DataFactory/factories/linkedservices",
                "name": "f/RESTapi",
                "properties": {"type": "RestService"},
            },
        ]
    }
    graph = AdfImporter().import_from_json(arm, source_name="test")
    non_adf = [n for n in graph.nodes if n.type != NodeType.ADF]
    assert non_adf == []


# ── Single linked-service shape ───────────────────────────────────────────────

SINGLE_LS = {
    "name": "KeyVaultLS",
    "type": "LinkedService",
    "properties": {
        "type": "AzureKeyVault",
    },
}


def test_import_single_ls_json():
    graph = AdfImporter().import_from_json(SINGLE_LS, source_name="factory")
    assert any(n.type == NodeType.KEY_VAULT for n in graph.nodes)


# ── Copy activity edges ───────────────────────────────────────────────────────

PIPELINE_WITH_COPY = {
    "resources": [
        {
            "type": "Microsoft.DataFactory/factories/linkedservices",
            "name": "f/BlobSrc",
            "properties": {"type": "AzureBlobStorage"},
        },
        {
            "type": "Microsoft.DataFactory/factories/linkedservices",
            "name": "f/ADLSSink",
            "properties": {"type": "AzureDataLakeStorageGen2"},
        },
        {
            "type": "Microsoft.DataFactory/factories/datasets",
            "name": "f/BlobDataset",
            "properties": {
                "type": "AzureBlob",
                "linkedServiceName": {"referenceName": "BlobSrc"},
            },
        },
        {
            "type": "Microsoft.DataFactory/factories/datasets",
            "name": "f/ADLSDataset",
            "properties": {
                "type": "AzureDataLakeStorageGen2File",
                "linkedServiceName": {"referenceName": "ADLSSink"},
            },
        },
        {
            "type": "Microsoft.DataFactory/factories/pipelines",
            "name": "f/CopyPipeline",
            "properties": {
                "activities": [
                    {
                        "name": "CopyData",
                        "type": "Copy",
                        "inputs": [{"referenceName": "BlobDataset", "type": "DatasetReference"}],
                        "outputs": [{"referenceName": "ADLSDataset", "type": "DatasetReference"}],
                    }
                ]
            },
        },
    ]
}


def test_copy_activity_creates_edge():
    graph = AdfImporter().import_from_json(PIPELINE_WITH_COPY, source_name="factory")
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.operation == "write"


def test_copy_activity_edge_direction():
    graph = AdfImporter().import_from_json(PIPELINE_WITH_COPY, source_name="factory")
    edge = graph.edges[0]
    # Source should be blob, target should be ADLS
    src_node = next(n for n in graph.nodes if n.id == edge.source)
    dst_node = next(n for n in graph.nodes if n.id == edge.target)
    assert src_node.type == NodeType.BLOB_STORAGE
    assert dst_node.type == NodeType.ADLS


# ── Databricks notebook activity ──────────────────────────────────────────────

DATABRICKS_PIPELINE = {
    "resources": [
        {
            "type": "Microsoft.DataFactory/factories/linkedservices",
            "name": "f/DatabricksWS",
            "properties": {"type": "AzureDatabricks"},
        },
        {
            "type": "Microsoft.DataFactory/factories/pipelines",
            "name": "f/TriggerNotebook",
            "properties": {
                "activities": [
                    {
                        "name": "RunNotebook",
                        "type": "DatabricksNotebook",
                        "linkedServiceName": {"referenceName": "DatabricksWS"},
                    }
                ]
            },
        },
    ]
}


def test_databricks_notebook_creates_trigger_edge():
    graph = AdfImporter().import_from_json(DATABRICKS_PIPELINE, source_name="factory")
    assert len(graph.edges) >= 1
    trigger_edges = [e for e in graph.edges if e.operation == "trigger"]
    assert len(trigger_edges) == 1


# ── Error handling ────────────────────────────────────────────────────────────

def test_import_from_file_missing_file(tmp_path):
    with pytest.raises(AdfImportError, match="Cannot read"):
        AdfImporter().import_from_file(tmp_path / "nonexistent.json")


def test_import_from_file_invalid_json(tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json at all")
    with pytest.raises(AdfImportError, match="Cannot read"):
        AdfImporter().import_from_file(bad_file)


def test_import_unrecognised_shape():
    with pytest.raises(AdfImportError, match="Unrecognised"):
        AdfImporter().import_from_json({"something": "else"}, source_name="test")


# ── from_file round-trip ──────────────────────────────────────────────────────

def test_import_from_file_valid(tmp_path):
    path = tmp_path / "factory.json"
    path.write_text(json.dumps(MINIMAL_ARM), encoding="utf-8")
    graph = AdfImporter().import_from_file(path)
    assert len(graph.nodes) >= 3  # ADF + ADLS + Databricks


# ── Metadata defaults ─────────────────────────────────────────────────────────

def test_imported_graph_has_metadata():
    graph = AdfImporter().import_from_json(MINIMAL_ARM, source_name="myfactory")
    assert graph.metadata.environment == "dev"
    assert graph.metadata.application_name == "myfactory"
